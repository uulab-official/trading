"""Private configuration and retained evidence for one existing PAPER pair.

This layer never repairs, launches, rebinds or changes either native database.
Locks coordinate cooperating owners; pinned names detect observed replacement,
not privileged changes that coherently replace all local evidence.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import sys
import time

from trading_data.upbit_history import parse_end
from . import paper_stream as stream_runtime
from . import paper_stream_store as stream_store
from .paper_stream import _Blocked, _SourceBinding, _Stopped, _verify
from .paper_backup import _Destination
from .paper_backup_io import (
    BackupInterrupted, BackupStorageError, BackupUnavailable, PrivateDirectory, write_file,
)

MAX_CONFIG_BYTES = 16 * 1024
MAX_STARTED_BYTES = 16 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_ATTEMPTS = 64
_ROOT_NAMES = {'.service.lock', 'service.json', 'attempts'}
_ATTEMPT_NAMES = {'started.json', 'result.json', 'capture.stderr', 'stream.stderr', 'health.sock'}
_EVIDENCE_LIMITS = {'result.json': MAX_RESULT_BYTES, 'capture.stderr': MAX_STDERR_BYTES,
                    'stream.stderr': MAX_STDERR_BYTES}
_PAIR_KEYS = {'account_path', 'capture_path', 'stream_id', 'capture_id', 'manifest_sha256',
              'execution_fingerprint', 'account_binding', 'capture_binding'}
_REPOSITORY = Path(__file__).resolve().parents[3]


class ServiceUnavailable(ValueError):
    """An input, private path, owner or stable observation is unavailable."""


class ServiceRefused(ServiceUnavailable):
    """Preserved policy evidence prevents a new supervised attempt."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__('paper_service_refused')


class ServiceStorageError(OSError):
    """Output may be incomplete or unconfirmed; preserve all artifacts."""


class ServiceInterrupted(Exception):
    """Cooperative interruption; any possible output must be retained."""

    def __init__(self, signum):
        self.signum = signum
        super().__init__('paper_service_interrupted')


def _unavailable():
    return ServiceUnavailable('paper_service_unavailable')


def _storage():
    return ServiceStorageError('paper_service_storage_unconfirmed')


def _stop(stop):
    if stop is not None and not callable(stop):
        raise _unavailable()
    value = stop() if stop is not None else 0
    if type(value) is not int or value not in (0, 2, 15):
        raise _unavailable()
    if value:
        raise ServiceInterrupted(value)


@contextmanager
def _errors(mutated=None):
    mutated = mutated if mutated is not None else [False]
    try:
        yield
    except (ServiceInterrupted, ServiceStorageError):
        raise
    except (BackupInterrupted, _Stopped) as error:
        raise ServiceInterrupted(error.signum) from None
    except BackupStorageError as error:
        raise _storage() from error
    except _Blocked as error:
        if mutated[0]:
            raise _storage() from error
        raise ServiceRefused(error.reason) from None
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError, OverflowError,
            RecursionError, UnicodeError) as error:
        if mutated[0]:
            raise _storage() from error
        if isinstance(error, ServiceUnavailable):
            raise
        raise _unavailable() from error


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise _unavailable()


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise _unavailable()


def _hex(value, length=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise _unavailable()


def _path(value):
    if (type(value) is not str or not 1 <= len(value.encode('utf-8')) <= 4096
            or not os.path.isabs(value) or os.path.normpath(value) != value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise _unavailable()


def _encode(value, limit):
    if type(value) is not dict:
        raise _unavailable()
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as error:
        raise _unavailable() from error
    if not 2 <= len(raw) <= limit:
        raise _unavailable()
    return raw


def _decode(raw, limit):
    if type(raw) is not bytes or not 2 <= len(raw) <= limit:
        raise _unavailable()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise _unavailable()
            result[key] = value
        return result
    def integer(value):
        if len(value) > 21:
            raise _unavailable()
        return int(value)
    def constant(value):
        raise _unavailable()
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_int=integer, parse_constant=constant)
        if _encode(value, limit) != raw:
            raise _unavailable()
        return value
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as error:
        raise _unavailable() from error


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _unavailable()
    stamp = value.astimezone(timezone.utc).isoformat()
    parse_end(stamp)
    return stamp


def _saved_stamp(value):
    if type(value) is not str or _stamp(parse_end(value)) != value:
        raise _unavailable()
    return parse_end(value)


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]


