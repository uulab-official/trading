"""Immutable paired PAPER archives; recovery verification never authorizes execution.

All execution-fingerprinted source files stay unchanged. Fixed binary payload
names intentionally cannot be opened as operational account/capture directories.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat

from trading_data.upbit_history import parse_end
from . import market_capture_store as capture_store
from . import paper_stream_store as stream_store
from .paper_stream import _Blocked, _SourceBinding, _Stopped, _utc, _verify
from .paper_backup_io import (
    BackupInterrupted, BackupStorageError, BackupUnavailable, PrivateDirectory,
    check_stop, copy_file, file_info, write_file,
)

ACCOUNT_NAME = 'account-database.bin'
CAPTURE_NAME = 'capture-database.bin'
MANIFEST_NAME = 'backup.json'
LOCK_NAME = '.backup.lock'
MAX_MANIFEST_BYTES = 65536
LIMITS = {ACCOUNT_NAME: stream_store.MAX_DATABASE_BYTES,
          CAPTURE_NAME: capture_store.MAX_DATABASE_BYTES}
MEMBERS = {LOCK_NAME, MANIFEST_NAME, ACCOUNT_NAME, CAPTURE_NAME}
BOUNDARIES = dict(verification='full_archived_replay', execution_authorized=False,
    source_rebinding=False, rollback_protection=False, provider_authenticity='not_authenticated')
_META_FIELDS = {'format', 'version', 'mode', 'live_enabled', 'backup_id',
    'created_at', 'origin', 'account', 'capture', 'payloads', 'boundaries'}
_ACCOUNT_FIELDS = {'stream_id', 'execution_fingerprint', 'manifest_sha256',
    'engine_sha256', 'event_head_sha256', 'events_verified', 'control'}
_CAPTURE_FIELDS = {'capture_id', 'config_sha256', 'snapshot_sha256', 'state'}


def _invalid():
    return BackupUnavailable('paper_backup_unavailable')


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _same(left, right):
    return _encoded(left) == _encoded(right)


def _fields(value, expected):
    if type(value) is not dict or set(value) != expected:
        raise _invalid()


def _hex(value, count=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(count) + '}', value) is None:
        raise _invalid()


def _path(value):
    if (type(value) is not str or not 1 <= len(value.encode('utf-8')) <= 4096
            or not Path(value).is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in ('.', '..') for part in value.split('/'))):
        raise _invalid()


def _stamp(value):
    if not isinstance(value, datetime):
        raise _invalid()
    return _utc(value).isoformat()


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise _invalid()


def _parse_integer(value):
    if len(value) > 16:
        raise _invalid()
    return int(value)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _constant(value):
    raise _invalid()


def _validate_meta(meta):
    _fields(meta, _META_FIELDS)
    if (meta['format'] != 'uulab.paper-backup' or type(meta['version']) is not int
            or meta['version'] != 1 or meta['mode'] != 'paper'
            or meta['live_enabled'] is not False or not _same(meta['boundaries'], BOUNDARIES)):
        raise _invalid()
    _hex(meta['backup_id'], 32)
    if type(meta['created_at']) is not str or _stamp(parse_end(meta['created_at'])) != meta['created_at']:
        raise _invalid()
    _fields(meta['origin'], {'account_path', 'capture_path'})
    for path in meta['origin'].values():
        _path(path)
    _fields(meta['account'], _ACCOUNT_FIELDS)
    _hex(meta['account']['stream_id'], 32)
    for key in ('execution_fingerprint', 'manifest_sha256', 'engine_sha256', 'event_head_sha256'):
        _hex(meta['account'][key])
    _integer(meta['account']['events_verified'], 0, 100000)
    _fields(meta['capture'], _CAPTURE_FIELDS)
    _hex(meta['capture']['capture_id'], 32)
    for key in ('config_sha256', 'snapshot_sha256'):
        _hex(meta['capture'][key])
    _fields(meta['payloads'], set(LIMITS))
    for name, limit in LIMITS.items():
        info = meta['payloads'][name]
        _fields(info, {'size', 'sha256'})
        _integer(info['size'], 100, limit)
        _hex(info['sha256'])
    return meta


@contextmanager
def _errors(mutated):
    try:
        yield
    except _Stopped as error:
        raise BackupInterrupted(error.signum) from None
    except (BackupInterrupted, BackupStorageError):
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError,
            RecursionError, UnicodeError, _Blocked) as error:
        if mutated[0]:
            raise BackupStorageError('paper_backup_output_unconfirmed') from error
        raise _invalid() from error


def _check_bundle(directory):
    directory.check()
    directory.check_names(MEMBERS)
    for name in MEMBERS:
        directory.check_file(name, stable=True)


def _read_meta(directory):
    fd = directory.open_file(MANIFEST_NAME, limit=MAX_MANIFEST_BYTES, minimum=2)
    raw = os.pread(fd, MAX_MANIFEST_BYTES + 1, 0)
    directory.check_file(MANIFEST_NAME, stable=True)
    if not 2 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise _invalid()
    meta = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                      parse_int=_parse_integer, parse_constant=_constant)
    if _encoded(meta) != raw:
        raise _invalid()
    return _validate_meta(meta)


@contextmanager
def _open_bundle(path):
    with PrivateDirectory(path) as directory:
        directory.lock(LOCK_NAME)
        directory.check_names(MEMBERS)
        meta = _read_meta(directory)
        for name, limit in LIMITS.items():
            directory.open_file(name, limit=limit, minimum=100)
        _check_bundle(directory)
        yield directory, meta
        _check_bundle(directory)


class _PayloadBinding:
    """Adapt an immutable bundle to the unchanged account reader's check API."""
    def __init__(self, directory):
        self.directory = directory

    def check(self, *, observer=False, stable=False):
        _check_bundle(self.directory)


