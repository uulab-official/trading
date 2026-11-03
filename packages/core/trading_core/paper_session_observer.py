"""Read-only, bounded observation of a durable local paper account.

Never opens a writer store or restores its manager. Stored marks/control are not
quote receipts, a heartbeat, broker evidence, or permission to place an order.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import os
import re
from pathlib import Path
import sqlite3
from threading import Lock
import time

from trading_core.order_manager import TERMINAL, _identifier
from trading_core.order_state_store import MAX_BYTES, _canonical, _digest, _identity
from trading_core.paper_control import control_status
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_core.paper_session import SCHEMA, _bundle

MAX_FILE_BYTES = 64 * 1024 * 1024
DETAIL_LIMIT = 100


class PaperSessionUnavailable(ValueError):
    """No trustworthy current read; callers must not substitute zero balances."""


class PaperSessionChanged(PaperSessionUnavailable):
    """The requested revision is no longer current; restart from page one."""


class PaperOrderNotFound(LookupError):
    """No such order in this valid, matching snapshot; never infer a rejection."""


class PaperOrderPageInvalid(ValueError):
    """Invalid bounded page request, never a signal to substitute empty data."""


ORDER_FILTERS = frozenset(('all', 'unresolved', 'submitting', 'unknown', 'filled'))


def _page_options(offset, limit, status, revision, digest):
    if (type(offset) is not int or not 0 <= offset <= 10000
            or type(limit) is not int or not 1 <= limit <= DETAIL_LIMIT
            or not isinstance(status, str) or status not in ORDER_FILTERS
            or (revision is None) != (digest is None)
            or (offset > 0 and revision is None)):
        raise PaperOrderPageInvalid('invalid_order_page')
    if revision is not None:
        if (not isinstance(revision, str) or not re.fullmatch(r'0|[1-9][0-9]{0,18}', revision)
                or int(revision) >= 2**62 or not isinstance(digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise PaperOrderPageInvalid('invalid_order_page')


def _ordered_orders(orders):
    ordered = sorted(orders.values(), key=lambda o: (o.updated_at, o.client_order_id), reverse=True)
    ordered.sort(key=lambda o: o.status in TERMINAL)
    return ordered


def _order_items(ordered, state):
    return [dict(**o.to_dict(), account_fill_present=o.client_order_id in state['orders'])
            for o in ordered]


def _order_page(data, orders, book, version, revision, digest, *, offset, limit, status):
    selected = [o for o in _ordered_orders(orders) if status == 'all'
                or (status == 'unresolved' and o.status not in TERMINAL)
                or o.status.value == status]
    if offset > 0 and offset >= len(selected):
        raise PaperOrderPageInvalid('invalid_order_page')
    return dict(schema_version=1, source='durable_paper_order_page', mode='paper',
        read_only=True, live_enabled=False, account_id=data['account_id'],
        session_version=version, revision=str(revision), digest=digest,
        observed_at=datetime.now(timezone.utc).isoformat(),
        orders=dict(total=len(orders), matched=len(selected), status=status,
            offset=offset, limit=limit, has_previous=offset > 0,
            has_next=offset + limit < len(selected),
            items=_order_items(selected[offset:offset + limit], data['book'])))


def _order_detail(data, orders, book, version, revision, digest, *, client_order_id):
    if client_order_id not in orders:
        return None  # Finish source-identity checks before reporting absence.
    order = orders[client_order_id]
    saved = data['book']['orders'].get(client_order_id)
    fill = None
    if saved is not None:
        # Match the original model's multiplication context, not caller precision.
        # This is modeled notional, NOT a realized P&L or a balance delta.
        with localcontext(PAPER_CALCULATION_CONTEXT):
            notional = Decimal(saved['filled']) * Decimal(saved['average'])
        fill = dict(venue_order_id=saved['id'], quantity=saved['filled'],
                    average_price=saved['average'], notional=str(notional),
                    fee_amount=saved['fee']['cost'], fee_currency=saved['fee']['currency'],
                    timestamp_ms=saved['timestamp'])
    confirmed = order.status.value == 'filled'
    classification = ('confirmed_fill' if confirmed else
                      'unconfirmed_account_fill' if fill is not None else
                      'unresolved_without_account_fill')
    return dict(schema_version=1, source='durable_paper_order_detail', mode='paper',
        read_only=True, live_enabled=False, account_id=data['account_id'],
        session_version=version, revision=str(revision), digest=digest,
        observed_at=datetime.now(timezone.utc).isoformat(),
        quote_currency=book.quote_currency,
        order=_order_items([order], data['book'])[0], fill=fill,
        evidence=dict(classification=classification, cause='not_recorded',
                      requires_reconciliation=not confirmed, may_resubmit=False))


def _identity_and_header(path):
    identity = _identity(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (identity != (info.st_dev, info.st_ino)
                or not 100 <= info.st_size <= MAX_FILE_BYTES):
            raise ValueError('invalid_session_file')
        header = os.read(fd, 100)
        # Reject WAL before sqlite opens it: read-only WAL can require sidecars.
        if header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
            raise ValueError('unsupported_session_journal')
    finally:
        os.close(fd)
    return identity


def _read_row(path, identity):
    # No immutable=1 or nolock: a cooperating owner may be committing new state.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&cache=private',
                                 uri=True, isolation_level=None, timeout=0.1)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('BEGIN')
        if _identity(path) != identity:
            raise ValueError('session_file_changed')
        objects = db.execute('SELECT name,type,sql FROM sqlite_master').fetchall()
        if objects != [('paper_session', 'table', SCHEMA)]:
            raise ValueError('unexpected_session_schema')
        sizes = db.execute('SELECT length(CAST(payload AS BLOB)) FROM paper_session LIMIT 2').fetchall()
        if len(sizes) != 1 or type(sizes[0][0]) is not int or not 0 < sizes[0][0] <= MAX_BYTES:
            raise ValueError('invalid_session_size')
        rows = db.execute('SELECT singleton,version,scope,revision,payload,digest FROM paper_session LIMIT 2').fetchall()
        if len(rows) != 1:
            raise ValueError('invalid_session_rows')
        db.execute('ROLLBACK')  # Release the short read before expensive validation.
    if _identity(path) != identity:
        raise ValueError('session_file_changed')
    singleton, version, scope, revision, raw, digest = rows[0]
    if (type(singleton) is not int or singleton != 1
            or type(version) is not int or version not in (1, 2, 3)
            or scope != 'paper-session-v1' or type(revision) is not int
            or not 0 <= revision < 2**62 or not isinstance(raw, str)
            or digest != _digest(raw)):
        raise ValueError('invalid_session_row')
    return version, revision, raw, digest


def _project(data, orders, book, version, revision, digest):
    state = data['book']
    quote = book.quote_currency
    cash = Decimal(state['balances'][quote])
    holdings = []
    missing = []
    with localcontext(PAPER_CALCULATION_CONTEXT):
        for asset, amount in sorted(state['balances'].items()):
            quantity = Decimal(amount)
            if asset == quote or quantity == 0:
                continue
            symbol = asset + '/' + quote
            ticker = state['tickers'].get(symbol)
            mark = None if ticker is None else ticker['last']
            if mark is None:
                missing.append(symbol)
            holdings.append(dict(asset=asset, quantity=str(quantity), mark=mark,
                mark_timestamp_ms=None if ticker is None else ticker['timestamp'],
                marked_value=None if mark is None else str(quantity * Decimal(mark))))
        equity = None if missing else book.get_total_value()
        value = None if equity is None else equity - cash
    unresolved = [o for o in orders.values() if o.status not in TERMINAL]
    # Keep every unresolved count, even when the bounded detail list is truncated.
    items = _order_items(_ordered_orders(orders)[:DETAIL_LIMIT], state)
    policy = data.get('policy')
    return dict(schema_version=1, source='durable_paper_session', mode='paper',
        read_only=True, live_enabled=False, account_id=data['account_id'],
        session_version=version, revision=str(revision), digest=digest,
        observed_at=datetime.now(timezone.utc).isoformat(),
        control=control_status(data.get('control')),
        policy=dict(enabled=policy is not None,
            max_quote_age_ms=None if policy is None else policy['max_quote_age_ms'],
            max_order_notional=None if policy is None else policy['max_order_notional']),
        observation=dict(process_state='unobserved', order_authorization='not_assessed',
            quote_receipts='unavailable_to_observer', valuation_basis='saved_marks',
            missing_marks=missing[:DETAIL_LIMIT], missing_marks_count=len(missing)),
        account=dict(quote_currency=quote, initial_cash=str(data['initial']['initial_balance']),
            cash=str(cash), total_fees=str(book.total_fees),
            marked_equity=None if equity is None else str(equity),
            position_value=None if value is None else str(value),
            holdings=holdings[:DETAIL_LIMIT], holdings_count=len(holdings),
            holdings_truncated=len(holdings) > DETAIL_LIMIT),
        orders=dict(total=len(orders), unresolved=len(unresolved),
            confirmed_filled=sum(o.status.value == 'filled' for o in orders.values()),
            account_fills=len(state['trades']),
            unconfirmed_account_fills=sum(o.client_order_id in state['orders'] for o in unresolved),
            items=items, truncated=len(orders) > DETAIL_LIMIT))


class PaperSessionObserver:
    """One pinned source; nonblocking same-observer read exclusion.

    No file handles remain open between reads. The first successful read pins
    inode/account/configuration; later reads reject replacement or rollback.
    Transient contention is an error, not a stale successful response. This is
    not a defense against malicious same-user writers or tamper-proof storage.
    """
    def __init__(self, path):
        self._path = None if path is None else Path(path).absolute()
        self._gate = Lock()
        self._binding = None
        self._previous = None

    def read(self):
        return self._observe(_project)

    def read_orders(self, *, offset=0, limit=50, status='all',
                    expected_revision=None, expected_digest=None):
        """Bounded pages of one current revision; never silently mix snapshots.

        A continuation requires both tokens from the first page. The observer
        retains no historical snapshot: any account commit requires a restart.
        Validation happens before source I/O; no order is normalized or changed.
        """
        _page_options(offset, limit, status, expected_revision, expected_digest)
        def project(*args):
            return _order_page(*args, offset=offset, limit=limit, status=status)
        return self._observe(project, expected_revision, expected_digest)

    def read_order(self, client_order_id, *, expected_revision=None, expected_digest=None):
        """Inspect one exact ID from a displayed snapshot, never the latest silently.

        Both page anchors are mandatory even for the first detail request. The
        full saved account is validated before absence or fill evidence is shown.
        No writer ownership, normalization, external lookup or replay occurs.
        """
        try:
            key = _identifier(client_order_id, 'client_order_id')
            if expected_revision is None or expected_digest is None:
                raise ValueError('missing_detail_precondition')
            _page_options(0, 1, 'all', expected_revision, expected_digest)
        except ValueError:
            raise PaperOrderPageInvalid('invalid_order_detail') from None
        def project(*args):
            return _order_detail(*args, client_order_id=key)
        report = self._observe(project, expected_revision, expected_digest)
        if report is None:
            raise PaperOrderNotFound('paper_order_not_found')
        return report

    def _observe(self, project, expected_revision=None, expected_digest=None):
        if not self._gate.acquire(blocking=False):
            raise PaperSessionUnavailable('paper_session_unavailable')
        try:
            if self._path is None:
                raise ValueError('no_source')
            identity = _identity_and_header(self._path)
            if self._binding is not None and self._binding[0] != identity:
                raise ValueError('different_file')
            version, revision, raw, digest = _read_row(self._path, identity)
            data, orders, book = _bundle(raw)  # Existing account/intent arithmetic validation.
            if version != (3 if 'control' in data else 2 if 'policy' in data else 1):
                raise ValueError('session_version_mismatch')
            binding = (identity, data['account_id'], version,
                       _canonical(data['initial']), _canonical(data.get('policy')))
            generation = data.get('control', {}).get('generation', 0)
            if self._binding is not None and binding != self._binding:
                raise ValueError('different_account_or_policy')
            if self._previous is not None:
                old_revision, old_digest, old_generation = self._previous
                if (revision < old_revision or generation < old_generation
                        or (revision == old_revision and digest != old_digest)):
                    raise ValueError('session_history_regressed')
            if (expected_revision is not None
                    and (str(revision) != expected_revision or digest != expected_digest)):
                raise PaperSessionChanged('paper_session_changed')
            report = project(data, orders, book, version, revision, digest)
            if _identity(self._path) != identity:
                raise ValueError('session_file_changed')
            self._binding = binding
            self._previous = revision, digest, generation
            return report
        except (PaperSessionChanged, PaperOrderPageInvalid):
            raise
        except Exception:
            raise PaperSessionUnavailable('paper_session_unavailable') from None
        finally:
            self._gate.release()
