"""Local PAPER monitor event acknowledgement; no external alert sender."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import quote

DB_NAME = 'paper-monitor-events.sqlite'
MAX_EVENTS = 10000
REASON = re.compile(r'[A-Za-z0-9가-힣 _.,:()/+\-]{8,200}\Z')


class Refused(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused('arguments_invalid')


def _args(argv):
    parser = Parser(add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    for name in ('sync', 'list', 'ack'):
        sub = commands.add_parser(name, add_help=False, allow_abbrev=False)
        sub.add_argument('--directory', required=True)
        if name == 'ack':
            sub.add_argument('--sequence', type=int, required=True)
            sub.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    if args.command == 'ack' and (not 1 <= args.sequence <= MAX_EVENTS
                                  or not REASON.fullmatch(args.reason)):
        raise Refused('invalid_ack')
    return args


def _root(raw):
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('invalid_directory')
    pos = Path('/')
    for component in path.parts[1:]:
        pos /= component
        info = os.lstat(pos)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise Refused('invalid_directory')
    info = os.stat(path)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused('invalid_directory')
    return path


def _open_existing(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o600):
            raise Refused('invalid_journal')
        other = os.stat(path, follow_symlinks=False)
        if (st.st_dev, st.st_ino) != (other.st_dev, other.st_ino):
            raise Refused('journal_replaced')
        return st.st_dev, st.st_ino
    finally:
        os.close(fd)


def _connect(path, read_only=False):
    mode = 'ro' if read_only else 'rw'
    conn = sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=' + mode,
                           uri=True, timeout=3, isolation_level=None)
    conn.execute('PRAGMA busy_timeout=3000')
    if not read_only:
        conn.execute('PRAGMA synchronous=FULL')
    return conn


def _require_origin(conn):
    state = conn.execute('SELECT status, signature, observations FROM state WHERE singleton=1').fetchone()
    if (state is None or state[0] not in ('healthy', 'attention_required')
            or type(state[1]) is not str or type(state[2]) is not int or state[2] < 1):
        raise Refused('invalid_monitor_state')
    count = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    if not 1 <= count <= MAX_EVENTS:
        raise Refused('invalid_monitor_events')
    first, last = conn.execute('SELECT MIN(sequence), MAX(sequence) FROM events').fetchone()
    if first != 1 or last != count:
        raise Refused('incomplete_monitor_events')
    final = conn.execute('SELECT status, signature FROM events WHERE sequence=?', (last,)).fetchone()
    if final != state[:2]:
        raise Refused('head_mismatch')
    return count


def _init(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS alert_queue ('
                 'sequence INTEGER PRIMARY KEY, status TEXT NOT NULL, '
                 'signature TEXT NOT NULL, observed_at TEXT NOT NULL, '
                 'acknowledged_at TEXT, reason TEXT, '
                 'CHECK ((acknowledged_at IS NULL AND reason IS NULL) OR '
                 '(acknowledged_at IS NOT NULL AND reason IS NOT NULL)))')


def _sync(conn):
    count = _require_origin(conn)
    _init(conn)
    rows = conn.execute('SELECT sequence,status,signature,observed_at FROM events ORDER BY sequence').fetchall()
    if len(rows) != count:
        raise Refused('inconsistent_events')
    for i, (seq, status, signature, timestamp) in enumerate(rows, start=1):
        if (type(seq) is not int or seq != i or status not in ('healthy', 'attention_required')
                or type(signature) is not str or not 2 <= len(signature) <= 2048
                or type(timestamp) is not str or not 10 <= len(timestamp) <= 64):
            raise Refused('invalid_event')
        existing = conn.execute('SELECT status,signature,observed_at FROM alert_queue WHERE sequence=?', (seq,)).fetchone()
        if existing is not None and existing != (status, signature, timestamp):
            raise Refused('alert_source_changed')
        if existing is None:
            conn.execute('INSERT INTO alert_queue(sequence,status,signature,observed_at) VALUES(?,?,?,?)',
                         (seq, status, signature, timestamp))
    if conn.execute('SELECT COUNT(*) FROM alert_queue').fetchone()[0] != count:
        raise Refused('alert_queue_conflict')
    return {'total_events': count,
            'pending': conn.execute('SELECT COUNT(*) FROM alert_queue WHERE acknowledged_at IS NULL').fetchone()[0]}


def _require_synced(conn):
    count = _require_origin(conn)
    queued = conn.execute('SELECT COUNT(*) FROM alert_queue').fetchone()[0]
    if queued != count:
        raise Refused('sync_required')
    mismatches = conn.execute('SELECT COUNT(*) FROM events e LEFT JOIN alert_queue a '
                              'ON e.sequence=a.sequence WHERE a.sequence IS NULL OR '
                              'e.status != a.status OR e.signature != a.signature OR '
                              'e.observed_at != a.observed_at').fetchone()[0]
    if mismatches:
        raise Refused('source_changed')


def _list(conn):
    _require_synced(conn)
    rows = conn.execute('SELECT sequence,status,observed_at FROM alert_queue '
                        'WHERE acknowledged_at IS NULL ORDER BY sequence LIMIT 100').fetchall()
    total = conn.execute('SELECT COUNT(*) FROM alert_queue WHERE acknowledged_at IS NULL').fetchone()[0]
    return {'pending': total,
            'items': [{'sequence': seq, 'status': status, 'observed_at': observed_at}
                      for seq, status, observed_at in rows],
            'truncated': total > len(rows)}


def _ack(conn, seq, reason):
    _require_synced(conn)
    row = conn.execute('SELECT acknowledged_at FROM alert_queue WHERE sequence=?', (seq,)).fetchone()
    if row is None:
        raise Refused('unknown_alert')
    if row[0] is not None:
        raise Refused('already_acknowledged')
    observed = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    updated = conn.execute('UPDATE alert_queue SET acknowledged_at=?,reason=? '
                           'WHERE sequence=? AND acknowledged_at IS NULL', (observed, reason, seq)).rowcount
    if updated != 1:
        raise Refused('ack_conflict')
    return {'acknowledged_sequence': seq, 'acknowledged_at': observed}


def operate(args):
    folder = _root(args.directory)
    path = folder / DB_NAME
    identity = _open_existing(path)
    with closing(_connect(path, read_only=args.command == 'list')) as conn:
        try:
            if args.command != 'list':
                conn.execute('BEGIN IMMEDIATE')
            if args.command == 'sync':
                body = _sync(conn)
            elif args.command == 'list':
                body = _list(conn)
            else:
                body = _ack(conn, args.sequence, args.reason)
            if _open_existing(path) != identity:
                raise Refused('journal_replaced')
            if args.command != 'list':
                conn.execute('COMMIT')
        except BaseException:
            if args.command != 'list':
                conn.execute('ROLLBACK')
            raise
    return body


def main(argv=None):
    try:
        args = _args(argv)
        result = operate(args)
        code, status = 0, 'complete'
    except (Refused, ValueError, OSError, sqlite3.Error):
        code, status, result = 2, 'unavailable', {}
    except Exception:
        code, status, result = 4, 'uncertain', {}
    report = {'schema_version': 1, 'source': 'paper_alert_queue', 'status': status,
              'exit_code': code, **result, 'boundaries': {'live_enabled': False,
              'external_delivery': False, 'broker_orders': False,
              'production_ready': False}}
    try:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)
    except OSError:
        return 4
    return code


if __name__ == '__main__':
    raise SystemExit(main())
