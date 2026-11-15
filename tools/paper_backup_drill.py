"""Run a SYNTHETIC, network-forbidden PAPER backup and recovery verification.

The destination must be a new private directory. This creates its own capture
and account; it never accepts existing trading inputs. Recovery output remains
a non-executing archive. No source rebinding or live continuation is authorized.
"""
import argparse
import asyncio
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
import threading
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))

from trading_bot.paper_backup_io import BackupInterrupted

_BASE = datetime(2026, 11, 14, tzinfo=timezone.utc)
_VALUES = (100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100, 101, 102, 101, 100, 99, 98, 97)
_BUNDLE_FILES = {'.backup.lock', 'backup.json', 'account-database.bin', 'capture-database.bin'}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('paper_backup_drill_arguments_invalid')


class _Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


def _stop(stop):
    signum = stop() if stop is not None else 0
    if type(signum) is not int or signum not in (0, 2, 15):
        raise ValueError('paper_backup_drill_invalid_stop')
    if signum:
        raise _Interrupted(signum)


def _require(condition):
    if not condition:
        raise RuntimeError('paper_backup_drill_verification_failed')


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


@contextmanager
def _workspace(directory):
    """Create once; pin the output and its parent throughout this local drill."""
    parent_fd = directory_fd = None
    try:
        path = Path(directory).expanduser().absolute()
        if path.parent.resolve(strict=True) != path.parent or not path.name:
            raise ValueError('paper_backup_drill_unavailable')
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = _identity(os.fstat(parent_fd))
        if parent != _identity(path.parent.lstat()) or parent[3] != os.geteuid() or parent[2] & 0o022:
            raise ValueError('paper_backup_drill_unavailable')
        os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=parent_fd)
        os.fchmod(directory_fd, 0o700)
        identity = _identity(os.fstat(directory_fd))

        def check():
            if (_identity(path.parent.lstat()) != parent
                    or path.parent.resolve(strict=True) != path.parent
                    or _identity(path.lstat()) != identity
                    or _identity(os.fstat(directory_fd)) != identity):
                raise OSError('paper_backup_drill_output_changed')

        check()
        os.fsync(directory_fd)
        os.fsync(parent_fd)
        yield path, directory_fd, check
        check()
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if parent_fd is not None:
            os.close(parent_fd)


@contextmanager
def _network_forbidden():
    def forbidden(*args, **kwargs):
        raise RuntimeError('paper_backup_drill_network_forbidden')
    with ExitStack() as stack:
        for name in ('socket.socket.connect', 'socket.socket.connect_ex',
                     'socket.socket.sendto', 'socket.create_connection', 'socket.getaddrinfo'):
            stack.enter_context(patch(name, side_effect=forbidden))
        yield


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_digests(path):
    _require({entry.name for entry in path.iterdir()} == _BUNDLE_FILES)
    return {name: _file_digest(path / name) for name in sorted(_BUNDLE_FILES)}


def _fixture(rows, at):
    from trading_data.upbit_history import candles_hash
    return dict(mode='paper', symbol='BTC/KRW', timeframe='1m', candles=rows,
        data_provenance=dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
            unit_minutes=1, end_exclusive_ms=rows[-1][0] + 60000, retrieved_at=at.isoformat(),
            bar_count=len(rows), candles_sha256=candles_hash(rows), gap_policy='reject',
            missing_intervals=0, authenticated=False))


def _archived_values(path):
    """Inspect the verified .bin payload directly, without an executable layout."""
    from trading_bot.paper_stream_store import _read_snapshot
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        snapshot = _read_snapshot(connection)
        events = connection.execute(
            'SELECT substr(body,1,16385) FROM stream_events ORDER BY sequence LIMIT 121').fetchall()
        _require(len(events) == 120)
        fills = [event['fill'] for event in (json.loads(row[0]) for row in events)
                 if event['fill'] is not None]
        return snapshot, fills
    finally:
        connection.close()