@contextmanager
def _payload_connection(directory, name):
    _check_bundle(directory)
    header = os.pread(directory.files[name], 100, 0)
    if header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
        raise _invalid()
    connection = sqlite3.connect((directory.path / name).as_uri() + '?mode=ro',
                                 uri=True, timeout=0.2, isolation_level=None)
    try:
        # SQLite opens by name independently: validate before it can query bytes.
        _check_bundle(directory)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA cache_size=-2048')
        connection.execute('PRAGMA query_only=ON')
        yield connection
        _check_bundle(directory)
    finally:
        connection.close()


def _evidence(account, capture, audit):
    manifest = account['manifest']
    if (capture['state']['capture_id'] != manifest['capture']['capture_id']
            or not _same(capture['config'], manifest['capture']['config'])):
        raise _invalid()
    return dict(
        account=dict(stream_id=manifest['stream_id'],
            execution_fingerprint=manifest['engine_sha256'],
            manifest_sha256=stream_store.digest(manifest),
            engine_sha256=account['engine_sha256'], event_head_sha256=account['event_head_sha256'],
            events_verified=audit['events_verified'], control=deepcopy(account['control'])),
        capture=dict(capture_id=capture['state']['capture_id'],
            config_sha256=stream_store.digest(capture['config']),
            snapshot_sha256=stream_store.digest(capture), state=deepcopy(capture['state'])))


def _creation_time(value, account, capture):
    stamp = _stamp(value)
    if parse_end(stamp) < max(parse_end(account['control']['updated_at']),
                             parse_end(capture['state']['updated_at'])):
        raise _invalid()
    return stamp


async def _verify_payloads(directory, meta, stop):
    check_stop(stop)
    _validate_meta(meta)
    for name, limit in LIMITS.items():
        if file_info(directory.files[name], limit, stop=stop,
                     check=lambda: _check_bundle(directory)) != meta['payloads'][name]:
            raise _invalid()
    with _payload_connection(directory, CAPTURE_NAME) as connection:
        connection.execute('BEGIN')
        capture = capture_store._read_snapshot(connection)
        connection.rollback()
    with _payload_connection(directory, ACCOUNT_NAME) as connection:
        connection.execute('BEGIN')
        account = stream_store._read_snapshot(connection)
        connection.rollback()
        owner = stream_store._Reader(_PayloadBinding(directory), connection, account, audit=True)
        try:
            audit = await _verify(owner, account, stop)
            if not _same(owner.read(), account):
                raise _invalid()
        finally:
            owner._broken = True
    evidence = _evidence(account, capture, audit)
    if (not _same(meta['account'], evidence['account'])
            or not _same(meta['capture'], evidence['capture'])
            or meta['origin']['capture_path'] != account['manifest']['capture']['path']):
        raise _invalid()
    _creation_time(parse_end(meta['created_at']), account, capture)
    for name, limit in LIMITS.items():
        if file_info(directory.files[name], limit, stop=stop,
                     check=lambda: _check_bundle(directory)) != meta['payloads'][name]:
            raise _invalid()
    check_stop(stop)
    _check_bundle(directory)
    return audit


def _result(operation, meta):
    return dict(schema_version=1, source='paper_backup_operation', mode='paper',
        live_enabled=False, operation=operation, read_only=operation == 'check',
        status='verified_restore' if operation == 'verify-restore' else 'verified_backup',
        exit_code=0, backup_id=meta['backup_id'], stream_id=meta['account']['stream_id'],
        capture_id=meta['capture']['capture_id'], events_verified=meta['account']['events_verified'],
        engine_sha256=meta['account']['engine_sha256'],
        event_head_sha256=meta['account']['event_head_sha256'],
        payloads=deepcopy(meta['payloads']), boundaries=deepcopy(BOUNDARIES))


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


