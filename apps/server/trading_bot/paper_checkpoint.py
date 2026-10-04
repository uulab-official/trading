"""One cooperating writer per local POSIX paper checkpoint."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat


class CheckpointInUseError(RuntimeError):
    """Another paper run currently owns this checkpoint."""


@contextmanager
def locked_checkpoint(path):
    """Hold writer ownership from before checkpoint reads through the final save.

    The persistent lock file must not be unlinked: competing processes must
    continue to lock the same inode. The kernel releases ownership on exit.
    In-memory replay does not need filesystem or POSIX locking support.
    """
    if path is None:
        yield None
        return
    try:
        import fcntl
    except ImportError as error:
        raise RuntimeError("Checkpoint replay requires POSIX file locking on a local filesystem") from error
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Checkpoint replay requires POSIX no-follow file locking support")
    canonical = Path(path).resolve()
    canonical.parent.mkdir(parents=True, exist_ok=True)
    lock_path = canonical.with_name(f".{canonical.name}.lock")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Paper checkpoint lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise CheckpointInUseError(f"Paper checkpoint is already in use: {canonical}") from error
            raise
        acquired = True
        yield canonical
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