@contextmanager
def _originals_unavailable(path, directory_fd, check):
    """Move only this drill's generated directories, restoring names on failure."""
    moved = []
    try:
        for name in ('capture', 'account'):
            check()
            original = path / name
            unavailable = path / (name + '-unavailable')
            if unavailable.exists() or unavailable.is_symlink():
                raise OSError('paper_backup_drill_output_changed')
            identity = _identity(original.lstat())
            _require(stat.S_ISDIR(identity[2]) and identity[3] == os.geteuid())
            os.rename(name, unavailable.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            moved.append((name, unavailable.name, identity))
            _require(not original.exists())
            _require(_identity(unavailable.lstat()) == identity)
        os.fsync(directory_fd)
        yield
    finally:
        for name, unavailable, identity in reversed(moved):
            check()
            if (path / name).exists() or (path / name).is_symlink():
                raise OSError('paper_backup_drill_restore_names_unconfirmed')
            if _identity((path / unavailable).lstat()) != identity:
                raise OSError('paper_backup_drill_restore_names_unconfirmed')
            os.rename(unavailable, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            _require(_identity((path / name).lstat()) == identity)
        os.fsync(directory_fd)


async def run_drill(directory, *, stop=None):
    """Return a private summary only after real paired backup/restore succeeds."""
    _stop(stop)
    with _network_forbidden(), _workspace(directory) as (path, directory_fd, check):
        from trading_bot import paper_backup
        from trading_bot.market_capture import collect_capture
        from trading_bot.market_capture_store import CaptureUnavailable, capture_writer, read_capture
        from trading_bot.paper_runner import PaperRunner, PaperRunConfig
        from trading_bot.paper_stream import audit_stream, run_stream
        from trading_bot.paper_stream_store import StreamUnavailable, read_stream, stream_reader
        from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
        from trading_data import Candle

        start = int(_BASE.timestamp()) * 1000
        rows = [[start + i * 60000, str(_VALUES[i % len(_VALUES)]),
                 str(_VALUES[i % len(_VALUES)] + 1), str(_VALUES[i % len(_VALUES)] - 1),
                 str(_VALUES[i % len(_VALUES)]), '20'] for i in range(240)]
        config = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=120,
                      retention_bars=200, poll_seconds=15)
        capture, account = path / 'capture', path / 'account'
        now = [_BASE + timedelta(minutes=120, seconds=6)]
        with capture_writer(capture, config=config, now=now[0]) as owner:
            owner.apply(_fixture(rows[:120], now[0]), at=now[0])
        operations = []
        for boundary in (120, 180, 240):
            _stop(stop)
            check()
            if boundary > 120:
                now[0] = _BASE + timedelta(minutes=boundary, seconds=6)
                with capture_writer(capture) as owner:
                    owner.apply(_fixture(rows[boundary - 61:boundary], now[0]), at=now[0])
            source_before = _file_digest(capture / 'capture.sqlite3')
            operation = await run_stream(account, capture=capture if boundary == 120 else None,
                max_polls=1, clock=lambda: now[0], stop=stop)
            _stop(stop)
            _require(operation['exit_code'] == 0)
            _require(operation['observation']['cursor']['bars_processed'] == boundary - 120)
            _require(_file_digest(capture / 'capture.sqlite3') == source_before)
            operations.append(operation)
        # Quiesce the actual capture owner after consumption. All owner contexts
        # have exited before acquiring the paired backup's read-only locks.
        with capture_writer(capture) as owner:
            owner.stop(now[0])
        original_hashes = {
            'account': _file_digest(account / 'stream.sqlite3'),
            'capture': _file_digest(capture / 'capture.sqlite3'),
        }
        snapshot, source = read_stream(account), read_capture(capture)
        original_audit = await audit_stream(account, stop=stop)
        _stop(stop)
        _require(original_audit['status'] == 'verified_prefix')
        _require(original_audit['events_verified'] == 120)
        _require(snapshot['control']['phase'] == 'stopped' and source['state']['phase'] == 'stopped')
        with stream_reader(account, audit=True) as reader:
            fills = [event['fill'] for event in reader.events() if event['fill'] is not None]

        settings = PaperRunConfig(**snapshot['manifest']['config'])
        warmed = PaperRunner(settings)._strategy()
        with localcontext(PAPER_CALCULATION_CONTEXT):
            for row in rows[:120]:
                warmed.on_candle(Candle(*row))
            expected = await PaperRunner(settings, strategy_factory=lambda: warmed)._run(
                rows[120:], return_state=True)
        _require(fills == expected['exchange']['trades'])
        _require(snapshot['engine']['cash'] == expected['exchange']['balances']['KRW'])
        _require(snapshot['engine']['holding'] == expected['exchange']['balances']['BTC'])
        _require(snapshot['engine']['total_fees'] == expected['exchange']['total_fees'])
        for key in ('pending_signal', 'risk_day', 'daily_start_equity', 'peak_equity',
                    'daily_loss_halted', 'drawdown_halted'):
            _require(snapshot['engine'][key] == expected[key])

        _stop(stop)
        check()
        backup, recovery = path / 'backup', path / 'recovery'
        created = await paper_backup.create_backup(account, backup, stop=stop, clock=lambda: now[0])
        _stop(stop)
        _require(created['status'] == 'verified_backup')
        backup_before = _bundle_digests(backup)
        with _originals_unavailable(path, directory_fd, check):
            _stop(stop)
            archived_check = await paper_backup.check_backup(backup, stop=stop)
            restored = await paper_backup.verify_restore(backup, recovery, stop=stop)
            restored_check = await paper_backup.check_backup(recovery, stop=stop)
            _stop(stop)
            _require(archived_check['status'] == 'verified_backup')
            _require(restored['status'] == 'verified_restore')
            _require(restored_check['status'] == 'verified_backup')
            for report in (created, archived_check, restored, restored_check):
                _require(report['events_verified'] == 120)
                _require(report['stream_id'] == snapshot['manifest']['stream_id'])
                _require(report['capture_id'] == source['state']['capture_id'])
                _require(report['boundaries']['execution_authorized'] is False)
                _require(report['boundaries']['source_rebinding'] is False)
                _require(report['boundaries']['rollback_protection'] is False)
            for bundle in (backup, recovery):
                before = _bundle_digests(bundle)
                _require(_file_digest(bundle / 'account-database.bin') == original_hashes['account'])
                _require(_file_digest(bundle / 'capture-database.bin') == original_hashes['capture'])
                archived_snapshot, archived_fills = _archived_values(bundle / 'account-database.bin')
                _require(archived_snapshot == snapshot and archived_fills == fills)
                try:
                    await run_stream(bundle, max_polls=1, clock=lambda: now[0], stop=stop)
                except StreamUnavailable:
                    pass
                else:
                    raise RuntimeError('paper_backup_drill_executable_copy')
                try:
                    collect_capture(bundle, max_polls=1, clock=lambda: now[0], stop=stop)
                except CaptureUnavailable:
                    pass
                else:
                    raise RuntimeError('paper_backup_drill_executable_copy')
                _require(_bundle_digests(bundle) == before)

        _stop(stop)
        check()
        _require(_file_digest(account / 'stream.sqlite3') == original_hashes['account'])
        _require(_file_digest(capture / 'capture.sqlite3') == original_hashes['capture'])
        _require(read_stream(account) == snapshot and read_capture(capture) == source)
        _require(_bundle_digests(backup) == backup_before)
        _require(len(fills) == 18 and source['state']['accepted_bars_total'] == 240)
        summary = dict(schema_version=1, source='paper_backup_drill', mode='paper',
            status='verified_recovery_drill', synthetic=True, network_forbidden=True,
            live_enabled=False, execution_authorized=False, source_rebinding=False,
            rollback_protection=False, bootstrap_bars=120, consumed_bars=120,
            source_bars=240, fills=len(fills), events_verified=120, verification='verified_prefix',
            original_input_unchanged=True, archive_verified_without_originals=True,
            restored_payloads_exact=True, archived_snapshots_exact=True,
            executable_layout_refused=True, original_engine_exact_match=True)
        report = dict(summary=summary, operations=operations, original_snapshot=snapshot,
            capture_snapshot=source, fills=fills, original_audit=original_audit,
            backup=created, archived_check=archived_check, restore=restored,
            restored_check=restored_check, original_sha256=original_hashes)
        body = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + '\n').encode('utf-8')
        _require(len(body) <= 2 * 1024 * 1024)
        _stop(stop)
        check()
        descriptor = os.open('drill-report.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
        with os.fdopen(descriptor, 'wb') as destination:
            os.fchmod(destination.fileno(), 0o600)
            destination.write(body)
            destination.flush()
            os.fsync(destination.fileno())
        os.fsync(directory_fd)
        check()
        _stop(stop)
        return summary


def main(argv=None):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--directory', required=True, help='New private directory for synthetic artifacts')
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print(json.dumps({'error': 'paper_backup_drill_arguments_invalid'}))
        return 2
    received, previous = [], {}

    def record_signal(signum, frame):
        if not received:
            received.append(int(signum))

    def stop():
        return received[0] if received else 0

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, record_signal)
        with redirect_stdout(sys.stderr):
            report = asyncio.run(run_drill(args.directory, stop=stop))
        code = 0
    except (_Interrupted, BackupInterrupted) as error:
        code, report = 128 + error.signum, {'error': 'paper_backup_drill_interrupted_check_output'}
    except (KeyboardInterrupt, asyncio.CancelledError):
        code, report = 130, {'error': 'paper_backup_drill_interrupted_check_output'}
    except (ValueError, FileExistsError, FileNotFoundError, PermissionError):
        code, report = 2, {'error': 'paper_backup_drill_unavailable'}
    except OSError:
        code, report = 4, {'error': 'paper_backup_drill_storage_failed_check_output'}
    except Exception:
        code, report = 4, {'error': 'paper_backup_drill_failed_check_output'}
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    # A received shutdown request cannot erase an unconfirmed storage/failure
    # outcome. Keep exit 4 and its inspection requirement for retained output.
    if received and code != 4:
        code, report = 128 + received[0], {'error': 'paper_backup_drill_interrupted_check_output'}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
