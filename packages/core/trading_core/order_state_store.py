"""Bounded SQLite write-ahead state for OrderManager (local POSIX filesystems).

A lifetime advisory lock and revision/content checks guard cooperating writers.
Snapshots contain order fields only, not credentials or raw adapter replies.
This is NOT an account ledger, trusted attestation or exactly-once broker service.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from trading_core.order_manager import (Order, OrderManager, OrderPersistenceError,
    OrderSide, OrderStatus, OrderType, TERMINAL, _intent)

MAX_ORDERS = 10000
MAX_BYTES = 16 * 1024 * 1024
SCHEMA = ('CREATE TABLE order_state ('
          'singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
          'version INTEGER NOT NULL, scope TEXT NOT NULL, revision INTEGER NOT NULL, '
          'payload TEXT NOT NULL, digest TEXT NOT NULL)')
FIELDS = frozenset(Order('x','X/Y',OrderSide.BUY,OrderType.MARKET,1).to_dict())


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False)


def _digest(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_field')
        result[key] = value
    return result


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('invalid_snapshot_size')
    data = json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    if not isinstance(data, dict) or len(data) > MAX_ORDERS or _canonical(data) != raw:
        raise ValueError('invalid_snapshot')
    orders = {}
    for key, row in data.items():
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('invalid_order_fields')
        item = dict(row)
        item['side'] = OrderSide(item['side'])
        item['order_type'] = OrderType(item['order_type'])
        item['status'] = OrderStatus(item['status'])
        for field in ('created_at', 'updated_at'):
            item[field] = datetime.fromisoformat(item[field])
            if item[field].tzinfo is None or item[field].utcoffset() is None:
                raise ValueError('order_timestamp_requires_timezone')
        if item['updated_at'] < item['created_at']:
            raise ValueError('reversed_order_timestamps')
        orders[key] = Order(**item)
    # Reuse the original manager's structural/quantity/identity validation;
    # restore normalization is applied later by the owning manager, not here.
    checker = OrderManager(None)
    checker.load_orders(orders)
    clean = checker.get_all_orders()
    for key in clean:
        clean[key].status = orders[key].status
    return clean


def _encode(orders):
    if not isinstance(orders, dict) or len(orders) > MAX_ORDERS:
        raise ValueError('invalid_order_inventory')
    raw = _canonical({key: order.to_dict() for key, order in orders.items()})
    return raw, _decode(raw)


def _identity(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_mode & 0o077 or info.st_uid != os.geteuid()):
        raise ValueError('private_regular_file_required')
    return info.st_dev, info.st_ino


class SQLiteOrderStateStore:
    """Explicit create-new/open-existing; one process and one manager per handle.

    Keep this context open until manager I/O has stopped. Do not delete or replace
    the DB/lock files. `scope` is a local alias, NOT verified account identity.
    """
    def __init__(self, path, *, scope, create=False):
        self._db = None
        self._fd = None
        self._claimed = False
        self._pid = os.getpid()
        self._broken = False
        try:
            import fcntl
            if (type(create) is not bool or not isinstance(scope, str)
                    or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', scope)):
                raise ValueError('invalid_store_options')
            source = Path(path).absolute()
            self.path = source.parent.resolve(strict=True) / source.name
            self.scope = scope
            self._lock_path = self.path.with_name('.'+self.path.name+'.orders.lock')
            self._fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_id = _identity(self._lock_path)
            fd_info = os.fstat(self._fd)
            if self._lock_id != (fd_info.st_dev, fd_info.st_ino):
                raise ValueError('lock_changed')
            if create:
                self._create()
            self._file_id = _identity(self.path)
            self._db = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True,
                                       isolation_level=None, timeout=0.1)
            self._db.execute('PRAGMA trusted_schema=OFF')
            if self._db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise ValueError('unexpected_journal_mode')
            self._db.execute('PRAGMA synchronous=EXTRA')
            if self._db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('invalid_database')
            self._last = self._read()
            self.ensure_open()
        except BaseException as exc:
            self.close()
            if not isinstance(exc, Exception):
                raise
            raise OrderPersistenceError('order_store_open_failed') from None

    def _create(self):
        if self.path.exists() or self.path.is_symlink():
            raise ValueError('store_already_exists')
        fd, name = tempfile.mkstemp(prefix='.order-state-', dir=self.path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary, isolation_level=None)) as db:
                db.execute('PRAGMA synchronous=EXTRA')
                db.execute('BEGIN IMMEDIATE')
                db.execute(SCHEMA)
                db.execute('INSERT INTO order_state VALUES(1,1,?,0,?,?)',
                           (self.scope, '{}', _digest('{}')))
                db.execute('COMMIT')
            os.link(temporary, self.path)  # Publish without overwriting a racer.
            temporary.unlink()
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def ensure_open(self):
        try:
            if self._broken or self._db is None or self._fd is None or os.getpid() != self._pid:
                raise ValueError('unusable_store')
            if _identity(self.path) != self._file_id or _identity(self._lock_path) != self._lock_id:
                raise ValueError('store_identity_changed')
        except Exception:
            self._broken = True
            raise OrderPersistenceError('order_store_unavailable') from None

    def _read(self):
        objects = self._db.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if objects != [('order_state','table',SCHEMA)]:
            raise ValueError('unexpected_store_schema')
        rows = self._db.execute('SELECT singleton,version,scope,revision,payload,digest FROM order_state').fetchall()
        if len(rows) != 1:
            raise ValueError('missing_state')
        singleton, version, scope, revision, raw, digest = rows[0]
        if (singleton != 1 or version != 1 or scope != self.scope
                or type(revision) is not int or revision < 0 or revision >= 2**62
                or not isinstance(raw,str) or digest != _digest(raw)):
            raise ValueError('invalid_store_state')
        _decode(raw)
        return revision, raw, digest

    def claim(self):
        self.ensure_open()
        try:
            if self._claimed or self._read() != self._last:
                raise ValueError('store_in_use_or_changed')
            result = _decode(self._last[1])
            self._claimed = True
            return result
        except Exception:
            raise OrderPersistenceError('order_store_claim_failed') from None

    def _commit(self):
        self._db.execute('COMMIT')

    def save(self, orders):
        self.ensure_open()
        try:
            if not self._claimed:
                raise ValueError('store_requires_owner')
            raw, clean = _encode(orders)
            previous = _decode(self._last[1])
            if not previous.keys() <= clean.keys():
                raise ValueError('order_removal_forbidden')
            for key, old in previous.items():
                new = clean[key]
                if (_intent(new) != _intent(old) or new.filled < old.filled
                        or new.created_at != old.created_at
                        or new.updated_at < old.updated_at
                        or (old.exchange_order_id is not None and old.exchange_order_id != new.exchange_order_id)
                        or (old.status in TERMINAL and (new.status != old.status or new.filled != old.filled))):
                    raise ValueError('order_history_conflict')
            self._db.execute('BEGIN IMMEDIATE')
            current = self._read()
            if current != self._last:
                raise ValueError('store_changed')
            if current[0] >= 2**62 - 1:
                raise ValueError('revision_limit_reached')
            revision = current[0] + 1
            digest = _digest(raw)
            self._db.execute('UPDATE order_state SET revision=?,payload=?,digest=? WHERE singleton=1',
                             (revision, raw, digest))
            self.ensure_open()
            self._commit()
            self.ensure_open()
            self._last = revision, raw, digest
        except BaseException as exc:
            # After an uncertain commit outcome, never retry on this handle.
            self._broken = True
            if self._db is not None and self._db.in_transaction:
                try:
                    self._db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
            if not isinstance(exc, Exception):
                raise
            raise OrderPersistenceError('order_store_save_failed') from None

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._fd is not None:
            # No LOCK_UN: a forked child's close must not release the parent's lock.
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        self.ensure_open()
        return self

    def __exit__(self, *args):
        self.close()
