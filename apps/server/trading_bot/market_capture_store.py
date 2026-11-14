"""Bounded transactional public-candle observations, independent of accounts.

Local POSIX storage; an advisory lock coordinates cooperating writers. Pinned
descriptors and name checks detect observed replacement, not a privileged future
filesystem race. A saved phase does not assert that any process is still alive.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

from trading_data.upbit_history import (_integer, _market, _milliseconds, _unit,
    candles_hash, decode_json, parse_end, timeframe, validate_market_fixture)

DB_NAME = 'capture.sqlite3'
LOCK_NAME = '.capture.lock'
APPLICATION_ID = 0x55434D31
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 4096
MAX_ROWS = 5000
MAX_SAFE_INTEGER = 2**53 - 1
MAX_VM_OPERATIONS = 1_000_000
MAX_OBSERVATION_SECONDS = 1.0
_CONFIG_FIELDS = {'market', 'unit_minutes', 'bootstrap_bars', 'retention_bars', 'poll_seconds'}
_FIXTURE_FIELDS = {'mode', 'symbol', 'timeframe', 'candles', 'data_provenance'}
_STATE_FIELDS = {'capture_id', 'revision', 'phase', 'created_at', 'updated_at',
    'last_attempt_at', 'last_verified_at', 'last_error', 'consecutive_failures',
    'successful_polls', 'accepted_bars_total', 'evicted_bars_total'}
_COUNTERS = ('revision', 'consecutive_failures', 'successful_polls',
             'accepted_bars_total', 'evicted_bars_total')
_PHASES = {'starting', 'fetching', 'running', 'retrying', 'blocked', 'stopped'}
_ERRORS = {'transport_unavailable', 'rate_limited', 'rate_blocked', 'request_rejected',
    'cooldown_required', 'data_rejected', 'history_conflict', 'catchup_limit', 'clock_regression'}
_RETRYABLE = {'transport_unavailable', 'rate_limited'}
_COLUMNS = ('open', 'high', 'low', 'close', 'volume')
_SCHEMA = {
    'capture_metadata': 'CREATE TABLE capture_metadata (singleton INTEGER PRIMARY KEY '
        'CHECK(singleton = 1), body TEXT NOT NULL CHECK(typeof(body) = \'text\' '
        'AND length(CAST(body AS BLOB)) <= 4096))',
    'candles': 'CREATE TABLE candles (open_ms INTEGER PRIMARY KEY '
        'CHECK(open_ms >= 0 AND open_ms <= 253402300799999), '
        + ', '.join(f'{name} TEXT NOT NULL CHECK(typeof({name}) = \'text\' '
                    f'AND length(CAST({name} AS BLOB)) <= 100)' for name in _COLUMNS) + ')',
}


class CaptureUnavailable(ValueError):
    """The capture is invalid, unavailable, or already has a cooperating owner."""


class CaptureConflict(CaptureUnavailable):
    """Incoming history cannot extend the committed contiguous history."""


class CaptureStorageError(OSError):
    """Storage confirmation failed; retain every artifact and inspect it."""


def _invalid():
    return CaptureUnavailable('market_capture_unavailable')


def validate_config(value):
    try:
        if not isinstance(value, dict) or set(value) != _CONFIG_FIELDS:
            raise ValueError('configuration fields')
        _market(value['market'])
        _unit(value['unit_minutes'])
        _integer(value['bootstrap_bars'], 1, 200)
        _integer(value['retention_bars'], value['bootstrap_bars'], MAX_ROWS)
        _integer(value['poll_seconds'], 5, 300)
        return dict(value)
    except (ValueError, TypeError, KeyError) as error:
        raise _invalid() from error


def _stamp(value):
    _milliseconds(value)
    return value.astimezone(timezone.utc).isoformat()


def _state_time(value):
    parsed = parse_end(value)
    if parsed.utcoffset().total_seconds() != 0 or value != parsed.isoformat():
        raise ValueError('noncanonical capture time')
    return parsed


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _identity(info):
    # Directory link counts legitimately change when creating a child capture.
    # Regular-file single-link ownership is checked separately by _private.
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _content(info):
    return _identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _directory_info(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise _invalid()
    return str(path.resolve(strict=True)), _identity(info)


def _private(info, *, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise _invalid()


class _Binding:
    """Hold parent, capture, database and permanent lock identities."""
    def __init__(self, directory, *, create=False):
        self.path = Path(directory).absolute()
        self.parent_fd = self.directory_fd = self.database_fd = self.lock_fd = None
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
            if _identity(os.fstat(self.directory_fd)) != self.directory_before[1]:
                raise _invalid()
            self._check_directory()
        except (OSError, ValueError) as error:
            self.close()
            if (create and isinstance(error, OSError)
                    and not isinstance(error, (FileExistsError, FileNotFoundError, PermissionError))):
                raise CaptureStorageError('market_capture_storage_unconfirmed') from error
            raise _invalid() from error

    def _check_directory(self):
        if (self.parent_before != _directory_info(self.path.parent)
                or _identity(os.fstat(self.parent_fd)) != self.parent_before[1]
                or self.directory_before != _directory_info(self.path)
                or _identity(os.fstat(self.directory_fd)) != self.directory_before[1]):
            raise _invalid()
        _private(os.fstat(self.directory_fd), directory=True)

    def open_files(self, *, create, writer):
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
        if writer:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        db = os.fstat(self.database_fd)
        if not (0 if empty else 100) <= db.st_size <= MAX_DATABASE_BYTES:
            raise _invalid()
        if os.fstat(self.lock_fd).st_size != 0:
            raise _invalid()
        if stable and _content(db) != self.content_before:
            raise _invalid()
        if not empty:
            header = os.pread(self.database_fd, 100, 0)
            if header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
                raise _invalid()
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                sidecar = os.stat(DB_NAME+suffix, dir_fd=self.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _private(sidecar)
            if sidecar.st_size > MAX_DATABASE_BYTES or suffix != '-journal':
                raise _invalid()
            # Observers never perform SQLite rollback recovery, including a
            # conservative refusal of an active writer's not-yet-hot journal.
            if observer and sidecar.st_size:
                raise _invalid()

    def close(self):
        for name in ('database_fd', 'lock_fd', 'directory_fd', 'parent_fd'):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)


def _connect(binding, *, observer, empty=False):
    connection = sqlite3.connect((binding.path/DB_NAME).as_uri() + ('?mode=ro' if observer else '?mode=rw'),
                                 uri=True, timeout=0.2, isolation_level=None)
    try:
        # sqlite3 opens by name, separately from our O_NOFOLLOW descriptor.
        # Confirm that observable binding before any connection setting could
        # read/recover or initialise a database substituted during connect.
        binding.check(observer=observer, empty=empty)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-2048')
        if observer:
            connection.execute('PRAGMA query_only=ON')
        else:
            connection.execute('PRAGMA synchronous=FULL')
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
            or connection.execute('PRAGMA page_count').fetchone()[0] > MAX_DATABASE_BYTES//4096
            or connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete'):
        raise _invalid()
    objects = connection.execute('SELECT type, substr(name,1,65), substr(tbl_name,1,65), '
        'substr(sql,1,4097), length(CAST(sql AS BLOB)) FROM sqlite_schema LIMIT 4').fetchall()
    if len(objects) != 2:
        raise _invalid()
    for kind, name, table, sql, length in objects:
        if (kind != 'table' or name != table or name not in _SCHEMA
                or sql != _SCHEMA[name] or length != len(_SCHEMA[name])):
            raise _invalid()


def _validate_snapshot(snapshot):
    config = validate_config(snapshot['config'])
    state, fixture = snapshot['state'], snapshot['fixture']
    if (snapshot['schema_version'] != 1 or type(snapshot['schema_version']) is not int
            or not isinstance(state, dict) or set(state) != _STATE_FIELDS
            or not isinstance(state['capture_id'], str)
            or not re.fullmatch('[0-9a-f]{32}', state['capture_id']) or state['phase'] not in _PHASES):
        raise _invalid()
    for key in _COUNTERS:
        _integer(state[key], 0, MAX_SAFE_INTEGER)
    created, updated = _state_time(state['created_at']), _state_time(state['updated_at'])
    if updated < created:
        raise _invalid()
    for field in ('last_attempt_at', 'last_verified_at'):
        if state[field] is not None and not created <= _state_time(state[field]) <= updated:
            raise _invalid()
    error, failures = state['last_error'], state['consecutive_failures']
    if ((error is not None and error not in _ERRORS) or (error is None) != (failures == 0)
            or state['successful_polls'] + failures > state['revision']):
        raise _invalid()
    phase = state['phase']
    if (state['revision'] == 0) != (phase == 'starting'):
        raise _invalid()
    if phase == 'starting' and (state['updated_at'] != state['created_at'] or state['last_attempt_at'] is not None):
        raise _invalid()
    if phase == 'fetching' and state['last_attempt_at'] is None:
        raise _invalid()
    if phase in {'blocked', 'retrying'} and error is None:
        raise _invalid()
    if phase == 'retrying' and error not in _RETRYABLE:
        raise _invalid()
    if phase == 'running' and (fixture is None or error is not None):
        raise _invalid()
    if fixture is None:
        if (any(state[field] for field in ('successful_polls', 'accepted_bars_total', 'evicted_bars_total'))
                or state['last_verified_at'] is not None):
            raise _invalid()
    else:
        if not isinstance(fixture, dict) or set(fixture) != _FIXTURE_FIELDS:
            raise _invalid()
        rows = fixture['candles']
        if not isinstance(rows, list) or not 1 <= len(rows) <= config['retention_bars']:
            raise _invalid()
        p = validate_market_fixture(fixture)
        if (p['market'] != config['market'] or p['unit_minutes'] != config['unit_minutes']
                or _stamp(parse_end(p['retrieved_at'])) != state['last_verified_at']
                or state['successful_polls'] < 1
                or state['accepted_bars_total'] < config['bootstrap_bars']
                or len(rows) + state['evicted_bars_total'] != state['accepted_bars_total']
                or (state['evicted_bars_total'] and len(rows) != config['retention_bars'])
                or phase == 'starting'):
            raise _invalid()
        if (phase == 'running' and state['last_attempt_at'] is not None
                and _state_time(state['last_attempt_at']) > _state_time(state['last_verified_at'])):
            raise _invalid()
    return snapshot


def _read_snapshot(connection):
    with _budget(connection):
        _check_schema(connection)
        metadata = connection.execute('SELECT singleton, typeof(body), length(CAST(body AS BLOB)), '
            'substr(body,1,4097) FROM capture_metadata LIMIT 2').fetchall()
        if (len(metadata) != 1 or metadata[0][0] != 1 or metadata[0][1] != 'text'
                or not 1 <= metadata[0][2] <= MAX_METADATA_BYTES):
            raise _invalid()
        value = decode_json(metadata[0][3], decimal_numbers=False)
        if not isinstance(value, dict) or set(value) != {'schema_version', 'config', 'state', 'provenance'}:
            raise _invalid()
        config = validate_config(value['config'])
        columns = ', '.join(f'substr({name},1,101), typeof({name}), length(CAST({name} AS BLOB))'
                            for name in _COLUMNS)
        stored = connection.execute('SELECT open_ms, '+columns+' FROM candles ORDER BY open_ms LIMIT ?',
                                    (config['retention_bars'] + 1,)).fetchall()
        if len(stored) > config['retention_bars']:
            raise _invalid()
        rows = []
        for stored_row in stored:
            row = [stored_row[0]]
            for index in range(1, len(stored_row), 3):
                text, kind, length = stored_row[index:index+3]
                if kind != 'text' or not 1 <= length <= 100:
                    raise _invalid()
                row.append(text)
            rows.append(row)
        fixture = None
        if value['provenance'] is not None:
            fixture = dict(mode='paper', symbol=config['market'][4:]+'/KRW',
                timeframe=timeframe(config['unit_minutes']), candles=rows, data_provenance=value['provenance'])
        elif rows:
            raise _invalid()
        return _validate_snapshot(dict(schema_version=value['schema_version'], config=config,
                                       state=value['state'], fixture=fixture))


def _write_snapshot(connection, snapshot, *, replace_rows):
    fixture = snapshot['fixture']
    body = _json(dict(schema_version=1, config=snapshot['config'], state=snapshot['state'],
                      provenance=fixture['data_provenance'] if fixture else None))
    if len(body.encode('utf-8')) > MAX_METADATA_BYTES:
        raise _invalid()
    if replace_rows:
        connection.execute('DELETE FROM candles')
        if fixture:
            connection.executemany('INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?)', fixture['candles'])
    connection.execute('INSERT INTO capture_metadata(singleton,body) VALUES (1,?) '
                       'ON CONFLICT(singleton) DO UPDATE SET body=excluded.body', (body,))


def read_capture(directory):
    binding, connection = None, None
    try:
        binding = _Binding(directory)
        binding.open_files(create=False, writer=False)
        connection = _connect(binding, observer=True)
        connection.execute('BEGIN')
        snapshot = _read_snapshot(connection)
        binding.check(observer=True, stable=True)
        connection.rollback()
        binding.check(observer=True, stable=True)
        return snapshot
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as error:
        raise _invalid() from error
    finally:
        if connection is not None:
            connection.close()
        if binding is not None:
            binding.close()


class _Writer:
    def __init__(self, binding, connection, snapshot):
        self.binding, self.connection = binding, connection
        self.capture_id, self.config = snapshot['state']['capture_id'], snapshot['config']

    def _read(self):
        snapshot = _read_snapshot(self.connection)
        if snapshot['state']['capture_id'] != self.capture_id or snapshot['config'] != self.config:
            raise _invalid()
        return snapshot

    def read(self):
        try:
            self.binding.check()
            self.connection.execute('BEGIN')
            snapshot = self._read()
            self.binding.check()
            return snapshot
        except (sqlite3.Error, OSError) as error:
            raise CaptureStorageError('market_capture_storage_unconfirmed') from error
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            raise _invalid() from error
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()

    def _mutate(self, at, change, *, replace_rows=False):
        publication_attempted = False
        try:
            stamp = _stamp(at)
            self.binding.check()
            self.connection.execute('BEGIN IMMEDIATE')
            snapshot = self._read()
            state = snapshot['state']
            if parse_end(stamp) < _state_time(state['updated_at']):
                raise _invalid()
            state['revision'] += 1
            state['updated_at'] = stamp
            change(snapshot)
            _validate_snapshot(snapshot)
            _write_snapshot(self.connection, snapshot, replace_rows=replace_rows)
            self.binding.check()
            publication_attempted = True
            self.connection.commit()
            os.fsync(self.binding.directory_fd)
            self.binding.check()
        except CaptureConflict:
            raise
        except (sqlite3.Error, OSError) as error:
            raise CaptureStorageError('market_capture_storage_unconfirmed') from error
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            if publication_attempted:
                raise CaptureStorageError('market_capture_storage_unconfirmed') from error
            raise _invalid() from error
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()

    def attempt(self, at):
        def change(snapshot):
            snapshot['state'].update(phase='fetching', last_attempt_at=_stamp(at))
        self._mutate(at, change)

    def apply(self, fixture, *, at):
        def change(snapshot):
            if (not isinstance(fixture, dict) or set(fixture) != _FIXTURE_FIELDS
                    or not isinstance(fixture['candles'], list) or not 1 <= len(fixture['candles']) <= 200):
                raise _invalid()
            p = validate_market_fixture(fixture)
            state, config = snapshot['state'], snapshot['config']
            verified = parse_end(p['retrieved_at']).astimezone(timezone.utc)
            if (p['market'] != config['market'] or p['unit_minutes'] != config['unit_minutes']
                    or not _state_time(state['created_at']) <= verified <= at.astimezone(timezone.utc)
                    or (state['last_verified_at'] is not None and verified < _state_time(state['last_verified_at']))
                    or (state['last_attempt_at'] is not None and verified < _state_time(state['last_attempt_at']))):
                raise _invalid()
            previous = snapshot['fixture']
            incoming = {row[0]: list(row) for row in fixture['candles']}
            if previous is None:
                if len(incoming) != config['bootstrap_bars']:
                    raise _invalid()
                combined, added = list(incoming.values()), len(incoming)
            else:
                old = previous['candles']
                if (old[-1][0] not in incoming or p['end_exclusive_ms'] < previous['data_provenance']['end_exclusive_ms']
                        or any(row[0] in incoming and incoming[row[0]] != row for row in old)):
                    raise CaptureConflict('market_capture_history_conflict')
                appended = [row for row in incoming.values() if row[0] > old[-1][0]]
                combined, added = old + appended, len(appended)
            evicted = max(0, len(combined)-config['retention_bars'])
            rows = combined[evicted:]
            provenance = dict(p, bar_count=len(rows), candles_sha256=candles_hash(rows))
            snapshot['fixture'] = dict(mode='paper', symbol=fixture['symbol'], timeframe=fixture['timeframe'],
                                      candles=rows, data_provenance=provenance)
            state.update(phase='running', last_verified_at=_stamp(verified), last_error=None,
                         consecutive_failures=0, successful_polls=state['successful_polls']+1,
                         accepted_bars_total=state['accepted_bars_total']+added,
                         evicted_bars_total=state['evicted_bars_total']+evicted)
        self._mutate(at, change, replace_rows=True)

    def fail(self, reason, *, at, retryable=False):
        if (type(retryable) is not bool or not isinstance(reason, str) or reason not in _ERRORS
                or (retryable and reason not in _RETRYABLE)):
            raise _invalid()
        def change(snapshot):
            state = snapshot['state']
            state.update(phase='retrying' if retryable else 'blocked', last_error=reason,
                         consecutive_failures=state['consecutive_failures']+1)
        self._mutate(at, change)

    def stop(self, at):
        self._mutate(at, lambda snapshot: snapshot['state'].update(phase='stopped'))


@contextmanager
def capture_writer(directory, *, config=None, now=None):
    """Create a new capture with config, or explicitly resume the existing one."""
    binding, connection, create, publication_attempted = None, None, False, False
    try:
        create = config is not None
        if create:
            config = validate_config(config)
            stamp = _stamp(now if now is not None else datetime.now(timezone.utc))
        elif now is not None:
            _stamp(now)
        binding = _Binding(directory, create=create)
        binding.open_files(create=create, writer=True)
        connection = _connect(binding, observer=False, empty=create)
        binding.check(empty=create)
        if create:
            connection.execute('PRAGMA page_size=4096')
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//4096))
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('PRAGMA application_id='+str(APPLICATION_ID))
            connection.execute('PRAGMA user_version=1')
            for sql in _SCHEMA.values():
                connection.execute(sql)
            state = dict(capture_id=secrets.token_hex(16), revision=0, phase='starting',
                created_at=stamp, updated_at=stamp, last_attempt_at=None, last_verified_at=None,
                last_error=None, consecutive_failures=0, successful_polls=0,
                accepted_bars_total=0, evicted_bars_total=0)
            snapshot = dict(schema_version=1, config=config, state=state, fixture=None)
            _write_snapshot(connection, snapshot, replace_rows=True)
            binding.check(empty=True)
            publication_attempted = True
            connection.commit()
            os.fsync(binding.directory_fd)
        binding.check()
        connection.execute('BEGIN')
        snapshot = _read_snapshot(connection)
        connection.rollback()
        binding.check()
        # Keep the same on-disk size ceiling for a resumed writer connection.
        connection.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//4096))
        writer = _Writer(binding, connection, snapshot)
    except CaptureStorageError:
        if connection is not None: connection.close()
        if binding is not None: binding.close()
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError) as error:
        if connection is not None: connection.close()
        if binding is not None: binding.close()
        if (publication_attempted or isinstance(error, sqlite3.OperationalError)
                or (isinstance(error, OSError)
                    and not isinstance(error, (FileExistsError, FileNotFoundError, PermissionError)))
                or (create and isinstance(error, sqlite3.Error))):
            raise CaptureStorageError('market_capture_storage_unconfirmed') from error
        raise _invalid() from error
    try:
        yield writer
    finally:
        connection.close()
        binding.close()
