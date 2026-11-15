"""Private bounded POSIX files for non-executing paper backup artifacts.

Ownership locks coordinate cooperating processes. Pinned descriptor/name checks
detect observed replacement; they are not a sandbox against privileged rewrites.
Created output is always preserved after an interruption or uncertain write.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat

CHUNK_BYTES = 1024 * 1024
MAX_NAMES = 16


class BackupUnavailable(ValueError):
    """An input, name, owner or stable observation was refused."""


class BackupStorageError(OSError):
    """Created output may be incomplete or unconfirmed; preserve it."""


class BackupInterrupted(Exception):
    """Cooperative stop intent; any already created output is preserved."""

    def __init__(self, signum):
        self.signum = signum
        super().__init__('paper_backup_interrupted')


def _unavailable():
    return BackupUnavailable('paper_backup_unavailable')


def _uncertain():
    return BackupStorageError('paper_backup_storage_unconfirmed')


def check_stop(stop):
    if stop is None:
        return
    if not callable(stop):
        raise _unavailable()
    signum = stop()
    if type(signum) is not int or signum not in (0, 2, 15):
        raise _unavailable()
    if signum:
        raise BackupInterrupted(signum)


def _guards(stop, check):
    check_stop(stop)
    if check is not None:
        if not callable(check):
            raise _unavailable()
        check()
    check_stop(stop)


def _bound(limit, minimum=0):
    if (type(limit) is not int or type(minimum) is not int
            or not 0 <= minimum <= limit <= 2**63 - 1):
        raise _unavailable()


def _name(value):
    if (not isinstance(value, str) or value in {'.', '..'}
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,255}', value)):
        raise _unavailable()


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _content(info):
    return _identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _directory(info, *, leaf=False):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or (leaf and stat.S_IMODE(info.st_mode) != 0o700)):
        raise _unavailable()


def _file(info, limit, minimum=0):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not minimum <= info.st_size <= limit):
        raise _unavailable()


class PrivateDirectory:
    """Pin an owned directory and its private file descriptors until close."""

    def __init__(self, path, *, create=False, expected_parent=None):
        self.path = None
        self.parent_fd = self.directory_fd = None
        self.files = {}
        self._records = {}
        self._locks = {}
        self._pid = os.getpid()
        self._closed = False
        self._created = False
        try:
            if type(create) is not bool:
                raise _unavailable()
            self.path = Path(path).absolute()
            if self.path.name in ('', '.', '..'):
                raise _unavailable()
            parent = self.path.parent.lstat()
            _directory(parent)
            self._parent_path = str(self.path.parent.resolve(strict=True))
            self._parent_identity = _identity(parent)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            self.parent_fd = os.open(self.path.parent, flags)
            if _identity(os.fstat(self.parent_fd)) != self._parent_identity:
                raise _unavailable()
            self._check_parent()
            if (expected_parent is not None
                    and (self._parent_path, self._parent_identity) != expected_parent):
                raise _unavailable()
            if create:
                os.mkdir(self.path.name, 0o700, dir_fd=self.parent_fd)
                self._created = True
            before = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid():
                raise _unavailable()
            if create and stat.S_IMODE(before.st_mode) != 0o700:
                # mkdir is affected by umask and does not return a descriptor.
                # Make our newly observed directory openable without changing
                # the process-wide umask or following a substituted symlink.
                os.chmod(self.path.name, 0o700, dir_fd=self.parent_fd, follow_symlinks=False)
                after = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
                if ((before.st_dev, before.st_ino, before.st_uid, before.st_gid)
                        != (after.st_dev, after.st_ino, after.st_uid, after.st_gid)):
                    raise _unavailable()
                before = after
            self.directory_fd = os.open(self.path.name, flags, dir_fd=self.parent_fd)
            opened = os.fstat(self.directory_fd)
            if _identity(opened) != _identity(before):
                raise _unavailable()
            if create:
                os.fchmod(self.directory_fd, 0o700)
            opened = os.fstat(self.directory_fd)
            _directory(opened, leaf=True)
            self._directory_identity = _identity(opened)
            self.check()
        except (OSError, ValueError, TypeError) as error:
            self.close()
            if self._created or (create and isinstance(error, OSError)
                    and not isinstance(error, (FileExistsError, FileNotFoundError, PermissionError))):
                raise _uncertain() from error
            raise _unavailable() from error

    def _check_parent(self):
        named = self.path.parent.lstat()
        opened = os.fstat(self.parent_fd)
        _directory(named)
        _directory(opened)
        if (str(self.path.parent.resolve(strict=True)) != self._parent_path
                or _identity(named) != self._parent_identity
                or _identity(opened) != self._parent_identity):
            raise _unavailable()

    def __enter__(self):
        try:
            self.check()
        except BackupUnavailable as error:
            self.close()
            if self._created:
                raise _uncertain() from error
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def check(self):
        try:
            if self._closed or os.getpid() != self._pid:
                raise _unavailable()
            self._check_parent()
            named = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
            opened = os.fstat(self.directory_fd)
            _directory(named, leaf=True)
            _directory(opened, leaf=True)
            if (_identity(named) != self._directory_identity
                    or _identity(opened) != self._directory_identity):
                raise _unavailable()
        except (OSError, ValueError, TypeError) as error:
            raise _unavailable() from error

    def open_file(self, name, *, create=False, limit, minimum=0):
        _name(name)
        _bound(limit, minimum)
        if type(create) is not bool:
            raise _unavailable()
        self.check()
        if name in self.files:
            if create:
                raise _unavailable()
            self.check_file(name)
            _file(os.fstat(self.files[name]), limit, minimum)
            return self.files[name]
        descriptor = None
        created = False
        try:
            flags = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            flags |= os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            descriptor = os.open(name, flags, 0o600, dir_fd=self.directory_fd)
            created = create
            if create:
                os.fchmod(descriptor, 0o600)
            opened = os.fstat(descriptor)
            named = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
            _file(opened, limit, minimum)
            _file(named, limit, minimum)
            if _content(named) != _content(opened):
                raise _unavailable()
            self.check()
            self.files[name] = descriptor
            self._records[name] = dict(identity=_identity(opened), content=_content(opened),
                                       limit=limit, minimum=minimum)
            return descriptor
        except (OSError, ValueError, TypeError) as error:
            if descriptor is not None:
                os.close(descriptor)
            if created or (create and isinstance(error, OSError)
                    and not isinstance(error, (FileExistsError, FileNotFoundError, PermissionError))):
                raise _uncertain() from error
            raise _unavailable() from error

    def check_file(self, name, *, stable=True):
        _name(name)
        if type(stable) is not bool:
            raise _unavailable()
        self.check()
        try:
            descriptor, record = self.files[name], self._records[name]
            opened = os.fstat(descriptor)
            named = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
            _file(opened, record['limit'], record['minimum'])
            _file(named, record['limit'], record['minimum'])
            if (_identity(opened) != record['identity']
                    or _content(named) != _content(opened)
                    or (stable and _content(opened) != record['content'])):
                raise _unavailable()
            self.check()
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise _unavailable() from error

    def refresh_file(self, name):
        self.check_file(name, stable=False)
        self._records[name]['content'] = _content(os.fstat(self.files[name]))
        self.check_file(name)

    def check_names(self, expected_names):
        if not isinstance(expected_names, (set, frozenset, list, tuple)) or len(expected_names) > MAX_NAMES:
            raise _unavailable()
        for name in expected_names:
            _name(name)
        self.check()
        try:
            observed = set()
            with os.scandir(self.directory_fd) as entries:
                for entry in entries:
                    if len(observed) >= MAX_NAMES:
                        raise _unavailable()
                    observed.add(entry.name)
            self.check()
            if observed != set(expected_names):
                raise _unavailable()
        except (OSError, ValueError, TypeError) as error:
            raise _unavailable() from error

    def lock(self, name, *, exclusive=False, create=False):
        if type(exclusive) is not bool:
            raise _unavailable()
        descriptor = self.open_file(name, create=create, limit=0)
        if name in self._locks:
            if self._locks[name] != exclusive:
                raise _unavailable()
            self.check_file(name)
            return descriptor
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            self._locks[name] = exclusive
            self.check_file(name)
            if create:
                os.fsync(descriptor)
                self.check_file(name)
            return descriptor
        except (OSError, ValueError) as error:
            if create:
                raise _uncertain() from error
            raise _unavailable() from error

    def sync(self):
        self.check()
        try:
            os.fsync(self.directory_fd)
            self.check()
            os.fsync(self.parent_fd)
            self.check()
        except (OSError, ValueError, TypeError) as error:
            raise _uncertain() from error

    def close(self):
        descriptors = list(self.files.values()) + [self.directory_fd, self.parent_fd]
        self.files.clear()
        self._records.clear()
        self._locks.clear()
        self.directory_fd = self.parent_fd = None
        self._closed = True
        for descriptor in descriptors:
            if descriptor is not None:
                try:
                    # Never LOCK_UN: a forked child must not unlock its parent.
                    os.close(descriptor)
                except OSError:
                    pass


def _source_state(descriptor, limit):
    _bound(limit)
    if type(descriptor) is not int or descriptor < 0:
        raise _unavailable()
    info = os.fstat(descriptor)
    _file(info, limit)
    return info


def _same_source(descriptor, before, limit):
    after = _source_state(descriptor, limit)
    if _content(before) != _content(after):
        raise _unavailable()


def file_info(fd, limit, *, stop=None, check=None):
    """Hash a stable private regular file without changing its current offset."""
    try:
        _bound(limit)
        _guards(stop, check)
        before = _source_state(fd, limit)
        digest = hashlib.sha256()
        position = 0
        while position < before.st_size:
            _guards(stop, check)
            _same_source(fd, before, limit)
            block = os.pread(fd, min(CHUNK_BYTES, before.st_size - position), position)
            if not block:
                raise _unavailable()
            digest.update(block)
            position += len(block)
            _guards(stop, check)
        _guards(stop, check)
        _same_source(fd, before, limit)
        return dict(size=position, sha256=digest.hexdigest())
    except BackupInterrupted:
        raise
    except (OSError, ValueError, TypeError, OverflowError) as error:
        raise _unavailable() from error


def _write_all(fd, block, guard):
    view = memoryview(block)
    while view:
        guard()
        written = os.write(fd, view)
        if type(written) is not int or not 0 < written <= len(view):
            raise _uncertain()
        view = view[written:]
        guard()


def _finish(destination, name, fd, expected, limit, guard, stop):
    guard()
    os.fsync(fd)
    guard()
    destination.refresh_file(name)
    actual = file_info(fd, limit, stop=stop,
                       check=lambda: (guard(), destination.check_file(name)))
    if actual != expected:
        raise _uncertain()
    guard()
    destination.check_file(name)
    return actual


def copy_file(source_fd, destination, name, limit, *, stop=None, check=None):
    """Copy into a new private file; never remove uncertain or partial output."""
    try:
        _bound(limit)
        _guards(stop, check)
        before = _source_state(source_fd, limit)
        destination.check()
    except BackupInterrupted:
        raise
    except (OSError, ValueError, TypeError, OverflowError) as error:
        raise _unavailable() from error
    fd = destination.open_file(name, create=True, limit=limit)

    def guard():
        _guards(stop, check)
        _same_source(source_fd, before, limit)
        destination.check_file(name, stable=False)

    try:
        digest = hashlib.sha256()
        position = 0
        while position < before.st_size:
            guard()
            block = os.pread(source_fd, min(CHUNK_BYTES, before.st_size - position), position)
            if not block:
                raise _unavailable()
            _write_all(fd, block, guard)
            digest.update(block)
            position += len(block)
        expected = dict(size=position, sha256=digest.hexdigest())
        return _finish(destination, name, fd, expected, limit, guard, stop)
    except (BackupInterrupted, BackupStorageError):
        raise
    except Exception as error:
        raise _uncertain() from error


def write_file(destination, name, data, limit, *, stop=None, check=None):
    """Write bounded immutable bytes to a new private file and verify readback."""
    try:
        _bound(limit)
        if not isinstance(data, bytes) or len(data) > limit:
            raise _unavailable()
        _guards(stop, check)
        destination.check()
    except BackupInterrupted:
        raise
    except (OSError, ValueError, TypeError, OverflowError) as error:
        raise _unavailable() from error
    fd = destination.open_file(name, create=True, limit=limit)

    def guard():
        _guards(stop, check)
        destination.check_file(name, stable=False)

    try:
        for position in range(0, len(data), CHUNK_BYTES):
            _write_all(fd, memoryview(data)[position:position + CHUNK_BYTES], guard)
        expected = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        return _finish(destination, name, fd, expected, limit, guard, stop)
    except (BackupInterrupted, BackupStorageError):
        raise
    except Exception as error:
        raise _uncertain() from error
