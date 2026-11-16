"""Stable bounded observations and confirmed, no-clobber local publication.

POSIX local files only. Descriptor/name checks detect observed replacement;
these are not a sandbox against a hostile writer or filesystem guarantees.
"""
import os
from pathlib import Path
import secrets
import stat


class ReportPublicationUncertain(OSError):
    """Publication may exist; preserve it and inspect before any further run."""

    def __init__(self):
        super().__init__('Report may have been published; final confirmation failed. '
                         'Preserve all output and inspect it. Do not rerun automatically.')


def _identity(value):
    return value.st_dev, value.st_ino, value.st_mode


def _observation(value):
    return (_identity(value), value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _parent_observation(parent):
    # Resolve repeatedly, rather than forgetting the original named parent, so
    # relocation behind a new alias cannot silently pass as the same parent.
    return (str(parent.resolve(strict=True)), _identity(parent.stat()),
            _identity(parent.lstat()))


def _open_parent(path):
    parent = path.parent
    before = _parent_observation(parent)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if _identity(os.fstat(fd)) != before[1] or _parent_observation(parent) != before:
            raise OSError('local parent changed')
        return fd, before
    except BaseException:
        os.close(fd)
        raise


def _check_parent(parent, fd, before):
    if _identity(os.fstat(fd)) != before[1] or _parent_observation(parent) != before:
        raise OSError('local parent changed')


def _named_stat(fd, name):
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def read_regular_bytes(path, *, max_bytes):
    """Read at most max_bytes + 1 bytes, rejecting unstable/special files."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError('invalid local read bound')
    path = Path(path)
    parent_fd, parent_before = _open_parent(path)
    fd = None
    try:
        before = _named_stat(parent_fd, path.name)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('regular local file required')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent_fd)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _observation(opened) != _observation(before):
            raise ValueError('local file changed')
        if opened.st_size > max_bytes:
            raise ValueError('local file too large')
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        named_after = _named_stat(parent_fd, path.name)
        _check_parent(path.parent, parent_fd, parent_before)
        if (_observation(after) != _observation(before)
                or _observation(named_after) != _observation(before)):
            raise ValueError('local file changed')
        raw = b''.join(chunks)
        if len(raw) > max_bytes:
            raise ValueError('local file too large')
        return raw
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def publish_new_report(path, body, *, on_exists=None, private_parent=False,
                       validate_destination=None):
    """Publish immutable bytes under a pinned parent and confirm directory sync.

    Once linking is attempted an ambiguous error must never become success or a
    retry. The destination is never removed, replaced, or repaired here.
    An optional collision resolver runs while the original parent remains pinned;
    its result is returned only after cleanup and a final parent confirmation.
    An optional destination validator receives the pinned parent observation
    before staging, before linking and during final confirmation. It can carry
    source/destination preconditions across a caller's earlier validated read.
    """
    if not isinstance(body, bytes):
        raise TypeError('report bytes required')
    path = Path(path)
    parent_fd, parent_before = _open_parent(path)
    temporary = '.report-' + secrets.token_hex(16) + '.tmp'
    fd = None
    possible_publication = False
    resolved_collision = False

    def check_bindings():
        _check_parent(path.parent, parent_fd, parent_before)
        if validate_destination is not None:
            validate_destination(parent_before)
            _check_parent(path.parent, parent_fd, parent_before)

    try:
        if private_parent:
            parent_stat = os.fstat(parent_fd)
            if (parent_stat.st_uid != os.getuid()
                    or stat.S_IMODE(parent_stat.st_mode) != 0o700
                    or path.parent.is_symlink()):
                raise ValueError('private owned publication parent required')
        check_bindings()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
        os.fchmod(fd, 0o600)
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('incomplete local write')
            view = view[written:]
        staged = os.fstat(fd)
        os.fsync(fd)
        check_bindings()
        if (_observation(os.fstat(fd)) != _observation(staged)
                or _observation(_named_stat(parent_fd, temporary)) != _observation(staged)):
            raise OSError('local staging file changed')
        possible_publication = True
        try:
            os.link(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
        except FileExistsError:
            possible_publication = False
            if on_exists is None:
                raise
            check_bindings()
            result = on_exists()
            resolved_collision = True
            return result
        # Linking changes ctime/link count. All other recorded attributes must
        # still describe the descriptor we wrote, including its private mode.
        linked = os.fstat(fd)
        if (_identity(linked) != _identity(staged) or linked.st_size != staged.st_size
                or linked.st_mtime_ns != staged.st_mtime_ns
                or _observation(_named_stat(parent_fd, path.name)) != _observation(linked)
                or _observation(_named_stat(parent_fd, temporary)) != _observation(linked)):
            raise OSError('local publication changed')
        check_bindings()
        os.unlink(temporary, dir_fd=parent_fd)
        temporary = None
        published = os.fstat(fd)
        os.fsync(parent_fd)
        check_bindings()
        if (_observation(os.fstat(fd)) != _observation(published)
                or _observation(_named_stat(parent_fd, path.name)) != _observation(published)):
            raise OSError('local publication changed')
    except (OSError, ValueError) as error:
        if possible_publication:
            raise ReportPublicationUncertain() from error
        raise
    finally:
        try:
            # Only unlink the staging inode we own, even on an uncertain result.
            # Any named destination or substituted staging file is preserved.
            if temporary is not None and fd is not None:
                try:
                    if _identity(_named_stat(parent_fd, temporary)) == _identity(os.fstat(fd)):
                        os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    if possible_publication:
                        raise ReportPublicationUncertain() from error
                    raise
            if resolved_collision:
                check_bindings()
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent_fd)
