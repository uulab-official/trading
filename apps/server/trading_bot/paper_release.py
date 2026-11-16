"""Bounded, descriptor-pinned PAPER source release operations.

Reports describe the last completed filesystem observation. Every descriptor is
closed before returning; later serialization and execution are separate events.
Possible named output is always preserved after failure.
"""
import hashlib
import os
from pathlib import Path
import stat

from . import paper_release_format as fmt

_ROOT = Path(__file__).resolve().parents[3]
_CHUNK = 64 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_FILE_FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class ReleaseUnavailable(ValueError):
    """Invalid, unavailable or unstable input; no output was created."""


class ReleaseStorageError(OSError):
    """Possible output or descriptor cleanup could not be confirmed."""


class ReleaseInterrupted(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('paper_release_interrupted')


def _unavailable():
    return ReleaseUnavailable('paper_release_unavailable')


def _uncertain():
    return ReleaseStorageError('paper_release_storage_unconfirmed')


def _stop(stop):
    if stop is None:
        return
    if not callable(stop):
        raise _unavailable()
    value = stop()
    if type(value) is not int or value not in (0, 2, 15):
        raise _unavailable()
    if value:
        raise ReleaseInterrupted(value)


def _path(value):
    # Normalize lexical dot components, but never resolve symlinks away.
    value = os.fspath(value)
    if not isinstance(value, str) or not value or '\x00' in value:
        raise _unavailable()
    return Path('/' + os.path.abspath(value).lstrip('/'))


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _content(info):
    return _identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(info, limit, private):
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or mode & 0o022
            or (private and mode != 0o600) or not 0 <= info.st_size <= limit):
        raise _unavailable()


def _separate(first, second):
    if first == second or first in second.parents or second in first.parents:
        raise _unavailable()