class _Destination:
    """Carry the selected parent through potentially lengthy input replay."""
    def __init__(self, output, sources):
        self.path = Path(output).absolute()
        self.fd = None
        self.pid = os.getpid()
        try:
            if self.path.name in ('', '.', '..'):
                raise _invalid()
            before = self.path.parent.lstat()
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
                raise _invalid()
            self.expected_parent = (str(self.path.parent.resolve(strict=True)), _identity(before))
            self.fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY
                              | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            self.check()
            target = Path(self.expected_parent[0]) / self.path.name
            for source in sources:
                origin = Path(source).resolve(strict=True)
                if target == origin or origin in target.parents or target in origin.parents:
                    raise _invalid()
            try:
                self.path.lstat()
            except FileNotFoundError:
                pass
            else:
                raise _invalid()
        except BaseException:
            self.close()
            raise

    def check(self):
        if (os.getpid() != self.pid
                or str(self.path.parent.resolve(strict=True)) != self.expected_parent[0]
                or _identity(self.path.parent.lstat()) != self.expected_parent[1]
                or _identity(os.fstat(self.fd)) != self.expected_parent[1]):
            raise _invalid()

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
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _content(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


async def create_backup(account_directory, output, *, stop=None, clock=None):
    """Archive one quiescent account and its exact bound capture; never repair."""
    mutated = [False]
    with _errors(mutated):
        check_stop(stop)
        with stream_store.stream_reader(account_directory, audit=True) as owner:
            account = owner.read()
            source = _SourceBinding(account['manifest']['capture']['path'],
                                    account['manifest']['capture']['binding'])
            try:
                fcntl.flock(source.fds['lock'], fcntl.LOCK_SH | fcntl.LOCK_NB)
                capture = source.read()
                source_before = _content(source.fds['database'])
                def stable_sources():
                    owner._ensure()
                    source.check()
                    if _content(source.fds['database']) != source_before:
                        raise _invalid()
                with _Destination(output, (owner.binding.path, source.path)) as target:
                    audit = await _verify(owner, account, stop)
                    evidence = _evidence(account, capture, audit)
                    created = _creation_time((clock or (lambda: datetime.now(timezone.utc)))(),
                                             account, capture)
                    def unchanged():
                        stable_sources()
                        target.check()
                    unchanged()
                    check_stop(stop)
                    with PrivateDirectory(target.path, create=True,
                                          expected_parent=target.expected_parent) as directory:
                        mutated[0] = True
                        directory.lock(LOCK_NAME, exclusive=True, create=True)
                        payloads = {}
                        for name, fd in ((ACCOUNT_NAME, owner.binding.database_fd),
                                         (CAPTURE_NAME, source.fds['database'])):
                            payloads[name] = copy_file(fd, directory, name, LIMITS[name],
                                                       stop=stop, check=unchanged)
                        unchanged()
                        if not _same(owner.read(), account) or not _same(source.read(), capture):
                            raise _invalid()
                        meta = dict(format='uulab.paper-backup', version=1, mode='paper',
                            live_enabled=False, backup_id=secrets.token_hex(16), created_at=created,
                            origin=dict(account_path=str(owner.binding.path.resolve(strict=True)),
                                        capture_path=account['manifest']['capture']['path']),
                            **evidence, payloads=payloads, boundaries=deepcopy(BOUNDARIES))
                        _validate_meta(meta)
                        write_file(directory, MANIFEST_NAME, _encoded(meta), MAX_MANIFEST_BYTES,
                                   stop=stop, check=unchanged)
                        directory.sync()
                        await _verify_payloads(directory, meta, stop)
                        unchanged()
                        if not _same(owner.read(), account) or not _same(source.read(), capture):
                            raise _invalid()
                        directory.sync()
                        unchanged()
                        _check_bundle(directory)
                        check_stop(stop)
                        return _result('create', meta)
            finally:
                source.close()


async def check_backup(directory, *, stop=None):
    """Fully verify archived bytes without source access, staging or writes."""
    with _errors([False]):
        check_stop(stop)
        with _open_bundle(directory) as (bundle, meta):
            await _verify_payloads(bundle, meta, stop)
            return _result('check', meta)


async def verify_restore(directory, output, *, stop=None):
    """Materialize and fully verify a new non-executing copy of one archive."""
    mutated = [False]
    with _errors(mutated):
        check_stop(stop)
        with _open_bundle(directory) as (original, meta):
            with _Destination(output, (original.path,)) as target:
                await _verify_payloads(original, meta, stop)
                def unchanged():
                    _check_bundle(original)
                    target.check()
                unchanged()
                with PrivateDirectory(target.path, create=True,
                                      expected_parent=target.expected_parent) as restored:
                    mutated[0] = True
                    restored.lock(LOCK_NAME, exclusive=True, create=True)
                    for name, limit in LIMITS.items():
                        copied = copy_file(original.files[name], restored, name, limit,
                                           stop=stop, check=unchanged)
                        if copied != meta['payloads'][name]:
                            raise _invalid()
                    copy_file(original.files[MANIFEST_NAME], restored, MANIFEST_NAME,
                              MAX_MANIFEST_BYTES, stop=stop, check=unchanged)
                    if not _same(_read_meta(restored), meta):
                        raise _invalid()
                    restored.sync()
                    await _verify_payloads(restored, meta, stop)
                    unchanged()
                    restored.sync()
                    _check_bundle(restored)
                    check_stop(stop)
                    return _result('verify-restore', meta)
