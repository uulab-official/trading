"""Opt-in, local-only paper account and order recovery in one SQLite file.

Uses original PaperExchange arithmetic. No credentials, network, live adapter,
account import or automatic resubmission. One event loop, local POSIX storage.
"""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from trading_core.paper_admission import (PaperOrderRejected, QuoteAdmission,
    policy_values, validate_policy)
from trading_core.paper_control import (MODES, control_status, initial_control,
    next_control, require_mode, validate_control)
from trading_core.exchange import ExchangeInterface
from trading_core.order_manager import (
    OrderManager, OrderPersistenceError, OrderStatus, ReconciliationRequired,
    TERMINAL, _identifier, _intent, _quantity,
)
from trading_core.order_state_store import (
    SQLiteOrderStateStore, MAX_BYTES, MAX_ORDERS,
    _canonical, _decode as _decode_orders, _digest, _encode, _pairs,
)
from trading_core.paper_exchange import PaperExchange, PAPER_CALCULATION_CONTEXT

SCHEMA = ('CREATE TABLE paper_session ('
          'singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
          'version INTEGER NOT NULL, scope TEXT NOT NULL, revision INTEGER NOT NULL, '
          'payload TEXT NOT NULL, digest TEXT NOT NULL)')


def _bundle(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('invalid_paper_session_size')
    data = json.loads(raw, object_pairs_hook=_pairs)
    if (not isinstance(data, dict) or not {'account_id', 'initial', 'book', 'orders'} <= set(data)
            or not set(data) <= {'account_id', 'initial', 'book', 'orders', 'policy', 'control'}
            or _canonical(data) != raw or not isinstance(data['account_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', data['account_id'])):
        raise ValueError('invalid_paper_session')
    if 'policy' in data:
        validate_policy(data['policy'])
    if 'control' in data:
        validate_control(data['control'])
    initial = data['initial']
    if not isinstance(initial, dict) or set(initial) != {'initial_balance', 'fee_pct', 'slippage_pct', 'quote_currency'}:
        raise ValueError('invalid_paper_initial_settings')
    for name in ('initial_balance', 'fee_pct', 'slippage_pct'):
        _quantity(initial[name])
    seed = PaperExchange(**initial)
    state = data['book']
    if not isinstance(state, dict) or not isinstance(state.get('orders'), dict) or len(state['orders']) > MAX_ORDERS:
        raise ValueError('invalid_paper_book')
    book = PaperExchange.from_snapshot(state)
    if book.snapshot() != state:
        raise ValueError('noncanonical_paper_book')
    if (book.quote_currency != seed.quote_currency or book.fee_pct != seed.fee_pct
            or book.slippage_pct != seed.slippage_pct):
        raise ValueError('paper_account_configuration_changed')
    orders = _decode_orders(_canonical(data['orders']))
    allowed = {OrderStatus.SUBMITTING, OrderStatus.UNKNOWN, OrderStatus.FILLED}
    if any(order.status not in allowed or (order.status != OrderStatus.FILLED and order.filled != 0)
           for order in orders.values()):
        raise ValueError('unsupported_paper_order_state')
    cash = {seed.quote_currency: Decimal(initial['initial_balance'])}
    with localcontext(PAPER_CALCULATION_CONTEXT):
        for trade in state['trades']:
            key = trade['clientOrderId']
            if key not in orders:
                raise ValueError('untracked_paper_fill')
            order, request = orders[key], state['requests'][key]
            price = None if request[4] is None else _quantity(request[4], positive=True)
            if ((order.symbol, order.order_type.value, order.side.value, order.amount, order.price)
                    != (request[0], request[1], request[2], _quantity(request[3], positive=True), price)
                    or (order.exchange_order_id is not None and order.exchange_order_id != trade['id'])
                    or (order.status == OrderStatus.FILLED and order.filled != Decimal(trade['filled']))):
                raise ValueError('paper_order_book_mismatch')
            base, quote = trade['symbol'].split('/')
            quantity, price, fee = (Decimal(trade['amount']), Decimal(trade['average']), Decimal(trade['fee']['cost']))
            notional = quantity * price
            if trade['side'] == 'buy':
                if cash[quote] < notional + fee:
                    raise ValueError('unfunded_paper_history')
                cash[quote] -= notional + fee
                cash[base] = cash.get(base, Decimal('0')) + quantity
            else:
                if cash.get(base, Decimal('0')) < quantity:
                    raise ValueError('oversold_paper_history')
                cash[base] -= quantity
                cash[quote] = cash.get(quote, Decimal('0')) + notional - fee
    if cash != {key: Decimal(value) for key, value in state['balances'].items()}:
        raise ValueError('paper_balance_history_mismatch')
    for key, order in orders.items():
        if key not in state['orders'] and (order.filled != 0 or order.exchange_order_id is not None or order.status == OrderStatus.FILLED):
            raise ValueError('missing_paper_fill')
    return data, orders, book


class _SessionStore(SQLiteOrderStateStore):
    """Reuses v20 ownership/identity guards; schema is not an order-only store."""
    def __init__(self, path, *, initial=None, policy=None, enable_control=False):
        self._initial, self._policy = initial, policy
        self._enable_control = enable_control
        super().__init__(path, scope='paper-session-v1', create=initial is not None)

    def _create(self):
        if self.path.exists() or self.path.is_symlink():
            raise ValueError('paper_session_already_exists')
        data = dict(account_id=uuid.uuid4().hex, initial=self._initial,
                    book=PaperExchange(**self._initial).snapshot(), orders={})
        if self._policy is not None:
            data['policy'] = validate_policy(self._policy)
        if self._enable_control:
            data['control'] = initial_control()
        raw = _canonical(data)
        _bundle(raw)
        fd, name = tempfile.mkstemp(prefix='.paper-session-', dir=self.path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary, isolation_level=None)) as db:
                db.execute('PRAGMA synchronous=EXTRA')
                db.execute('BEGIN IMMEDIATE')
                db.execute(SCHEMA)
                db.execute('INSERT INTO paper_session VALUES(1,?,?,0,?,?)',
                           (3 if self._enable_control else 2 if self._policy is not None else 1,
                            self.scope, raw, _digest(raw)))
                db.execute('COMMIT')
            os.link(temporary, self.path)
            temporary.unlink()
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self):
        objects = self._db.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if objects != [('paper_session', 'table', SCHEMA)]:
            raise ValueError('unexpected_paper_session_schema')
        rows = self._db.execute('SELECT singleton,version,scope,revision,payload,digest FROM paper_session').fetchall()
        if len(rows) != 1:
            raise ValueError('missing_paper_session')
        singleton, version, scope, revision, raw, digest = rows[0]
        if (singleton != 1 or type(version) is not int or version not in (1, 2, 3) or scope != self.scope
                or type(revision) is not int or not 0 <= revision < 2**62
                or not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES
                or digest != _digest(raw)):
            raise ValueError('invalid_paper_session_row')
        data, _, _ = _bundle(raw)
        expected_version = 3 if 'control' in data else 2 if 'policy' in data else 1
        if version != expected_version:
            raise ValueError('paper_policy_version_mismatch')
        return revision, raw, digest

    def view(self):
        self.ensure_open()
        try:
            if self._read() != self._last:
                raise ValueError('paper_session_changed')
            result = _bundle(self._last[1])
            self.ensure_open()
            return result
        except Exception:
            self._broken = True
            raise OrderPersistenceError('paper_session_read_failed') from None

    def claim(self):
        if self._claimed:
            raise OrderPersistenceError('paper_session_already_claimed')
        _, orders, _ = self.view()
        self._claimed = True
        return orders

    def _write(self, data, *, control_update=False):
        self.ensure_open()
        try:
            if not self._claimed:
                raise ValueError('paper_session_requires_manager')
            raw = _canonical(data)
            _, new_orders, _ = _bundle(raw)
            old, old_orders, _ = _bundle(self._last[1])
            if control_update:
                previous = old.get('control')
                proposed = data.get('control')
                if (previous is None or proposed is None
                        or proposed == previous
                        or next_control(previous, proposed['mode'], previous['generation'],
                                        proposed['reason']) != proposed
                        or data['book'] != old['book'] or data['orders'] != old['orders']):
                    raise ValueError('invalid_paper_control_transition')
            elif data.get('control') != old.get('control'):
                raise ValueError('paper_control_requires_explicit_transition')
            if (data['account_id'] != old['account_id'] or data['initial'] != old['initial']
                    or data.get('policy') != old.get('policy')
                    or not old_orders.keys() <= new_orders.keys()
                    or data['book']['trades'][:len(old['book']['trades'])] != old['book']['trades']):
                raise ValueError('paper_history_replaced')
            for key, previous in old_orders.items():
                current = new_orders[key]
                if (_intent(previous) != _intent(current) or current.filled < previous.filled
                        or current.created_at != previous.created_at or current.updated_at < previous.updated_at
                        or (previous.exchange_order_id is not None and current.exchange_order_id != previous.exchange_order_id)
                        or (previous.status in TERMINAL and current.status != previous.status)):
                    raise ValueError('paper_order_history_changed')
            self._db.execute('BEGIN IMMEDIATE')
            if self._read() != self._last or self._last[0] >= 2**62 - 1:
                raise ValueError('paper_revision_changed_or_exhausted')
            revision, digest = self._last[0] + 1, _digest(raw)
            self._db.execute('UPDATE paper_session SET revision=?,payload=?,digest=? WHERE singleton=1', (revision, raw, digest))
            self.ensure_open()
            self._commit()
            self.ensure_open()
            self._last = revision, raw, digest
        except BaseException as exc:
            self._broken = True
            if self._db is not None and self._db.in_transaction:
                try:
                    self._db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
            if not isinstance(exc, Exception):
                raise
            raise OrderPersistenceError('paper_session_write_failed') from None

    def save_control(self, control):
        data, _, _ = self.view()
        data['control'] = control
        self._write(data, control_update=True)

    def save(self, orders):
        data, _, _ = self.view()
        data['orders'] = json.loads(_encode(orders)[0])
        self._write(data)

    def save_book(self, book):
        data, _, _ = self.view()
        data['book'] = book.snapshot()
        self._write(data)