class _Pins:
    """Retain original component and file observations, never adopt a rename."""

    def __init__(self, stop):
        self.stop = stop
        self.fds = []
        self.dirs = {}
        self.files = []
        self.created = False
        self.inventories = {}

    def own(self, fd):
        self.fds.append(fd)
        return fd

    def directory(self, path, *, private=False):
        pending = []
        current = path
        while current not in self.dirs:
            pending.append(current)
            if current == Path('/'):
                break
            current = current.parent
        # Walk from the pinned ancestor toward the leaf without Python stack
        # recursion, so deeply nested unavailable paths remain ordinary refusals.
        for current in reversed(pending):
            _stop(self.stop)
            if current == Path('/'):
                fd = self.own(os.open('/', _DIR_FLAGS))
                before = os.fstat(fd)
                parent, name = None, '/'
            else:
                parent = self.dirs[current.parent]['fd']
                name = current.name
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode):
                    raise _unavailable()
                fd = self.own(os.open(name, _DIR_FLAGS, dir_fd=parent))
            after = os.fstat(fd)
            if not stat.S_ISDIR(after.st_mode) or _identity(before) != _identity(after):
                raise _unavailable()
            self.dirs[current] = dict(fd=fd, parent=parent, name=name,
                                      identity=_identity(after))
        record = self.dirs[path]
        if private and (record['identity'][3] != os.geteuid()
                        or stat.S_IMODE(record['identity'][2]) != 0o700):
            raise _unavailable()
        return record['fd']

    def check(self):
        for record in self.dirs.values():
            fd, parent, name = record['fd'], record['parent'], record['name']
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (_identity(named) != record['identity']
                    or _identity(os.fstat(fd)) != record['identity']):
                raise _unavailable()
        for record in self.files:
            self.check_file(record)
        for path, expected in self.inventories.items():
            self.check_names(path, expected)

    def check_names(self, path, expected):
        observed = set()
        with os.scandir(self.dirs[path]['fd']) as entries:
            for entry in entries:
                if len(observed) >= len(expected):
                    raise _unavailable()
                observed.add(entry.name)
        if observed != expected:
            raise _unavailable()

    def guard(self):
        _stop(self.stop)
        self.check()
        _stop(self.stop)

    def check_file(self, record):
        opened = os.fstat(record['fd'])
        named = os.stat(record['name'], dir_fd=record['parent'], follow_symlinks=False)
        _regular(opened, record['limit'], record['private'])
        if (_identity(opened) != record['identity'] or _content(named) != _content(opened)
                or (record['stable'] and _content(opened) != record['content'])):
            raise _unavailable()

    def read(self, path, limit, *, private=False):
        parent = self.directory(path.parent)
        self.guard()
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _regular(before, limit, private)
        fd = self.own(os.open(path.name, os.O_RDONLY | _FILE_FLAGS, dir_fd=parent))
        opened = os.fstat(fd)
        if _content(before) != _content(opened):
            raise _unavailable()
        record = dict(fd=fd, parent=parent, name=path.name, identity=_identity(opened),
                      content=_content(opened), limit=limit, private=private, stable=True)
        self.files.append(record)
        result = self.read_record(record)
        self.guard()
        return result

    def read_record(self, record):
        blocks, offset = [], 0
        while True:
            _stop(self.stop)
            self.check_file(record)
            block = os.pread(record['fd'], min(_CHUNK, record['limit'] + 1 - offset), offset)
            if not block:
                break
            blocks.append(block)
            offset += len(block)
            if offset > record['limit']:
                raise _unavailable()
        self.check_file(record)
        if offset != os.fstat(record['fd']).st_size:
            raise _unavailable()
        return b''.join(blocks)

    def new_directory(self, path):
        parent = self.directory(path.parent)
        self.guard()
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except (FileExistsError, FileNotFoundError, PermissionError):
            raise _unavailable()
        except OSError:
            self.created = True
            raise
        self.created = True
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
            raise _unavailable()
        # Linux O_PATH can pin even a mode-000 directory without permission
        # repair. fchmod does not accept O_PATH descriptors, so use the trusted
        # proc descriptor link: a renamed replacement must never be chmodded.
        if stat.S_IMODE(before.st_mode) != 0o700:
            path_fd = self.own(os.open(path.name, os.O_PATH | os.O_DIRECTORY
                                      | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent))
            if _identity(os.fstat(path_fd)) != _identity(before):
                raise _unavailable()
            os.chmod('/proc/self/fd/' + str(path_fd), 0o700)
            repaired = os.fstat(path_fd)
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (_identity(after) != _identity(repaired)
                    or stat.S_IMODE(repaired.st_mode) != 0o700):
                raise _unavailable()
        fd = self.directory(path, private=True)
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_uid, before.st_gid) != (
                opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid):
            raise _unavailable()
        self.guard()

    def write(self, path, data, limit):
        parent = self.directory(path.parent)
        self.guard()
        try:
            fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                         0o600, dir_fd=parent)
        except (FileExistsError, FileNotFoundError, PermissionError):
            raise _unavailable()
        except OSError:
            self.created = True
            raise
        self.created = True
        self.own(fd)
        os.fchmod(fd, 0o600)
        opened = os.fstat(fd)
        record = dict(fd=fd, parent=parent, name=path.name, identity=_identity(opened),
                      content=_content(opened), limit=limit, private=True, stable=False)
        self.files.append(record)
        self.check_file(record)
        for offset in range(0, len(data), _CHUNK):
            view = memoryview(data)[offset:offset + _CHUNK]
            while view:
                self.guard()
                size = os.write(fd, view)
                if type(size) is not int or not 0 < size <= len(view):
                    raise _uncertain()
                view = view[size:]
        self.guard()
        os.fsync(fd)
        self.guard()
        record['content'] = _content(os.fstat(fd))
        record['stable'] = True
        if self.read_record(record) != data:
            raise _uncertain()
        self.guard()

    def close(self):
        failed = False
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError:
                failed = True
        self.fds.clear()
        if failed:
            raise _uncertain()


def _execute(stop, operation):
    pins = _Pins(stop)
    try:
        _stop(stop)
        result = operation(pins)
        pins.guard()
        return result
    except ReleaseInterrupted:
        # A requested signal does not override detected storage uncertainty.
        if pins.created:
            try:
                pins.check()
            except (OSError, ValueError):
                raise _uncertain() from None
        raise
    except ReleaseStorageError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        if pins.created:
            raise _uncertain() from None
        raise _unavailable() from None
    finally:
        pins.close()


