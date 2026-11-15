"""Non-activating PAPER host capability probe. No provider or broker requests."""

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import stat
import sys
import tempfile


_CHECKS = ("linux", "cpython", "proc_fd", "private_files", "advisory_lock", "unix_socket", "sqlite_wal")


def _private_root(path):
    """Require an existing owned, private physical directory; never create it."""
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("invalid_directory")
    current = Path("/")
    for name in candidate.parts[1:]:
        current = current / name
        entry = os.lstat(current)
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
            raise ValueError("invalid_directory")
    info = os.stat(candidate)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("invalid_directory")
    return candidate


def _check_proc_fd():
    fd = os.open('/proc/self/fd', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        return stat.S_ISDIR(info.st_mode)
    finally:
        os.close(fd)


def _check_private_files(work):
    path = work / "private"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, b"paper-probe")
        os.fsync(fd)
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            return False
    finally:
        os.close(fd)
    if path.read_bytes() != b"paper-probe":
        return False
    os.symlink("private", work / "link")
    try:
        fd = os.open(work / "link", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return True
    else:
        os.close(fd)
        return False


def _check_lock(work):
    path = work / "lock"
    fd1 = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    fd2 = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            return False
    finally:
        os.close(fd2)
        os.close(fd1)


def _check_socket(work):
    path = str(work / "sock")
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as server:
        server.settimeout(1)
        server.bind(path)
        server.listen(1)
        with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as client:
            client.settimeout(1)
            client.connect(path)
            with contextlib.closing(server.accept()[0]):
                return True


def _check_sqlite(work):
    path = str(work / 'probe.sqlite')
    with contextlib.closing(sqlite3.connect(path, timeout=1)) as connection:
        mode = connection.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('CREATE TABLE probe (value INTEGER NOT NULL)')
        connection.execute('INSERT INTO probe VALUES (1)')
        connection.commit()
        if mode.lower() != 'wal':
            return False
    with contextlib.closing(sqlite3.connect(path, timeout=1)) as connection:
        return connection.execute('SELECT value FROM probe').fetchone() == (1,)


def inspect(directory):
    """Return point-in-time host capabilities; never authorize a service start."""
    result = {name: 'not_checked' for name in _CHECKS}
    result['linux'] = 'pass' if sys.platform == 'linux' else 'fail'
    result['cpython'] = ('pass' if platform.python_implementation() == 'CPython'
                          and sys.version_info >= (3, 10) else 'fail')
    if result['linux'] == 'pass':
        try:
            result['proc_fd'] = 'pass' if _check_proc_fd() else 'fail'
        except (OSError, ValueError):
            result['proc_fd'] = 'fail'
    if all(result[name] == 'pass' for name in ('linux', 'cpython')):
        with tempfile.TemporaryDirectory(prefix='.paper-host-', dir=str(directory)) as tmp:
            work = Path(tmp)
            for name, probe in (("private_files", _check_private_files),
                                ("advisory_lock", _check_lock),
                                ("unix_socket", _check_socket),
                                ("sqlite_wal", _check_sqlite)):
                try:
                    result[name] = 'pass' if probe(work) else 'fail'
                except (OSError, ValueError, sqlite3.Error):
                    result[name] = 'fail'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--directory', required=True)
    try:
        args = parser.parse_args(argv)
        directory = _private_root(args.directory)
        checks = inspect(directory)
        passed = all(value == 'pass' for value in checks.values())
        code = 0 if passed else 1
        report = {'schema_version': 1, 'source': 'paper_host_preflight',
                  'status': 'capabilities_observed' if passed else 'unsupported',
                  'exit_code': code, 'checks': checks,
                  'boundaries': {'live_enabled': False, 'service_activated': False,
                                 'provider_verified': False, 'production_ready': False}}
    except (ValueError, OSError, SystemExit):
        code = 2
        report = {'schema_version': 1, 'source': 'paper_host_preflight',
                  'status': 'unavailable', 'exit_code': code, 'checks': {},
                  'boundaries': {'live_enabled': False, 'service_activated': False,
                                 'provider_verified': False, 'production_ready': False}}
    try:
        print(json.dumps(report, sort_keys=True), flush=True)
    except OSError:
        return 4
    return code


if __name__ == '__main__':
    raise SystemExit(main())
