"""Persist explicit PAPER monitor observations without sending notifications.

Operator-invoked and read-only to all trading stores. No workers, network,
broker requests, retries, external alerts, or readiness claims are initiated.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat


MAX_INPUT = 65536
MAX_EVENTS = 10000
EXPECTED = {'service': 'running', 'capture': 'current', 'account': 'current'}


class Invalid(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid('invalid_arguments')


def _private_directory(raw):
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise Invalid('invalid_directory')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise Invalid('invalid_directory')
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Invalid('invalid_directory')
    return path


def _bounded_json_file(raw):
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise Invalid('invalid_input')
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(os.lstat(current).st_mode):
            raise Invalid('invalid_input')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT
                or info.st_size < 2 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise Invalid('invalid_input')
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(8192, MAX_INPUT + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_INPUT:
                raise Invalid('invalid_input')
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Invalid('unstable_input')
        seen = os.stat(path, follow_symlinks=False)
        if (seen.st_dev, seen.st_ino) != (info.st_dev, info.st_ino):
            raise Invalid('unstable_input')
        return json.loads(b''.join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Invalid('invalid_json') from exc
    finally:
        os.close(fd)


def _observe(raw):
    if (not isinstance(raw, dict) or type(raw.get('schema_version')) is not int
            or raw['schema_version'] != 1 or raw.get('source') != 'paper_monitor'
            or raw.get('status') not in ('healthy', 'attention_required')
            or type(raw.get('exit_code')) is not int or raw['exit_code'] not in (0, 1)):
        raise Invalid('invalid_observation')
    checks = raw.get('checks')
    if not isinstance(checks, dict) or set(checks) != set(EXPECTED):
        raise Invalid('incomplete_observation')
    signature = {}
    healthy = True
    for key, expected in EXPECTED.items():
        item = checks[key]
        if not isinstance(item, dict) or type(item.get('healthy')) is not bool:
            raise Invalid('invalid_probe')
        status, code = item.get('status'), item.get('exit_code')
        if (type(status) is not str or not 1 <= len(status) <= 64
                or (code is not None and type(code) is not int)):
            raise Invalid('invalid_probe')
        actual = bool(item['healthy'] and code == 0 and status == expected)
        if item['healthy'] != actual:
            raise Invalid('inconsistent_probe')
        signature[key] = {'healthy': actual, 'status': status, 'exit_code': code}
        healthy = healthy and actual
    if (raw['status'] == 'healthy') != healthy or raw['exit_code'] != (0 if healthy else 1):
        raise Invalid('inconsistent_observation')
    return ('healthy' if healthy else 'attention_required',
            json.dumps(signature, sort_keys=True, separators=(',', ':')))


def _open_database(directory):
    db = directory / 'paper-monitor-events.sqlite'
    if not db.exists() and not db.is_symlink():
        previous_umask = os.umask(0o077)
        try:
            fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            os.close(fd)
        finally:
            os.umask(previous_umask)
    if db.exists() or db.is_symlink():
        info = os.lstat(db)
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1):
            raise Invalid('invalid_database')
    with closing(sqlite3.connect(str(db), timeout=2, isolation_level=None)) as conn:
        conn.execute('PRAGMA busy_timeout=2000')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            status TEXT NOT NULL, signature TEXT NOT NULL, observed_at TEXT NOT NULL,
            observations INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL, status TEXT NOT NULL,
            signature TEXT NOT NULL
        );
        """)
        if stat.S_IMODE(os.stat(db).st_mode) != 0o600:
            raise Invalid('database_permissions')
    return db


def _connect(db):
    conn = sqlite3.connect(str(db), timeout=2, isolation_level=None)
    conn.execute('PRAGMA busy_timeout=2000')
    conn.execute('PRAGMA synchronous=FULL')
    return conn


def _record(db, status, signature, *, now=None):
    timestamp = now or datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with closing(_connect(db)) as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT status,signature,observed_at,observations '
                               'FROM state WHERE singleton=1').fetchone()
            if row and timestamp < row[2]:
                raise Invalid('clock_regression')
            changed = row is None or row[1] != signature or row[0] != status
            count = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
            if changed and count >= MAX_EVENTS:
                raise Invalid('event_capacity')
            if changed:
                conn.execute('INSERT INTO events(observed_at,status,signature) VALUES(?,?,?)',
                             (timestamp, status, signature))
            conn.execute('INSERT INTO state(singleton,status,signature,observed_at,observations) '
                         'VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET '
                         'status=excluded.status,signature=excluded.signature,'
                         'observed_at=excluded.observed_at,observations=excluded.observations',
                         (status, signature, timestamp, (row[3] if row else 0) + 1))
            seq = conn.execute('SELECT MAX(sequence) FROM events').fetchone()[0]
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
    return {'status': status, 'event_created': changed, 'event_sequence': seq,
            'observation_count': (row[3] if row else 0) + 1}


def _check(db):
    if not db.exists() or db.is_symlink():
        raise Invalid('missing_journal')
    info = os.lstat(db)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()):
        raise Invalid('invalid_database')
    with closing(sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True, timeout=2)) as conn:
        state = conn.execute('SELECT status,observed_at,observations FROM state '
                             'WHERE singleton=1').fetchone()
        if state is None:
            raise Invalid('missing_state')
        count = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    return {'status': state[0], 'last_observed_at': state[1],
            'observation_count': state[2], 'event_count': count}


def main(argv=None):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument('command', choices=('record', 'check'))
    parser.add_argument('--directory', required=True)
    parser.add_argument('--input')
    try:
        args = parser.parse_args(argv)
        if (args.command == 'record') != (args.input is not None):
            raise Invalid('invalid_arguments')
        directory = _private_directory(args.directory)
        observation = _observe(_bounded_json_file(args.input)) if args.command == 'record' else None
        previous_umask = os.umask(0o077)
        try:
            db = _open_database(directory) if args.command == 'record' else directory / 'paper-monitor-events.sqlite'
            report = (_record(db, *observation) if observation is not None else _check(db))
        finally:
            os.umask(previous_umask)
        code = 0
        report = {'schema_version': 1, 'source': 'paper_monitor_events',
                  'operation': args.command, 'exit_code': 0, **report}
    except (Invalid, ValueError, OSError, sqlite3.Error) as exc:
        code = 2 if isinstance(exc, (Invalid, ValueError)) else 4
        report = {'schema_version': 1, 'source': 'paper_monitor_events',
                  'operation': 'unavailable', 'status': 'unavailable', 'exit_code': code,
                  'error': 'monitor_events_unavailable' if code == 2 else 'monitor_events_storage_uncertain'}
    report['boundaries'] = {'live_enabled': False, 'orders_submitted': False,
                            'notification_delivered': False, 'production_ready': False}
    try:
        print(json.dumps(report, sort_keys=True), flush=True)
    except OSError:
        return 4
    return code


if __name__ == '__main__':
    raise SystemExit(main())