def _destination(pins, path):
    path = _path(path)
    _separate(path, _path(_ROOT))
    fd = pins.directory(path.parent)
    if os.fstat(fd).st_uid != os.geteuid():
        raise _unavailable()
    pins.guard()
    try:
        os.stat(path.name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return path
    raise _unavailable()


def _report(operation, status, raw, files):
    return dict(schema_version=1, source='paper_release', operation=operation,
                status=status, exit_code=0, archive_sha256=hashlib.sha256(raw).hexdigest(),
                manifest_sha256=hashlib.sha256(files[fmt.MANIFEST_NAME]).hexdigest(),
                file_count=len(fmt.RELEASE_FILES),
                payload_bytes=sum(len(files[name]) for name in fmt.RELEASE_FILES),
                execution_fingerprint=fmt.execution_fingerprint(files),
                boundaries=dict(source_integrity='digest_created' if operation == 'build'
                                else 'expected_digest_matched', readiness_assessed=False,
                                data_readiness_assessed=False, provider_authenticity_assessed=False,
                                authenticated_provenance=False, data_included=False,
                                live_execution=False, service_activation=False,
                                interpreter_packaged=False))


def _archive(pins, archive, expected_sha256):
    raw = pins.read(_path(archive), fmt.MAX_ARCHIVE_BYTES)
    files = fmt.decode_archive(raw, expected_sha256)
    pins.guard()
    return raw, files


def _inventory():
    inventory = {'': set()}
    for name in (*fmt.RELEASE_FILES, fmt.MANIFEST_NAME):
        path = Path(name)
        inventory.setdefault(path.parent.as_posix() if path.parent != Path('.') else '', set()).add(path.name)
        for parent in path.parents:
            if parent == Path('.'):
                continue
            key = parent.parent.as_posix() if parent.parent != Path('.') else ''
            inventory.setdefault(key, set()).add(parent.name)
            inventory.setdefault(parent.as_posix(), set())
    return inventory


def _check_tree(pins, directory):
    for name, expected in _inventory().items():
        path = directory / name
        pins.directory(path, private=True)
        pins.check_names(path, expected)
        pins.inventories[path] = expected
    pins.guard()


def build_release(output, *, stop=None):
    def build(pins):
        destination = _destination(pins, output)
        root = _path(_ROOT)
        files = {name: pins.read(root / name, fmt.MAX_FILE_BYTES) for name in fmt.RELEASE_FILES}
        raw = fmt.encode_archive(files)
        decoded = fmt.decode_archive(raw, hashlib.sha256(raw).hexdigest())
        pins.guard()
        pins.write(destination, raw, fmt.MAX_ARCHIVE_BYTES)
        os.fsync(pins.directory(destination.parent))
        pins.guard()
        return _report('build', 'built', raw, decoded)
    return _execute(stop, build)


def check_release(archive, *, expected_sha256, stop=None):
    def check(pins):
        raw, files = _archive(pins, archive, expected_sha256)
        return _report('check', 'verified_archive', raw, files)
    return _execute(stop, check)


def extract_release(archive, directory, *, expected_sha256, stop=None):
    def extract(pins):
        destination = _destination(pins, directory)
        _separate(destination, _path(archive))
        raw, files = _archive(pins, archive, expected_sha256)
        pins.new_directory(destination)
        for name in sorted(_inventory(), key=lambda item: (item.count('/'), item)):
            if name:
                pins.new_directory(destination / name)
        for name in fmt.RELEASE_FILES:
            pins.write(destination / name, files[name], fmt.MAX_FILE_BYTES)
        pins.write(destination / fmt.MANIFEST_NAME, files[fmt.MANIFEST_NAME], fmt.MAX_MANIFEST_BYTES)
        for name in sorted(_inventory(), key=lambda item: (item.count('/'), item), reverse=True):
            pins.guard()
            os.fsync(pins.directory(destination / name))
        os.fsync(pins.directory(destination.parent))
        _check_tree(pins, destination)
        return _report('extract', 'extracted', raw, files)
    return _execute(stop, extract)


def verify_release(directory, *, expected_sha256, stop=None):
    def verify(pins):
        root = _path(directory)
        _check_tree(pins, root)
        files = {name: pins.read(root / name, fmt.MAX_FILE_BYTES, private=True)
                 for name in fmt.RELEASE_FILES}
        manifest = pins.read(root / fmt.MANIFEST_NAME, fmt.MAX_MANIFEST_BYTES, private=True)
        raw = fmt.encode_archive(files)
        decoded = fmt.decode_archive(raw, expected_sha256)
        if decoded[fmt.MANIFEST_NAME] != manifest:
            raise _unavailable()
        _check_tree(pins, root)
        return _report('verify', 'verified_tree', raw, decoded)
    return _execute(stop, verify)