class _PaperAdapter(ExchangeInterface):
    def __init__(self, store):
        self._store, self._connected = store, False
        self._admission = QuoteAdmission(store.view()[0].get('policy'))

    async def connect(self):
        self._store.ensure_open()
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def _book(self):
        return self._store.view()[2]

    async def _read(self, name, *args, **kwargs):
        if not self._connected:
            raise RuntimeError('paper_session_disconnected')
        book = self._book()
        await book.connect()
        return await getattr(book, name)(*args, **kwargs)

    @property
    def quote_currency(self):
        return self._book().quote_currency

    def snapshot(self):
        return self._book().snapshot()

    def get_total_value(self):
        return self._book().get_total_value()

    def set_price(self, symbol, price, *, timestamp=0):
        if type(timestamp) is not int or not 0 <= timestamp < 2**63:
            raise ValueError('invalid_paper_timestamp')
        _identifier(symbol, 'symbol')
        price = _quantity(price, positive=True)
        book = self._book()
        receipt = self._admission.prepare_quote(symbol, price, timestamp,
                                                book.snapshot()['tickers'].get(symbol))
        book.set_price(symbol, price, timestamp=timestamp)
        self._store.save_book(book)
        self._admission.accept_quote(symbol, receipt)

    async def preflight(self, order):
        """Simulate on a detached original book. Never publish its fills or balance."""
        if not self._connected:
            raise PaperOrderRejected('paper_session_disconnected')
        data, _, book = self._store.view()
        require_mode(data.get('control'), order.side)
        self._admission.require_quote(order.symbol, book.snapshot()['tickers'])
        await book.connect()
        try:
            response = await book.create_order(order.symbol, order.order_type.value,
                order.side.value, order.amount, order.price,
                {'clientOrderId': order.client_order_id})
            self._admission.require_notional(response)
            # The proposed acknowledgement must also fit the original manager contract.
            checker = OrderManager(None)
            checker._apply_response(deepcopy(order), response)
            # Also validate the prospective stage-2 bundle (chronology, size,
            # tracked intent and complete balance history) without writing it.
            data['orders'][order.client_order_id] = order.to_dict()
            data['book'] = book.snapshot()
            _bundle(_canonical(data))
        except ValueError as exc:
            if isinstance(exc, PaperOrderRejected):
                raise
            raise PaperOrderRejected('paper_order_not_executable') from None
        finally:
            await book.disconnect()

    async def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        if not self._connected:
            raise RuntimeError('paper_session_disconnected')
        if not isinstance(params, dict) or set(params) != {'clientOrderId'}:
            raise ValueError('explicit_paper_client_id_required')
        key = _identifier(params['clientOrderId'], 'client_order_id')
        _, orders, book = self._store.view()
        if key not in orders or orders[key].status != OrderStatus.SUBMITTING:
            raise ReconciliationRequired('durable_submitting_intent_required')
        order = orders[key]
        require_mode(self._store.view()[0].get('control'), order.side)
        if (symbol, order_type, side, amount, price) != (order.symbol, order.order_type.value, order.side.value, order.amount, order.price):
            raise ValueError('paper_intent_conflict')
        self._admission.require_quote(symbol, book.snapshot()['tickers'])
        await book.connect()
        response = await book.create_order(symbol, order_type, side, amount, price, params)
        self._admission.require_notional(response)
        self._admission.require_quote(symbol, book.snapshot()['tickers'])
        # No await between the final operating-mode check and account commit.
        require_mode(self._store.view()[0].get('control'), order.side)
        # Commit account effect WITH its existing intent, before the acknowledgement.
        self._store.save_book(book)
        return deepcopy(response)

    async def cancel_order(self, order_id, symbol):
        return await self._read('cancel_order', order_id, symbol)

    async def fetch_balance(self):
        return await self._read('fetch_balance')

    async def fetch_ticker(self, symbol):
        return await self._read('fetch_ticker', symbol)

    async def fetch_markets(self):
        return await self._read('fetch_markets')

    async def fetch_ohlcv(self, symbol, timeframe='1h', limit=100):
        return await self._read('fetch_ohlcv', symbol, timeframe, limit)

    async def fetch_order(self, order_id, symbol):
        return await self._read('fetch_order', order_id, symbol)

    async def fetch_order_by_client_id(self, client_order_id, symbol):
        return await self._read('fetch_order_by_client_id', client_order_id, symbol)

    async def fetch_open_orders(self, symbol=None):
        return await self._read('fetch_open_orders', symbol)

    async def fetch_my_trades(self, symbol=None, since=None, limit=None):
        return await self._read('fetch_my_trades', symbol, since, limit)


