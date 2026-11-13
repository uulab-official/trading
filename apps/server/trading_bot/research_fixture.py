"""Explicit bounded comparison of local bytes to a reported fixture digest.

This observes a regular file; it neither interprets prices nor authenticates a
provider or execution. Metadata checks detect ordinary concurrent changes, not
an authenticated atomic filesystem snapshot. Rendering uses only the pure
comparison validator and never opens a file.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import stat


MAX_FIXTURE_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_FIELDS = frozenset(('status', 'scope', 'algorithm', 'expected_sha256',
                     'actual_sha256', 'size_bytes'))


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_fixture_check(value, expected_sha256):
    """Validate a comparison's internal consistency and report binding, without I/O."""
    if (not _digest(expected_sha256) or type(value) is not dict or set(value) != _FIELDS
            or value['scope'] != 'local_fixture_bytes' or value['algorithm'] != 'sha256'
            or not _digest(value['expected_sha256']) or not _digest(value['actual_sha256'])
            or value['expected_sha256'] != expected_sha256
            or type(value['size_bytes']) is not int
            or not 0 <= value['size_bytes'] <= MAX_FIXTURE_BYTES
            or value['status'] != ('matched' if value['actual_sha256'] == expected_sha256 else 'mismatch')):
        raise ValueError('research_fixture_check_invalid')
    return dict(value)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid, info.st_nlink)


def _directory_identity(info):
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('directory_changed')
    return info.st_dev, info.st_ino


def _check_parent(named_parent, canonical_parent, identity):
    if (named_parent.resolve(strict=True) != canonical_parent
            or _directory_identity(os.stat(canonical_parent, follow_symlinks=False)) != identity):
        raise ValueError('directory_changed')


def check_fixture(path, expected_sha256):
    """Hash bounded raw bytes from a stable observed regular file, or fail closed.

The final path component must not be a symlink. An initially resolved parent
alias is allowed, but a redirect/replacement detected during this check fails.
No path or source contents are included in the returned comparison or errors.
"""
    try:
        if not _digest(expected_sha256):
            raise ValueError('invalid_digest')
        raw_path = os.fspath(path)
        if type(raw_path) is not str or not raw_path:
            raise ValueError('invalid_path')
        named = Path(raw_path).absolute()
        parent = named.parent.resolve(strict=True)
        parent_id = _directory_identity(os.stat(parent, follow_symlinks=False))

        with ExitStack() as cleanup:
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            cleanup.callback(os.close, directory)
            if _directory_identity(os.fstat(directory)) != parent_id:
                raise ValueError('directory_changed')
            _check_parent(named.parent, parent, parent_id)

            before = os.stat(named.name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_FIXTURE_BYTES:
                raise ValueError('invalid_file')
            signature = _signature(before)
            descriptor = os.open(named.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            cleanup.callback(os.close, descriptor)
            if _signature(os.fstat(descriptor)) != signature:
                raise ValueError('file_changed')

            digest = hashlib.sha256()
            size = 0
            while True:
                raw = os.read(descriptor, min(_CHUNK_BYTES, MAX_FIXTURE_BYTES + 1 - size))
                if not raw:
                    break
                size += len(raw)
                if size > MAX_FIXTURE_BYTES:
                    raise ValueError('file_too_large')
                digest.update(raw)

            if (size != before.st_size or _signature(os.fstat(descriptor)) != signature
                    or _signature(os.stat(named.name, dir_fd=directory, follow_symlinks=False)) != signature):
                raise ValueError('file_changed')
            _check_parent(named.parent, parent, parent_id)

            observed = digest.hexdigest()
            return dict(status='matched' if observed == expected_sha256 else 'mismatch',
                        scope='local_fixture_bytes', algorithm='sha256',
                        expected_sha256=expected_sha256, actual_sha256=observed, size_bytes=size)
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ValueError('research_fixture_unavailable') from None