def _content(fd):
    info = os.fstat(fd)
    return (*_identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _parent(directory):
    directory.check()
    return str(directory.path.resolve(strict=True)), tuple(_identity(os.fstat(directory.directory_fd)))


def _binding(owner):
    binding = owner.binding
    result = {name: _identity(os.fstat(getattr(binding, name + '_fd')))
              for name in ('parent', 'directory', 'database', 'lock')}
    result['parent_path'] = str(binding.path.parent.resolve(strict=True))
    return result


def _validate_binding(value, path):
    _fields(value, {'parent_path', 'parent', 'directory', 'database', 'lock'})
    if value['parent_path'] != str(Path(path).parent):
        raise _unavailable()
    for name in ('parent', 'directory', 'database', 'lock'):
        entry = value[name]
        if type(entry) is not list or len(entry) != 5:
            raise _unavailable()
        for number in entry:
            _integer(number, 0, 2**64 - 1)
        is_directory = name in ('parent', 'directory')
        if not entry[1] or not (stat.S_ISDIR(entry[2]) if is_directory else stat.S_ISREG(entry[2])):
            raise _unavailable()
        if name != 'parent' and (entry[3] != os.geteuid()
                or stat.S_IMODE(entry[2]) != (0o700 if is_directory else 0o600)):
            raise _unavailable()


def _validate_pair(pair):
    _fields(pair, _PAIR_KEYS)
    for name in ('account', 'capture'):
        _path(pair[name + '_path'])
        _validate_binding(pair[name + '_binding'], pair[name + '_path'])
    _hex(pair['stream_id'], 32)
    _hex(pair['capture_id'], 32)
    _hex(pair['manifest_sha256'])
    _hex(pair['execution_fingerprint'])
    return pair


def _runtime():
    return dict(python=str(Path(sys.executable).absolute()), repository=str(_REPOSITORY),
                implementation=sys.implementation.name, major_minor=list(sys.version_info[:2]))


def _policy(startup, heartbeat, stop):
    _integer(startup, 10, 900)
    _integer(heartbeat, 5, 120)
    _integer(stop, 1, 60)
    return dict(startup_seconds=startup, heartbeat_seconds=heartbeat, stop_seconds=stop,
                max_attempts=MAX_ATTEMPTS, auto_restart=False)


def _validate_manifest(value):
    _fields(value, {'schema_version', 'source', 'service_id', 'mode', 'live_enabled',
                    'created_at', 'pair', 'runtime', 'policy'})
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['source'] != 'paper_service_config' or value['mode'] != 'paper'
            or value['live_enabled'] is not False):
        raise _unavailable()
    _hex(value['service_id'], 32)
    _saved_stamp(value['created_at'])
    _validate_pair(value['pair'])
    runtime = value['runtime']
    _fields(runtime, {'python', 'repository', 'implementation', 'major_minor'})
    _path(runtime['python'])
    _path(runtime['repository'])
    if (type(runtime['implementation']) is not str
            or re.fullmatch('[a-z][a-z0-9_]{0,31}', runtime['implementation']) is None
            or type(runtime['major_minor']) is not list or len(runtime['major_minor']) != 2):
        raise _unavailable()
    for number in runtime['major_minor']:
        _integer(number, 0, 999)
    policy = value['policy']
    _fields(policy, {'startup_seconds', 'heartbeat_seconds', 'stop_seconds', 'max_attempts', 'auto_restart'})
    expected = _policy(policy['startup_seconds'], policy['heartbeat_seconds'], policy['stop_seconds'])
    if (type(policy['max_attempts']) is not int or policy['max_attempts'] != MAX_ATTEMPTS
            or policy['auto_restart'] is not False or _encode(policy, MAX_CONFIG_BYTES) != _encode(expected, MAX_CONFIG_BYTES)):
        raise _unavailable()
    return value