class _SessionOrderManager(OrderManager):
    async def _preflight_order(self, order):
        await self.exchange.preflight(order)


class PaperTradingSession:
    """Own one paper account and manager; never pair separately restored files.

    Explicit create=True initializes fresh cash. Reopening accepts no cash/cost
    settings and restores both projections from the same row. Use async with.
    Only manager.place_order submits; adapter writes require its durable intent.
    """
    def __init__(self, path, *, create=False, initial_balance=None, fee_pct=None,
                 slippage_pct=None, quote_currency=None, max_quote_age_ms=None,
                 max_order_notional=None, enable_control=False):
        options = dict(initial_balance=initial_balance, fee_pct=fee_pct,
                       slippage_pct=slippage_pct, quote_currency=quote_currency)
        policy = policy_values(max_quote_age_ms, max_order_notional)
        if (type(create) is not bool or type(enable_control) is not bool
                or (not create and (enable_control or any(v is not None for v in options.values())
                or any(v is not None for v in policy.values())))):
            raise ValueError('settings_only_allowed_for_new_paper_session')
        initial = None
        if create:
            defaults = dict(initial_balance='10000', fee_pct='0', slippage_pct='0', quote_currency='USDT')
            initial = {k: defaults[k] if v is None else str(v) for k, v in options.items()}
            for name in ('initial_balance', 'fee_pct', 'slippage_pct'):
                _quantity(initial[name])
            PaperExchange(**initial)  # Validate costs/currency before file creation.
        self._closed = False
        self._store = _SessionStore(path, initial=initial,
            policy=policy if any(v is not None for v in policy.values()) else None,
            enable_control=enable_control)
        try:
            self.exchange = _PaperAdapter(self._store)
            self.manager = _SessionOrderManager(self.exchange, state_store=self._store)
        except BaseException:
            self._store.close()
            raise

    @property
    def control_status(self):
        """Latest persisted decision only; not readiness, liveness or an audit log."""
        return control_status(self._store.view()[0].get('control'))

    async def set_operating_mode(self, mode, *, expected_generation, reason):
        """Explicit compare-and-swap on this owner. Never submits or cancels orders."""
        async with self.manager._lock:
            self.manager._ensure_storage()
            try:
                current = self._store.view()[0].get('control')
                proposed = next_control(current, mode, expected_generation, reason)
                if proposed == current:
                    return control_status(current)
                if MODES[mode] < MODES[current['mode']]:
                    if self.manager._inflight or any(
                            o.status not in TERMINAL for o in self.manager._orders.values()):
                        raise ReconciliationRequired('paper_control_requires_reconciliation')
                self._store.save_control(proposed)
                # A transition never blesses an old price. New receipts are required
                # only when the existing quote-age policy is enabled.
                self.exchange._admission = QuoteAdmission(self.admission_policy
                    if any(v is not None for v in self.admission_policy.values()) else None)
                return control_status(proposed)
            except BaseException as exc:
                if isinstance(exc, OrderPersistenceError) or self._store._broken:
                    self.manager._storage_failed = True
                raise

    @property
    def admission_policy(self):
        """Detached persisted settings; all None means legacy/no optional guards."""
        return deepcopy(self._store.view()[0].get('policy', policy_values()))

    @property
    def account_id(self):
        return self._store.view()[0]['account_id']

    async def __aenter__(self):
        try:
            await self.exchange.connect()
        except BaseException:
            self._store.close()
            self._closed = True
            raise
        return self

    async def close(self):
        if self._closed:
            return
        try:
            await self.manager.stop()
        finally:
            if self.manager._inflight:
                raise ReconciliationRequired('paper_session_io_still_in_progress')
            try:
                await self.exchange.disconnect()
            finally:
                self._store.close()
                self._closed = True

    async def __aexit__(self, *args):
        await self.close()
