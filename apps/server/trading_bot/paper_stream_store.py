"""Atomic local PAPER candle journal and complete incremental-engine checkpoint.

One cooperating POSIX owner, explicit create/resume, bounded append-only history.
Passive readers do not repair journals. Full audit readers take a nonblocking
shared owner lock; an audit is deliberately unavailable while a writer is active.
Hashes establish consistency, not source authenticity or protection from a
privileged rewrite. No provider, broker, credentials, or network is accepted.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timezone
from decimal import Decimal, DecimalException, localcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from trading_bot.paper_runner import PaperRunConfig
from trading_core.order_manager import _quantity
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_data.upbit_history import _milliseconds, _row, parse_end, timeframe
from trading_bot.market_capture_store import validate_config as validate_capture_config

DB_NAME = 'stream.sqlite3'
LOCK_NAME = '.stream.lock'
APPLICATION_ID = 0x55505331
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_HEAD_BYTES = 512 * 1024
MAX_EVENT_BYTES = 16 * 1024
MAX_BARS = 100000
MAX_HISTORY_ROWS = 5000
MAX_SAFE_INTEGER = 2**53 - 1
MAX_VM_OPERATIONS = 2_000_000
MAX_OBSERVATION_SECONDS = 2.0
EVENT_BATCH_SIZE = 64
ZERO_HASH = '0' * 64
_HEX = re.compile('[0-9a-f]{64}')
_MANIFEST_FIELDS = {'format', 'version', 'mode', 'live_enabled', 'execution_model',
    'stream_id', 'created_at', 'config', 'bootstrap', 'capture', 'activation_source',
    'engine_sha256', 'poll_seconds', 'max_bars'}
_SOURCE_FIELDS = {'revision', 'accepted_bars_total', 'evicted_bars_total',
    'last_verified_at', 'latest_open_ms', 'snapshot_sha256'}
_CONTROL_FIELDS = {'revision', 'phase', 'last_error', 'updated_at', 'source'}
_HEAD_FIELDS = {'control', 'engine', 'engine_sha256', 'event_head_sha256'}
_EVENT_FIELDS = {'sequence', 'bar', 'fill', 'applied_at', 'source',
    'previous_sha256', 'engine_sha256', 'sha256'}
_PHASES = {'waiting', 'running', 'stopped', 'blocked'}
_ERRORS = {'source_unavailable', 'source_not_current', 'source_identity_changed',
    'source_history_conflict', 'source_regression', 'retention_lost',
    'source_blocked', 'clock_regression', 'capacity', 'engine_rejected'}
_SCHEMA = {
    'stream_manifest': ('table', 'CREATE TABLE stream_manifest (singleton INTEGER PRIMARY KEY '
        'CHECK(singleton = 1), body TEXT NOT NULL CHECK(typeof(body) = \'text\' '
        'AND length(CAST(body AS BLOB)) <= 524288), sha256 TEXT NOT NULL)'),
    'stream_head': ('table', 'CREATE TABLE stream_head (singleton INTEGER PRIMARY KEY '
        'CHECK(singleton = 1), body TEXT NOT NULL CHECK(typeof(body) = \'text\' '
        'AND length(CAST(body AS BLOB)) <= 524288), sha256 TEXT NOT NULL)'),
    'stream_events': ('table', 'CREATE TABLE stream_events (sequence INTEGER PRIMARY KEY '
        'CHECK(sequence >= 1 AND sequence <= 100000), open_ms INTEGER NOT NULL '
        'CHECK(open_ms >= 0 AND open_ms <= 253402300799999), body TEXT NOT NULL '
        'CHECK(typeof(body) = \'text\' AND length(CAST(body AS BLOB)) <= 16384), '
        'sha256 TEXT NOT NULL)'),
    'stream_open_ms': ('index', 'CREATE UNIQUE INDEX stream_open_ms ON stream_events (open_ms)'),
}


class StreamUnavailable(ValueError):
    """The source or stream is invalid, unavailable, conflicting, or owned."""


class StreamStorageError(OSError):
    """Durability is unconfirmed; retain artifacts and abandon this owner."""


class StreamCapacity(StreamUnavailable):
    """The bounded archive cannot accept another bar; preserve its account."""


def _invalid():
    return StreamUnavailable('continuous_paper_unavailable')


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise _invalid()
    return value


def _hash(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise _invalid()
    return value


def _stamp(at):
    _milliseconds(at)
    return at.astimezone(timezone.utc).isoformat()


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise _invalid()
    parsed = parse_end(value)
    if parsed.utcoffset().total_seconds() != 0 or parsed.isoformat() != value:
        raise _invalid()
    return parsed


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _decode(raw, limit):
    if not isinstance(raw, str) or not 1 <= len(raw.encode('utf-8')) <= limit:
        raise _invalid()
    def invalid_constant(_):
        raise _invalid()
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise _invalid()
    return value


def _bounded(value, limit):
    raw = canonical_json(value)
    if len(raw.encode('utf-8')) > limit:
        raise _invalid()
    return raw


def _bar(value, duration):
    if not isinstance(value, list) or len(value) != 6:
        raise _invalid()
    _integer(value[0], 0, 253402300799999)
    if value[0] % duration or value != _row(value[0], value[1:]):
        raise _invalid()
    return value


def _source(value, manifest):
    if not isinstance(value, dict) or set(value) != _SOURCE_FIELDS:
        raise _invalid()
    for field in ('revision', 'accepted_bars_total', 'evicted_bars_total'):
        _integer(value[field], 0, MAX_SAFE_INTEGER)
    config = manifest['capture']['config']
    duration = config['unit_minutes'] * 60000
    _integer(value['latest_open_ms'], 0, 253402300799999 - duration)
    verified = _time(value['last_verified_at'])
    retained = value['accepted_bars_total'] - value['evicted_bars_total']
    if (value['latest_open_ms'] % duration
            or value['revision'] < 1
            or value['accepted_bars_total'] < config['bootstrap_bars']
            or not 1 <= retained <= config['retention_bars']
            or (value['evicted_bars_total'] and retained != config['retention_bars'])
            or value['latest_open_ms'] + duration + 5000 > _milliseconds(verified)):
        raise _invalid()
    _hash(value['snapshot_sha256'])
    return value


def _source_progress(before, after, manifest):
    _source(after, manifest)
    duration = manifest['capture']['config']['unit_minutes'] * 60000
    if (any(after[field] < before[field] for field in
            ('revision', 'accepted_bars_total', 'evicted_bars_total', 'latest_open_ms'))
            or _time(after['last_verified_at']) < _time(before['last_verified_at'])
            or (after['revision'] == before['revision'] and after != before)
            or after['latest_open_ms'] - before['latest_open_ms']
                != (after['accepted_bars_total'] - before['accepted_bars_total']) * duration):
        raise _invalid()


def _manifest(value):
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise _invalid()
    _bounded(value, MAX_MANIFEST_BYTES)
    if (value['format'] != 'uulab.continuous-paper' or type(value['version']) is not int
            or value['version'] != 1 or value['mode'] != 'paper'
            or value['live_enabled'] is not False
            or value['execution_model'] != 'delayed_next_open'
            or not isinstance(value['stream_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', value['stream_id'])
            or type(value['max_bars']) is not int or value['max_bars'] != 100000):
        raise _invalid()
    created = _time(value['created_at'])
    _integer(value['poll_seconds'], 5, 300)
    _hash(value['engine_sha256'])
    config = value['config']
    if not isinstance(config, dict) or PaperRunConfig(**config).identity() != config:
        raise _invalid()
    capture = value['capture']
    if (not isinstance(capture, dict) or set(capture) != {'path', 'binding', 'capture_id', 'config'}
            or not isinstance(capture['capture_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', capture['capture_id'])):
        raise _invalid()
    source_config = validate_capture_config(capture['config'])
    if (config['symbol'] != source_config['market'][4:] + '/KRW'
            or config['timeframe'] != timeframe(source_config['unit_minutes'])):
        raise _invalid()
    path, binding = capture['path'], capture['binding']
    if (not isinstance(path, str) or not 1 <= len(path) <= 4096 or '\x00' in path
            or not os.path.isabs(path) or os.path.normpath(path) != path
            or not isinstance(binding, dict)
            or set(binding) != {'parent_path', 'parent', 'directory', 'database', 'lock'}
            or binding['parent_path'] != str(Path(path).parent)):
        raise _invalid()
    for name in ('parent', 'directory', 'database', 'lock'):
        entry = binding[name]
        if not isinstance(entry, list) or len(entry) != 5:
            raise _invalid()
        for number in entry:
            _integer(number, 0, 2**64 - 1)
        if entry[1] == 0 or not (stat.S_ISDIR(entry[2]) if name in ('parent', 'directory')
                                else stat.S_ISREG(entry[2])):
            raise _invalid()
        if name != 'parent' and (entry[3] != os.geteuid()
                or stat.S_IMODE(entry[2]) != (0o700 if name == 'directory' else 0o600)):
            raise _invalid()
    duration = source_config['unit_minutes'] * 60000
    rows = value['bootstrap']
    minimum = max(50, config['slow_period'] if config['strategy'] == 'ma_cross' else config['rsi_period'] + 1)
    if not isinstance(rows, list) or not minimum <= len(rows) <= 500:
        raise _invalid()
    for index, row in enumerate(rows):
        _bar(row, duration)
        if index and row[0] != rows[index - 1][0] + duration:
            raise _invalid()
    source = _source(value['activation_source'], value)
    if (source['latest_open_ms'] != rows[-1][0]
            or len(rows) > source['accepted_bars_total'] - source['evicted_bars_total']
            or _time(source['last_verified_at']) > created):
        raise _invalid()
    return value


def _engine(value):
    # Delayed import keeps the persistence module independent of the coordinator.
    from trading_bot.paper_stream_engine import validate_state
    return validate_state(value)


def _snapshot(manifest, head):
    _manifest(manifest)
    if not isinstance(head, dict) or set(head) != _HEAD_FIELDS:
        raise _invalid()
    _bounded(head, MAX_HEAD_BYTES)
    engine = _engine(head['engine'])
    _hash(head['engine_sha256'])
    _hash(head['event_head_sha256'])
    count = _integer(engine['next_index'], 0, manifest['max_bars'])
    duration = manifest['capture']['config']['unit_minutes'] * 60000
    if (engine['config'] != manifest['config']
            or engine['bootstrap_count'] != len(manifest['bootstrap'])
            or engine['last_open_ms'] != manifest['bootstrap'][-1][0] + count * duration
            or head['engine_sha256'] != digest(engine)
            or (count == 0) != (head['event_head_sha256'] == ZERO_HASH)):
        raise _invalid()
    if count == 0 and (engine['candles'] != manifest['bootstrap']
            or Decimal(engine['cash']) != Decimal(manifest['config']['initial_balance'])
            or Decimal(engine['holding']) != 0 or Decimal(engine['total_fees']) != 0
            or engine['fill_count'] != 0 or engine['pending_signal'] is not None):
        raise _invalid()
    control = head['control']
    if not isinstance(control, dict) or set(control) != _CONTROL_FIELDS:
        raise _invalid()
    _integer(control['revision'], count, MAX_SAFE_INTEGER)
    if (control['phase'] not in _PHASES
            or (control['last_error'] is not None and control['last_error'] not in _ERRORS)
            or (control['phase'] == 'blocked' and control['last_error'] is None)
            or (control['phase'] == 'running' and control['last_error'] is not None)
            or _time(control['updated_at']) < _time(manifest['created_at'])):
        raise _invalid()
    source = control['source']
    _source_progress(manifest['activation_source'], source, manifest)
    if (_time(source['last_verified_at']) > _time(control['updated_at'])
            or source['latest_open_ms'] < engine['last_open_ms']):
        raise _invalid()
    if control['revision'] == 0 and (count != 0 or control['phase'] != 'waiting'
            or control['last_error'] is not None or control['updated_at'] != manifest['created_at']
            or source != manifest['activation_source']):
        raise _invalid()
    return dict(schema_version=1, manifest=manifest, **head)


def _account_number(value, *, positive=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise _invalid()
    try:
        number = Decimal(value)
        if (not number.is_finite() or number.is_signed() or (positive and number == 0)
                or number > Decimal('1e100') or not -100 <= number.as_tuple().exponent <= 100
                or str(number) != value):
            raise _invalid()
    except DecimalException as error:
        raise _invalid() from error
    return number


def _fill(value, row, sequence, manifest):
    if value is None:
        return
    expected = {'id', 'clientOrderId', 'symbol', 'type', 'side', 'amount', 'filled',
        'remaining', 'average', 'price', 'status', 'timestamp', 'fee'}
    if not isinstance(value, dict) or set(value) != expected:
        raise _invalid()
    side = value['side']
    client_id = f'fixture-{sequence - 2}-{side}'
    if (sequence < 2 or side not in {'buy', 'sell'} or value['type'] != 'market'
            or value['status'] != 'closed' or value['symbol'] != manifest['config']['symbol']
            or value['clientOrderId'] != client_id or value['id'] != 'paper-' + client_id
            or type(value['timestamp']) is not int or value['timestamp'] != row[0]
            or not isinstance(value['fee'], dict) or set(value['fee']) != {'cost', 'currency'}
            or value['fee']['currency'] != 'KRW'):
        raise _invalid()
    numbers = {}
    for name in ('amount', 'filled', 'remaining'):
        if not isinstance(value[name], str):
            raise _invalid()
        numbers[name] = _quantity(value[name], positive=name != 'remaining')
    # Actual fill prices/fees are account values, not request quantities. Valid
    # original calculations can have exponents below the manager's -30 bound.
    for name in ('average', 'price'):
        numbers[name] = _account_number(value[name], positive=True)
    fee = _account_number(value['fee']['cost'])
    with localcontext(PAPER_CALCULATION_CONTEXT):
        price = Decimal(row[1]) * (1 + (1 if side == 'buy' else -1)
                                  * Decimal(manifest['config']['slippage_pct']) / 100)
        if (numbers['amount'] != numbers['filled'] or numbers['remaining'] != 0
                or numbers['average'] != price or numbers['price'] != price
                or fee != numbers['amount'] * price * Decimal(manifest['config']['fee_pct']) / 100):
            raise _invalid()


def _event(value, manifest):
    if not isinstance(value, dict) or set(value) != _EVENT_FIELDS:
        raise _invalid()
    _bounded(value, MAX_EVENT_BYTES)
    sequence = _integer(value['sequence'], 1, manifest['max_bars'])
    duration = manifest['capture']['config']['unit_minutes'] * 60000
    row = _bar(value['bar'], duration)
    if row[0] != manifest['bootstrap'][-1][0] + sequence * duration:
        raise _invalid()
    _hash(value['previous_sha256'])
    _hash(value['engine_sha256'])
    if (value['sha256'] != digest({key: item for key, item in value.items() if key != 'sha256'})
            or _time(value['applied_at']) < _time(manifest['created_at'])):
        raise _invalid()
    source = _source(value['source'], manifest)
    _source_progress(manifest['activation_source'], source, manifest)
    if source['latest_open_ms'] < row[0] or _time(source['last_verified_at']) > _time(value['applied_at']):
        raise _invalid()
    _fill(value['fill'], row, sequence, manifest)
    return value


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _content(info):
    return _identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _directory_info(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise _invalid()
    return str(path.resolve(strict=True)), _identity(info)


def _private(info, *, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise _invalid()


class _Binding:
    """Lifetime descriptors and name checks; no promise against privileged races."""
    def __init__(self, directory, *, create=False):
        self.path = Path(directory).absolute()
        self.parent_fd = self.directory_fd = self.database_fd = self.lock_fd = None
        self.pid = os.getpid()
        try:
            if self.path.name in {'', '.', '..'}:
                raise _invalid()
            self.parent_before = _directory_info(self.path.parent)
            self.parent_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY
                | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            if _identity(os.fstat(self.parent_fd)) != self.parent_before[1]:
                raise _invalid()
            if create:
                os.mkdir(self.path.name, 0o700, dir_fd=self.parent_fd)
            self.directory_before = _directory_info(self.path)
            self.directory_fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY
                | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.parent_fd)
            _private(os.fstat(self.directory_fd), directory=True)
            self._check_directory()
        except BaseException:
            self.close()
            raise

    def _check_directory(self):
        if (os.getpid() != self.pid or self.parent_before != _directory_info(self.path.parent)
                or _identity(os.fstat(self.parent_fd)) != self.parent_before[1]
                or self.directory_before != _directory_info(self.path)
                or _identity(os.fstat(self.directory_fd)) != self.directory_before[1]):
            raise _invalid()
        _private(os.fstat(self.directory_fd), directory=True)

    def open_files(self, *, create, writer, audit=False):
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        self.lock_fd = os.open(LOCK_NAME, flags | (os.O_RDWR if writer else os.O_RDONLY),
                               0o600, dir_fd=self.directory_fd)
        if create:
            os.fchmod(self.lock_fd, 0o600)
            os.fsync(self.lock_fd)
        self.lock_before = _identity(os.fstat(self.lock_fd))
        _private(os.fstat(self.lock_fd))
        if os.fstat(self.lock_fd).st_size != 0:
            raise _invalid()
        if writer or audit:
            try:
                fcntl.flock(self.lock_fd, (fcntl.LOCK_EX if writer else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise _invalid() from error
        self.database_fd = os.open(DB_NAME, flags | (os.O_RDWR if writer else os.O_RDONLY),
                                   0o600, dir_fd=self.directory_fd)
        if create:
            os.fchmod(self.database_fd, 0o600)
        info = os.fstat(self.database_fd)
        _private(info)
        self.database_before, self.content_before = _identity(info), _content(info)
        self.check(observer=not writer, empty=create)
        if create:
            os.fsync(self.directory_fd)
            os.fsync(self.parent_fd)

    def check(self, *, observer=False, empty=False, stable=False):
        self._check_directory()
        for name, fd, expected in ((LOCK_NAME, self.lock_fd, self.lock_before),
                                  (DB_NAME, self.database_fd, self.database_before)):
            opened = os.fstat(fd)
            named = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
            _private(opened)
            _private(named)
            if _identity(opened) != expected or _identity(named) != expected:
                raise _invalid()
        info = os.fstat(self.database_fd)
        if (not (0 if empty else 100) <= info.st_size <= MAX_DATABASE_BYTES
                or os.fstat(self.lock_fd).st_size != 0
                or (stable and _content(info) != self.content_before)):
            raise _invalid()
        if not empty:
            header = os.pread(self.database_fd, 100, 0)
            if header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
                raise _invalid()
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                sidecar = os.stat(DB_NAME + suffix, dir_fd=self.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _private(sidecar)
            if sidecar.st_size > MAX_DATABASE_BYTES or suffix != '-journal' or (observer and sidecar.st_size):
                raise _invalid()

    def remember_content(self):
        self.content_before = _content(os.fstat(self.database_fd))

    def close(self):
        for name in ('database_fd', 'lock_fd', 'directory_fd', 'parent_fd'):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
        # Do not LOCK_UN: closing a forked descriptor must not unlock its parent.


def _connect(binding, *, observer, empty=False):
    connection = sqlite3.connect((binding.path / DB_NAME).as_uri()
        + ('?mode=ro' if observer else '?mode=rw'), uri=True, timeout=0.2, isolation_level=None)
    try:
        # SQLite separately opens by name. Check before any setting can read,
        # recover, or initialize a substituted database.
        binding.check(observer=observer, empty=empty)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-2048')
        connection.execute('PRAGMA query_only=ON' if observer else 'PRAGMA synchronous=FULL')
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def _budget(connection):
    started, operations = time.monotonic(), [0]
    def progress():
        operations[0] += 1000
        return int(operations[0] >= MAX_VM_OPERATIONS
                   or time.monotonic() - started > MAX_OBSERVATION_SECONDS)
    connection.set_progress_handler(progress, 1000)
    try:
        yield
        if time.monotonic() - started > MAX_OBSERVATION_SECONDS:
            raise _invalid()
    finally:
        connection.set_progress_handler(None, 0)


def _check_schema(connection):
    if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
            or connection.execute('PRAGMA user_version').fetchone()[0] != 1
            or connection.execute('PRAGMA page_size').fetchone()[0] != 4096
            or connection.execute('PRAGMA page_count').fetchone()[0] > MAX_DATABASE_BYTES // 4096
            or connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete'):
        raise _invalid()
    objects = connection.execute('SELECT type,substr(name,1,65),substr(tbl_name,1,65),'
        'substr(sql,1,4097),length(CAST(sql AS BLOB)) FROM sqlite_schema LIMIT 5').fetchall()
    if len(objects) != len(_SCHEMA):
        raise _invalid()
    for kind, name, table, sql, length in objects:
        if (name not in _SCHEMA or (kind, sql) != _SCHEMA[name]
                or table != ('stream_events' if kind == 'index' else name)
                or length != len(sql)):
            raise _invalid()


def _read_object(connection, table, limit):
    rows = connection.execute('SELECT singleton,typeof(body),length(CAST(body AS BLOB)),'
        'substr(body,1,?),typeof(sha256),length(sha256),substr(sha256,1,65) FROM '
        + table + ' LIMIT 2', (limit + 1,)).fetchall()
    if len(rows) != 1:
        raise _invalid()
    singleton, kind, length, raw, hash_kind, hash_length, saved_hash = rows[0]
    if (singleton != 1 or kind != 'text' or not 1 <= length <= limit
            or hash_kind != 'text' or hash_length != 64):
        raise _invalid()
    value = _decode(raw, limit)
    if saved_hash != digest(value):
        raise _invalid()
    return value


_EVENT_SELECT = ('SELECT sequence,open_ms,typeof(body),length(CAST(body AS BLOB)),'
    'substr(body,1,16385),typeof(sha256),length(sha256),substr(sha256,1,65) FROM stream_events ')


def _stored_event(row, manifest):
    sequence, opening, kind, length, raw, hash_kind, hash_length, saved_hash = row
    if (kind != 'text' or not 1 <= length <= MAX_EVENT_BYTES
            or hash_kind != 'text' or hash_length != 64):
        raise _invalid()
    value = _event(_decode(raw, MAX_EVENT_BYTES), manifest)
    if sequence != value['sequence'] or opening != value['bar'][0] or saved_hash != value['sha256']:
        raise _invalid()
    return value


def _read_snapshot(connection):
    with _budget(connection):
        _check_schema(connection)
        manifest = _read_object(connection, 'stream_manifest', MAX_MANIFEST_BYTES)
        head = _read_object(connection, 'stream_head', MAX_HEAD_BYTES)
        snapshot = _snapshot(manifest, head)
        rows = connection.execute(_EVENT_SELECT + 'ORDER BY sequence DESC LIMIT 1').fetchall()
        count = snapshot['engine']['next_index']
        if count == 0:
            if rows:
                raise _invalid()
        else:
            if not rows:
                raise _invalid()
            event = _stored_event(rows[0], manifest)
            if (event['sequence'] != count or event['bar'] != snapshot['engine']['candles'][-1]
                    or event['engine_sha256'] != snapshot['engine_sha256']
                    or event['sha256'] != snapshot['event_head_sha256']
                    or _time(event['applied_at']) > _time(snapshot['control']['updated_at'])):
                raise _invalid()
            _source_progress(event['source'], snapshot['control']['source'], manifest)
        return snapshot


def _head(snapshot):
    return {key: snapshot[key] for key in _HEAD_FIELDS}


class _Reader:
    def __init__(self, binding, connection, snapshot, *, writer=False, audit=False):
        self.binding, self.connection = binding, connection
        self._last, self._writer, self._audit = snapshot, writer, audit
        self._broken = False

    def _ensure(self):
        if self._broken:
            if self._writer:
                raise StreamStorageError('continuous_paper_storage_unconfirmed')
            raise _invalid()
        try:
            self.binding.check(observer=not self._writer, stable=True)
        except (OSError, ValueError, TypeError) as error:
            self._broken = True
            raise _invalid() from error

    def _same_head(self, snapshot):
        if snapshot != self._last:
            self._broken = True
            raise _invalid()

    def read(self):
        self._ensure()
        try:
            self.connection.execute('BEGIN')
            snapshot = _read_snapshot(self.connection)
            self._same_head(snapshot)
            self._ensure()
            return deepcopy(snapshot)
        except (OSError, sqlite3.Error) as error:
            if self._writer:
                self._broken = True
                raise StreamStorageError('continuous_paper_storage_unconfirmed') from error
            raise _invalid() from error
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise _invalid() from error
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()

    def _event_batch(self, first, count, snapshot):
        self._ensure()
        try:
            self.connection.execute('BEGIN')
            self._same_head(_read_snapshot(self.connection))
            if self._last != snapshot:
                raise _invalid()
            with _budget(self.connection):
                rows = self.connection.execute(_EVENT_SELECT
                    + 'WHERE sequence >= ? ORDER BY sequence LIMIT ?', (first, count)).fetchall()
                events = [_stored_event(row, snapshot['manifest']) for row in rows]
            self._ensure()
            return events
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise _invalid() from error
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()

    def events(self):
        if not (self._writer or self._audit):
            raise _invalid()
        snapshot = self.read()
        count = snapshot['engine']['next_index']
        sequence, previous = 1, ZERO_HASH
        source = snapshot['manifest']['activation_source']
        applied_at = snapshot['manifest']['created_at']
        while sequence <= count:
            size = min(EVENT_BATCH_SIZE, count - sequence + 1)
            events = self._event_batch(sequence, size, snapshot)
            if len(events) != size:
                raise _invalid()
            for event in events:
                if (event['sequence'] != sequence or event['previous_sha256'] != previous
                        or _time(event['applied_at']) < _time(applied_at)):
                    raise _invalid()
                _source_progress(source, event['source'], snapshot['manifest'])
                previous, source, applied_at = event['sha256'], event['source'], event['applied_at']
                sequence += 1
                yield deepcopy(event)
        if previous != snapshot['event_head_sha256']:
            raise _invalid()
        self._ensure()


class _Writer(_Reader):
    def __init__(self, binding, connection, snapshot, *, created):
        super().__init__(binding, connection, snapshot, writer=True)
        self._verified = created

    def verify_head(self, engine_sha256, event_head_sha256):
        """Bind the coordinator's completed full replay to this owned head."""
        snapshot = self.read()
        if (engine_sha256 != snapshot['engine_sha256']
                or event_head_sha256 != snapshot['event_head_sha256']):
            raise _invalid()
        self._verified = True

    def _commit(self):
        self.connection.commit()

    def _write(self, snapshot, event=None):
        # All candidate validation precedes SQL writes. The old head stays the
        # sole authoritative in-memory state until commit and sync confirm.
        _snapshot(snapshot['manifest'], _head(snapshot))
        if snapshot['manifest'] != self._last['manifest']:
            raise _invalid()
        raw = _bounded(_head(snapshot), MAX_HEAD_BYTES)
        event_raw = _bounded(event, MAX_EVENT_BYTES) if event is not None else None
        self._ensure()
        if event_raw is not None:
            # Reserve a full replacement head, the new event and eight B-tree/
            # control pages. This conservative stop happens before any account
            # effect, leaving room to record the terminal capacity observation.
            # The SQLite page ceiling remains a second, hard storage boundary.
            reserve_pages = ((len(raw.encode('utf-8')) + 4095) // 4096
                             + (len(event_raw.encode('utf-8')) + 4095) // 4096 + 8)
            if self.binding.content_before[1] + reserve_pages * 4096 > MAX_DATABASE_BYTES:
                raise StreamCapacity('continuous_paper_capacity')
        try:
            self.connection.execute('BEGIN IMMEDIATE')
            self._same_head(_read_snapshot(self.connection))
            if event is not None:
                self.connection.execute('INSERT INTO stream_events VALUES (?,?,?,?)',
                    (event['sequence'], event['bar'][0], event_raw, event['sha256']))
            self.connection.execute('UPDATE stream_head SET body=?,sha256=? WHERE singleton=1',
                                    (raw, digest(_head(snapshot))))
            self.binding.check()
            self._commit()
            os.fsync(self.binding.directory_fd)
            self.binding.check()
            self.binding.remember_content()
            self._last = deepcopy(snapshot)
            return deepcopy(snapshot)
        except BaseException as error:
            self._broken = True
            if self.connection.in_transaction:
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    pass
            if not isinstance(error, Exception):
                raise
            raise StreamStorageError('continuous_paper_storage_unconfirmed') from error

    def _control(self, phase, error, at, source):
        previous = self._last['control']
        stamp = _stamp(at)
        if (phase not in _PHASES or (error is not None and error not in _ERRORS)
                or previous['phase'] == 'blocked'
                or (phase == 'blocked' and error is None)
                or (phase == 'running' and error is not None)
                or _time(stamp) < _time(previous['updated_at'])
                or previous['revision'] >= MAX_SAFE_INTEGER):
            raise _invalid()
        incoming = previous['source'] if source is None else source
        _source_progress(previous['source'], incoming, self._last['manifest'])
        if _time(incoming['last_verified_at']) > _time(stamp):
            raise _invalid()
        return dict(revision=previous['revision'] + 1, phase=phase, last_error=error,
                    updated_at=stamp, source=deepcopy(incoming))

    def control(self, phase, error, *, at, source=None):
        self.read()
        try:
            if not self._verified and phase != 'blocked':
                raise _invalid()
            proposed = deepcopy(self._last)
            proposed['control'] = self._control(phase, error, at, source)
            return self._write(proposed)
        except StreamUnavailable:
            raise
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            raise _invalid() from error

    def latch(self, error):
        """Persist terminal refusal when a new wall-clock stamp is untrustworthy.

        Retain the last trusted control timestamp verbatim. This is an explicit
        failure observation, never a heartbeat, successful commit time, or clock
        clamp. Account, archive and source watermark remain unchanged.
        """
        snapshot = self.read()
        terminal = _ERRORS - {'source_unavailable', 'source_not_current'}
        if error not in terminal:
            raise _invalid()
        control = snapshot['control']
        if control['phase'] == 'blocked':
            return snapshot
        if control['last_error'] in terminal:
            error = control['last_error']
        return self.control('blocked', error, at=_time(control['updated_at']))

    def commit_bar(self, engine, bar, fill, *, at, source):
        self.read()
        try:
            if not self._verified or self._last['control']['phase'] == 'blocked':
                raise _invalid()
            previous = self._last['engine']
            if previous['next_index'] >= min(MAX_BARS, self._last['manifest']['max_bars']):
                raise StreamCapacity('continuous_paper_capacity')
            engine = deepcopy(_engine(engine))
            duration = self._last['manifest']['capture']['config']['unit_minutes'] * 60000
            _bar(bar, duration)
            if (engine['next_index'] != previous['next_index'] + 1
                    or bar[0] != previous['last_open_ms'] + duration
                    or engine['last_open_ms'] != bar[0]
                    or engine['candles'] != (previous['candles'] + [bar])[-500:]
                    or engine['config'] != previous['config']
                    or engine['bootstrap_count'] != previous['bootstrap_count']):
                raise _invalid()
            event = dict(sequence=engine['next_index'], bar=deepcopy(bar), fill=deepcopy(fill),
                applied_at=_stamp(at), source=deepcopy(source),
                previous_sha256=self._last['event_head_sha256'], engine_sha256=digest(engine))
            event['sha256'] = digest(event)
            _event(event, self._last['manifest'])
            with localcontext(PAPER_CALCULATION_CONTEXT):
                cash, holding, fees = (Decimal(previous[key]) for key in ('cash', 'holding', 'total_fees'))
                if fill is not None:
                    if previous['pending_signal'] is None or previous['pending_signal']['action'] != fill['side']:
                        raise _invalid()
                    amount, price, fee = Decimal(fill['amount']), Decimal(fill['average']), Decimal(fill['fee']['cost'])
                    cost = amount * price
                    # Preserve the original exchange's operation order: changing
                    # grouping changes Decimal rounding at 28-digit boundaries.
                    if fill['side'] == 'buy':
                        cash -= cost + fee
                        holding += amount
                    else:
                        cash = cash + cost - fee
                        holding -= amount
                    fees += fee
                if (Decimal(engine['cash']) != cash or Decimal(engine['holding']) != holding
                        or Decimal(engine['total_fees']) != fees
                        or engine['fill_count'] != previous['fill_count'] + int(fill is not None)
                        or Decimal(engine['equity']) != cash + holding * Decimal(bar[4])):
                    raise _invalid()
            proposed = dict(self._last, engine=engine, engine_sha256=event['engine_sha256'],
                event_head_sha256=event['sha256'],
                control=self._control('running', None, at, source))
            return self._write(proposed, event)
        except StreamUnavailable:
            raise
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            raise _invalid() from error

    def history(self, first_open_ms, last_open_ms):
        """Return at most 5,000 archived canonical rows, using indexed sequence."""
        snapshot = self.read()
        duration = snapshot['manifest']['capture']['config']['unit_minutes'] * 60000
        _integer(first_open_ms, 0, 253402300799999)
        _integer(last_open_ms, first_open_ms, 253402300799999)
        if (first_open_ms % duration or last_open_ms % duration
                or (last_open_ms - first_open_ms) // duration + 1 > MAX_HISTORY_ROWS):
            raise _invalid()
        bootstrap = snapshot['manifest']['bootstrap']
        result = [deepcopy(row) for row in bootstrap if first_open_ms <= row[0] <= last_open_ms]
        origin = bootstrap[-1][0]
        first = max(1, (first_open_ms - origin) // duration)
        last = min(snapshot['engine']['next_index'], (last_open_ms - origin) // duration)
        if first <= last:
            previous = ZERO_HASH
            if first > 1:
                prior = self._event_batch(first - 1, 1, snapshot)
                if len(prior) != 1 or prior[0]['sequence'] != first - 1:
                    raise _invalid()
                previous = prior[0]['sha256']
            position = first
            while position <= last:
                size = min(EVENT_BATCH_SIZE, last - position + 1)
                events = self._event_batch(position, size, snapshot)
                if len(events) != size:
                    raise _invalid()
                for event in events:
                    if event['sequence'] != position or event['previous_sha256'] != previous:
                        raise _invalid()
                    result.append(deepcopy(event['bar']))
                    position, previous = position + 1, event['sha256']
        return result


@contextmanager
def stream_writer(directory, *, manifest=None, engine=None, at=None, source=None):
    """Create a new leaf with all inputs, or open an existing account unchanged."""
    binding = connection = None
    created = manifest is not None
    publication_attempted = False
    try:
        if created:
            if engine is None or at is None or source is None:
                raise _invalid()
            manifest, engine, source = deepcopy(manifest), deepcopy(engine), deepcopy(source)
            _manifest(manifest)
            if _stamp(at) != manifest['created_at'] or source != manifest['activation_source']:
                raise _invalid()
            initial = _snapshot(manifest, dict(engine=engine, engine_sha256=digest(engine),
                event_head_sha256=ZERO_HASH, control=dict(revision=0, phase='waiting',
                last_error=None, updated_at=_stamp(at), source=source)))
        elif any(value is not None for value in (engine, at, source)):
            raise _invalid()
        binding = _Binding(directory, create=created)
        binding.open_files(create=created, writer=True)
        connection = _connect(binding, observer=False, empty=created)
        if created:
            connection.execute('PRAGMA page_size=4096')
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // 4096))
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('PRAGMA application_id=' + str(APPLICATION_ID))
            connection.execute('PRAGMA user_version=1')
            for kind, sql in _SCHEMA.values():
                connection.execute(sql)
            connection.execute('INSERT INTO stream_manifest VALUES (1,?,?)',
                (_bounded(manifest, MAX_MANIFEST_BYTES), digest(manifest)))
            connection.execute('INSERT INTO stream_head VALUES (1,?,?)',
                (_bounded(_head(initial), MAX_HEAD_BYTES), digest(_head(initial))))
            binding.check(empty=True)
            publication_attempted = True
            connection.commit()
            os.fsync(binding.directory_fd)
        binding.check()
        connection.execute('BEGIN')
        snapshot = _read_snapshot(connection)
        connection.rollback()
        binding.check()
        binding.remember_content()
        connection.execute('PRAGMA max_page_count=' + str(MAX_DATABASE_BYTES // 4096))
        writer = _Writer(binding, connection, snapshot, created=created)
    except BaseException as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            binding.close()
        if not isinstance(error, Exception):
            raise
        if (publication_attempted or isinstance(error, sqlite3.OperationalError)
                or (isinstance(error, OSError)
                    and not isinstance(error, (FileExistsError, FileNotFoundError, PermissionError)))
                or (created and isinstance(error, sqlite3.Error))):
            raise StreamStorageError('continuous_paper_storage_unconfirmed') from error
        raise _invalid() from error
    try:
        yield writer
    finally:
        writer._broken = True
        connection.close()
        binding.close()


@contextmanager
def stream_reader(directory, *, audit=False):
    """Read-only, nonrepairing observer; explicit audit refuses a live owner."""
    binding = connection = None
    try:
        if type(audit) is not bool:
            raise _invalid()
        binding = _Binding(directory)
        binding.open_files(create=False, writer=False, audit=audit)
        connection = _connect(binding, observer=True)
        connection.execute('BEGIN')
        snapshot = _read_snapshot(connection)
        binding.check(observer=True, stable=True)
        connection.rollback()
        binding.check(observer=True, stable=True)
        reader = _Reader(binding, connection, snapshot, audit=audit)
    except BaseException as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            binding.close()
        if not isinstance(error, Exception):
            raise
        raise _invalid() from error
    try:
        yield reader
    finally:
        reader._broken = True
        connection.close()
        binding.close()


def read_stream(directory):
    """Bounded head structure/integrity observation, not a complete economic audit."""
    with stream_reader(directory) as reader:
        return reader.read()