def _validate_audit(audit, stream_id):
    _fields(audit, {'schema_version', 'source', 'mode', 'live_enabled', 'read_only', 'status',
                   'exit_code', 'stream_id', 'events_verified', 'engine_sha256',
                   'event_head_sha256', 'boundaries'})
    if (type(audit['schema_version']) is not int or audit['schema_version'] != 1
            or audit['source'] != 'continuous_paper_audit' or audit['mode'] != 'paper'
            or audit['live_enabled'] is not False or audit['read_only'] is not True
            or audit['status'] != 'verified_prefix' or type(audit['exit_code']) is not int
            or audit['exit_code'] != 0 or audit['stream_id'] != stream_id):
        raise _unavailable()
    _integer(audit['events_verified'], 0, stream_store.MAX_BARS)
    _hex(audit['engine_sha256'])
    _hex(audit['event_head_sha256'])
    _fields(audit['boundaries'], {'provider_authenticity', 'live_orders', 'verification'})
    if (audit['boundaries']['provider_authenticity'] != 'not_authenticated'
            or audit['boundaries']['live_orders'] is not False
            or audit['boundaries']['verification'] != 'full_archived_replay'):
        raise _unavailable()


class _Pair:
    """Keep native account audit and capture shared owners through publication."""

    def __init__(self, account, expected=None):
        self._stack = ExitStack()
        self._last_time = None
        try:
            self.owner = self._stack.enter_context(stream_store.stream_reader(account, audit=True))
            self.account = self.owner.read()
            manifest = self.account['manifest']
            self.source = _SourceBinding(manifest['capture']['path'], manifest['capture']['binding'])
            self._stack.callback(self.source.close)
            fcntl.flock(self.source.fds['lock'], fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.capture = self.source.read()
            self._capture_content = _content(self.source.fds['database'])
            if (self.capture['state']['capture_id'] != manifest['capture']['capture_id']
                    or self.capture['config'] != manifest['capture']['config']):
                raise ServiceRefused('source_identity_changed')
            self.pair = _validate_pair(dict(account_path=str(self.owner.binding.path.resolve(strict=True)),
                capture_path=manifest['capture']['path'], stream_id=manifest['stream_id'],
                capture_id=self.capture['state']['capture_id'], manifest_sha256=stream_store.digest(manifest),
                execution_fingerprint=manifest['engine_sha256'], account_binding=_binding(self.owner),
                capture_binding=deepcopy(self.source.identity)))
            if expected is not None:
                _validate_pair(expected)
                if _encode(expected, MAX_CONFIG_BYTES) != _encode(self.pair, MAX_CONFIG_BYTES):
                    raise ServiceRefused('pair_changed')
            self.check()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._stack.close()

    def check(self):
        self.owner._ensure()
        self.source.check()
        if (_content(self.source.fds['database']) != self._capture_content
                or self.owner.read() != self.account or self.source.read() != self.capture):
            raise _unavailable()
        control, state = self.account['control'], self.capture['state']
        if control['phase'] == 'blocked' or control['last_error'] in stream_runtime._TERMINAL_STREAM:
            raise ServiceRefused(control['last_error'] or 'account_blocked')
        if state['phase'] == 'blocked' or state['last_error'] in stream_runtime._TERMINAL_SOURCE:
            raise ServiceRefused(state['last_error'] or 'capture_blocked')
        if state['phase'] == 'fetching':
            raise ServiceRefused('capture_fetching')
        if (self.account['engine']['next_index'] >= self.account['manifest']['max_bars']
                or os.fstat(self.owner.binding.database_fd).st_size >= stream_store.MAX_DATABASE_BYTES):
            raise ServiceRefused('capacity')
        now = parse_end(_stamp(_now()))
        dates = [self.account['manifest']['created_at'], control['updated_at'],
                 control['source']['last_verified_at']]
        dates.extend(state[name] for name in ('created_at', 'updated_at', 'last_attempt_at', 'last_verified_at')
                     if state[name] is not None)
        if ((self._last_time is not None and now < self._last_time)
                or any(parse_end(value) > now for value in dates)):
            raise ServiceRefused('clock_regression')
        self._last_time = now
        if stream_runtime._engine_fingerprint() != self.pair['execution_fingerprint']:
            raise ServiceRefused('execution_fingerprint_changed')

    async def prepare(self, stop):
        _stop(stop)
        audit = await _verify(self.owner, self.account, stop)
        _validate_audit(audit, self.pair['stream_id'])
        self.check()
        _stop(stop)
        return dict(pair=deepcopy(self.pair), audit=audit)


async def prepare_pair(account, *, expected=None, stop=None):
    """Fully replay one quiescent original pair, preserving both input stores."""
    with _errors():
        _stop(stop)
        with _Pair(account, expected) as prepared:
            return await prepared.prepare(stop)


async def configure_service(account, directory, *, startup_seconds=180,
                            heartbeat_seconds=30, stop_seconds=15, stop=None):
    """Publish new inert configuration while original native audit owners stay held."""
    mutated = [False]
    with _errors(mutated):
        _stop(stop)
        policy = _policy(startup_seconds, heartbeat_seconds, stop_seconds)
        with _Pair(account) as pair:
            with _Destination(directory, (pair.owner.binding.path, pair.source.path)) as destination:
                await pair.prepare(stop)
                def unchanged():
                    pair.check()
                    destination.check()
                unchanged()
                manifest = _validate_manifest(dict(schema_version=1, source='paper_service_config',
                    service_id=secrets.token_hex(16), mode='paper', live_enabled=False,
                    created_at=_stamp(_now()), pair=deepcopy(pair.pair), runtime=_runtime(), policy=policy))
                data = _encode(manifest, MAX_CONFIG_BYTES)
                _stop(stop)
                with PrivateDirectory(destination.path, create=True,
                                      expected_parent=destination.expected_parent) as root:
                    mutated[0] = True
                    root.lock('.service.lock', exclusive=True, create=True)
                    with PrivateDirectory(root.path / 'attempts', create=True,
                                          expected_parent=_parent(root)) as attempts:
                        attempts.check_names(set())
                        attempts.sync()
                        write_file(root, 'service.json', data, MAX_CONFIG_BYTES,
                                   stop=stop, check=unchanged)
                        root.check_names(_ROOT_NAMES)
                        root.sync()
                        unchanged()
                        attempts.check_names(set())
                        root.check_names(_ROOT_NAMES)
                        for name in ('.service.lock', 'service.json'):
                            root.check_file(name)
                        _stop(stop)
                        return dict(schema_version=1, source='paper_service', operation='configure',
                            mode='paper', live_enabled=False, read_only=False, status='configured',
                            exit_code=0, service_id=manifest['service_id'], attempt_id=None, children={},
                            boundaries=dict(data_readiness='not_assessed', live_execution=False))


def _read_document(directory, name, limit):
    fd = directory.open_file(name, limit=limit, minimum=2)
    raw = os.pread(fd, limit + 1, 0)
    directory.check_file(name)
    return _decode(raw, limit), raw


def _inventory(directory, limit):
    directory.check()
    names = set()
    with os.scandir(directory.directory_fd) as entries:
        for entry in entries:
            if len(names) >= limit:
                raise _unavailable()
            names.add(entry.name)
    directory.check()
    return names


class ServiceDirectory:
    """Hold immutable bindings; explicit EX admission waits at most one second.

Only contention on the service lease is retried. Native account/capture owners
remain immediate refusals, and no attempt or runtime operation begins here.
"""

    def __init__(self, directory, *, exclusive=False, stop=None):
        self.directory = self.attempts = None
        self._opened = {}
        self.exclusive = exclusive
        self._manifest = None
        with _errors():
            try:
                _stop(stop)
                if type(exclusive) is not bool:
                    raise _unavailable()
                self.directory = PrivateDirectory(directory)
                self.path = self.directory.path
                self.directory.open_file('.service.lock', limit=0)
                self.directory.check_names(_ROOT_NAMES)
                self._manifest, raw = _read_document(self.directory, 'service.json', MAX_CONFIG_BYTES)
                _validate_manifest(self._manifest)
                self.config_sha256 = hashlib.sha256(raw).hexdigest()
                self.attempts = PrivateDirectory(self.path / 'attempts', expected_parent=_parent(self.directory))
                if exclusive:
                    self._acquire_lease(stop)
                self.check()
                _stop(stop)
            except BaseException:
                self.close()
                raise

    def _acquire_lease(self, stop):
        deadline = time.monotonic() + 1.0
        while True:
            _stop(stop)
            self._base_check()
            # Check before attempting, including after a descheduled sleep.
            if time.monotonic() >= deadline:
                raise _unavailable()
            try:
                self.directory.lock('.service.lock', exclusive=True)
            except BackupUnavailable as error:
                cause = error.__cause__
                if (not isinstance(cause, BlockingIOError)
                        or cause.errno not in (errno.EAGAIN, errno.EWOULDBLOCK)):
                    raise
                self._base_check()
                _stop(stop)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _unavailable() from error
                time.sleep(min(.01, remaining))
            else:
                _stop(stop)
                self._base_check()
                return

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    def __enter__(self):
        try:
            self.check()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()

    def close(self):
        for attempt in reversed(list(self._opened.values())):
            attempt.close()
        self._opened.clear()
        for name in ('attempts', 'directory'):
            owner = getattr(self, name, None)
            if owner is not None:
                owner.close()
                setattr(self, name, None)

    def _base_check(self):
        if self.directory is None or self.attempts is None:
            raise _unavailable()
        self.directory.check_names(_ROOT_NAMES)
        self.directory.check_file('.service.lock')
        self.directory.check_file('service.json')
        self.attempts.check()

    def _names(self):
        self._base_check()
        names = _inventory(self.attempts, MAX_ATTEMPTS)
        if names != {f'{number:06d}' for number in range(1, len(names) + 1)}:
            raise _unavailable()
        return sorted(names)

    def _attempt(self, name):
        if name not in self._opened:
            self._opened[name] = Attempt(self, name)
        return self._opened[name]

    def check(self):
        with _errors():
            names = self._names()
            if set(self._opened) - set(names):
                raise _unavailable()
            for name in names:
                self._attempt(name).check()
            self._base_check()

    def owner_present(self):
        """Probe a fresh file description; never unlock an inherited owner FD."""
        with _errors():
            self.check()
            fd = os.open('.service.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self.directory.directory_fd)
            try:
                if _content(fd) != _content(self.directory.files['.service.lock']):
                    raise _unavailable()
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    result = True
                else:
                    result = False
                self.check()
                return result
            finally:
                os.close(fd)

    def latest_attempt(self):
        with _errors():
            self.check()
            names = self._names()
            return self._attempt(names[-1]) if names else None

    def new_attempt(self, audit):
        mutated = [False]
        with _errors(mutated):
            self.check()
            if not self.exclusive:
                raise _unavailable()
            _validate_audit(audit, self._manifest['pair']['stream_id'])
            names = self._names()
            if names and not self._attempt(names[-1])._confirmed():
                raise ServiceRefused('previous_attempt_unconfirmed')
            if len(names) >= MAX_ATTEMPTS:
                raise ServiceRefused('attempt_capacity')
            stamp = _stamp(_now())
            floor = self._manifest['created_at']
            if names:
                floor = max(parse_end(floor), parse_end(self._attempt(names[-1]).record['started_at']))
            else:
                floor = parse_end(floor)
            if parse_end(stamp) < floor:
                raise ServiceRefused('clock_regression')
            number = len(names) + 1
            name = f'{number:06d}'
            record = dict(schema_version=1, source='paper_service_attempt',
                service_id=self._manifest['service_id'], attempt_id=secrets.token_hex(16),
                number=number, config_sha256=self.config_sha256, started_at=stamp, audit=deepcopy(audit))
            raw = _encode(record, MAX_STARTED_BYTES)
            directory = PrivateDirectory(self.attempts.path / name, create=True,
                                         expected_parent=_parent(self.attempts))
            try:
                mutated[0] = True
                write_file(directory, 'started.json', raw, MAX_STARTED_BYTES, check=self._base_check)
                directory.check_names({'started.json'})
                directory.sync()
                self._base_check()
                self.directory.sync()
                self._base_check()
                directory.check_names({'started.json'})
                attempt = Attempt(self, name, _directory=directory)
                self._opened[name] = attempt
                directory = None  # The returned Attempt retains these exact descriptors.
                self.check()
                return attempt
            finally:
                if directory is not None:
                    directory.close()


class Attempt:
    """Retain one immutable start and possible final evidence under held names."""

    def __init__(self, service, name, *, _directory=None):
        self.service = service
        self.directory = None
        self._socket_fd = None
        self._socket_identity = None
        self._listener = None
        self._record = None
        with _errors():
            try:
                service._base_check()
                if re.fullmatch(r'0000(?:[0-5][0-9]|6[0-4])', name) is None or name == '000000':
                    raise _unavailable()
                self.number = int(name)
                self.directory = _directory if _directory is not None else PrivateDirectory(
                    service.attempts.path / name, expected_parent=_parent(service.attempts))
                if self.directory.path != service.attempts.path / name:
                    raise _unavailable()
                self.directory.check()
                self.path = self.directory.path
                self._record, _ = _read_document(self.directory, 'started.json', MAX_STARTED_BYTES)
                record = self._record
                _fields(record, {'schema_version', 'source', 'service_id', 'attempt_id',
                                 'number', 'config_sha256', 'started_at', 'audit'})
                if (type(record['schema_version']) is not int or record['schema_version'] != 1
                        or record['source'] != 'paper_service_attempt'
                        or record['service_id'] != service._manifest['service_id']
                        or type(record['number']) is not int or record['number'] != self.number
                        or record['config_sha256'] != service.config_sha256):
                    raise _unavailable()
                _hex(record['attempt_id'], 32)
                _saved_stamp(record['started_at'])
                if parse_end(record['started_at']) < parse_end(service._manifest['created_at']):
                    raise _unavailable()
                _validate_audit(record['audit'], service._manifest['pair']['stream_id'])
                self.attempt_id = record['attempt_id']
                self.check()
            except BaseException:
                self.close()
                raise

    @property
    def record(self):
        return deepcopy(self._record)

    def close(self):
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._socket_fd is not None:
            os.close(self._socket_fd)
            self._socket_fd = None
        if self.directory is not None:
            self.directory.close()
            self.directory = None

    def _binding_check(self):
        self.service._base_check()
        if self.directory is None:
            raise _unavailable()
        self.directory.check()
        self.directory.check_file('started.json')

    def _check_socket(self, required=False):
        self._binding_check()
        try:
            named = os.stat('health.sock', dir_fd=self.directory.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            if required or self._socket_fd is not None:
                raise _unavailable()
            return
        if (not stat.S_ISSOCK(named.st_mode) or stat.S_IMODE(named.st_mode) != 0o600
                or named.st_uid != os.geteuid() or named.st_nlink != 1):
            raise _unavailable()
        if self._socket_fd is None:
            self._socket_fd = os.open('health.sock', os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                                      dir_fd=self.directory.directory_fd)
            self._socket_identity = (*_identity(named), named.st_nlink)
        held = os.fstat(self._socket_fd)
        if ((*_identity(held), held.st_nlink) != self._socket_identity
                or (*_identity(named), named.st_nlink) != self._socket_identity):
            raise _unavailable()
        self._binding_check()

    def check(self):
        with _errors():
            self._binding_check()
            names = _inventory(self.directory, len(_ATTEMPT_NAMES))
            if 'started.json' not in names or names - _ATTEMPT_NAMES:
                raise _unavailable()
            for name, limit in _EVIDENCE_LIMITS.items():
                if name in names:
                    self.directory.open_file(name, limit=limit)
                    self.directory.check_file(name)
                elif name in self.directory.files:
                    raise _unavailable()
            self._check_socket()

    def health_address(self):
        with _errors():
            self.check()
            self._check_socket(required=True)
            return f'/proc/self/fd/{self.directory.directory_fd}/health.sock'

    def bind_health_socket(self):
        mutated = [False]
        with _errors(mutated):
            self.check()
            if not self.service.exclusive or self._listener is not None:
                raise _unavailable()
            try:
                os.stat('health.sock', dir_fd=self.directory.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _unavailable()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.setblocking(False)
                listener.set_inheritable(False)
                listener.bind(f'/proc/self/fd/{self.directory.directory_fd}/health.sock')
                mutated[0] = True
                before = os.stat('health.sock', dir_fd=self.directory.directory_fd, follow_symlinks=False)
                os.chmod('health.sock', 0o600, dir_fd=self.directory.directory_fd, follow_symlinks=False)
                after = os.stat('health.sock', dir_fd=self.directory.directory_fd, follow_symlinks=False)
                if ((before.st_dev, before.st_ino, before.st_uid, before.st_gid, before.st_nlink)
                        != (after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_nlink)):
                    raise _unavailable()
                self._check_socket(required=True)
                listener.listen(4)
                self.directory.sync()
                self.check()
                self._listener = listener
                return listener
            except BaseException:
                listener.close()
                raise

    def _validate_result(self, result):
        if (type(result) is not dict or type(result.get('schema_version')) is not int
                or result['schema_version'] != 1 or result.get('source') != 'paper_service'
                or result.get('operation') != 'run' or result.get('mode') != 'paper'
                or result.get('live_enabled') is not False
                or result.get('service_id') != self.service._manifest['service_id']
                or result.get('attempt_id') != self.attempt_id
                or type(result.get('cleanup_confirmed')) is not bool
                or type(result.get('exit_code')) is not int
                or result['exit_code'] not in (0, 2, 3, 4, 130, 143)
                or type(result.get('status')) is not str
                or re.fullmatch('[a-z][a-z0-9_]{0,63}', result['status']) is None):
            raise _unavailable()

    def _confirmed(self):
        self.check()
        names = _inventory(self.directory, len(_ATTEMPT_NAMES))
        if not set(_EVIDENCE_LIMITS) <= names:
            return False
        try:
            result, _ = _read_document(self.directory, 'result.json', MAX_RESULT_BYTES)
            self._validate_result(result)
        except (ValueError, OSError):
            return False
        return result['cleanup_confirmed'] is True

    def save_result(self, result, stderr):
        mutated = [False]
        with _errors(mutated):
            self.check()
            if not self.service.exclusive:
                raise _unavailable()
            self._validate_result(result)
            raw = _encode(result, MAX_RESULT_BYTES)
            _fields(stderr, {'capture', 'stream'})
            if any(type(data) is not bytes or len(data) > MAX_STDERR_BYTES for data in stderr.values()):
                raise _unavailable()
            if _inventory(self.directory, len(_ATTEMPT_NAMES)) & set(_EVIDENCE_LIMITS):
                raise _unavailable()
            for role in ('capture', 'stream'):
                write_file(self.directory, role + '.stderr', stderr[role], MAX_STDERR_BYTES,
                           check=self._binding_check)
                mutated[0] = True
            write_file(self.directory, 'result.json', raw, MAX_RESULT_BYTES, check=self._binding_check)
            self.directory.sync()
            self.service.directory.sync()
            self.check()
            self.service.check()
