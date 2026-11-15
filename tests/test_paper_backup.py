"""Paired archive and recovery verification against real PAPER databases."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))
from trading_bot.market_capture_store import capture_writer
from trading_bot.paper_stream import run_stream
from trading_bot.paper_stream_store import read_stream, stream_writer
from trading_data.upbit_history import candles_hash

BASE = datetime(2026, 11, 15, tzinfo=timezone.utc)
START = int(BASE.timestamp()) * 1000
CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=50,
              retention_bars=200, poll_seconds=15)
OPTIONS = dict(initial_balance='1000', fee_pct='0', slippage_pct='0',
               position_size_pct='100', max_daily_loss_pct='100', max_drawdown_pct='100')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(encode(value)).hexdigest()


def fixture(first, count, now):
    rows = []
    for index in range(first, first + count):
        values = ('100', '101', '99', '100') if index < 50 else (
            ('105', '115', '100', '110') if index == 50 else ('120', '125', '115', '120'))
        rows.append([START + index * 60000, *values, '1'])
    return dict(mode='paper', symbol='BTC/KRW', timeframe='1m', candles=rows,
        data_provenance=dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
            unit_minutes=1, end_exclusive_ms=rows[-1][0] + 60000,
            retrieved_at=now.isoformat(), bar_count=count, candles_sha256=candles_hash(rows),
            gap_policy='reject', missing_intervals=0, authenticated=False))


class PaperBackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_bot.paper_backup'),
                             'paired PAPER backup coordinator is not implemented')
        self.module = importlib.import_module('trading_bot.paper_backup')
        self.io = importlib.import_module('trading_bot.paper_backup_io')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.account, self.capture = self.root / 'account', self.root / 'capture'
        self.backup, self.restored = self.root / 'backup', self.root / 'recovered'
        self.now = BASE + timedelta(minutes=50, seconds=6)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        with capture_writer(self.capture, config=CONFIG, now=self.now) as owner:
            owner.apply(fixture(0, 50, self.now), at=self.now)
        first = await run_stream(self.account, capture=self.capture, config_options=OPTIONS,
                                 max_polls=1, clock=lambda: self.now)
        self.assertEqual(first['exit_code'], 0)
        self.now = BASE + timedelta(minutes=52, seconds=6)
        with capture_writer(self.capture) as owner:
            owner.apply(fixture(49, 3, self.now), at=self.now)
        resumed = await run_stream(self.account, max_polls=1, clock=lambda: self.now)
        self.assertEqual(resumed['exit_code'], 0)
        with capture_writer(self.capture) as owner:
            owner.stop(self.now)
        self.now += timedelta(seconds=1)

    async def create(self):
        return await self.module.create_backup(self.account, self.backup, clock=lambda: self.now)

    def manifest(self, directory=None):
        return json.loads(((directory or self.backup) / 'backup.json').read_bytes())

    def write_manifest(self, data):
        (self.backup / 'backup.json').write_bytes(encode(data))

    def original_bytes(self):
        return ((self.account / 'stream.sqlite3').read_bytes(),
                (self.capture / 'capture.sqlite3').read_bytes())

    async def test_create_check_and_recovered_copy_preserve_real_payloads(self):
        before = self.original_bytes()
        result = await self.create()
        self.assertEqual(result['status'], 'verified_backup')
        self.assertEqual(result['events_verified'], 2)
        self.assertFalse(result['boundaries']['execution_authorized'])
        self.assertEqual(set(p.name for p in self.backup.iterdir()),
                         {'.backup.lock', 'backup.json', 'account-database.bin', 'capture-database.bin'})
        for name, data in zip(('account-database.bin', 'capture-database.bin'), before):
            self.assertEqual((self.backup / name).read_bytes(), data)
            self.assertEqual(result['payloads'][name],
                             dict(size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        checked = await self.module.check_backup(self.backup)
        self.assertEqual(checked['stream_id'], result['stream_id'])
        self.assertTrue(checked['read_only'])
        restored = await self.module.verify_restore(self.backup, self.restored)
        self.assertEqual(restored['status'], 'verified_restore')
        self.assertEqual(restored['payloads'], result['payloads'])
        self.assertEqual((self.backup / 'backup.json').read_bytes(),
                         (self.restored / 'backup.json').read_bytes())
        self.assertEqual(before, self.original_bytes())

    async def test_read_only_check_and_restore_do_not_follow_recorded_original_paths(self):
        await self.create()
        inputs = {p.name: p.read_bytes() for p in self.backup.iterdir()}
        self.account.rename(self.root / 'account-unavailable')
        self.capture.rename(self.root / 'capture-unavailable')
        self.assertEqual((await self.module.check_backup(self.backup))['events_verified'], 2)
        self.assertEqual((await self.module.verify_restore(self.backup, self.restored))['events_verified'], 2)
        self.assertEqual(inputs, {p.name: p.read_bytes() for p in self.backup.iterdir()})
        from trading_bot.market_capture_store import read_capture, CaptureUnavailable
        from trading_bot.paper_stream_store import StreamUnavailable
        with self.assertRaises(StreamUnavailable):
            read_stream(self.restored)
        with self.assertRaises(CaptureUnavailable):
            read_capture(self.restored)

    async def test_actual_account_owner_refuses_before_output_creation(self):
        with stream_writer(self.account):
            with self.assertRaises(self.io.BackupUnavailable):
                await self.create()
        self.assertFalse(self.backup.exists())

    async def test_actual_capture_owner_refuses_even_when_saved_phase_is_stopped(self):
        with capture_writer(self.capture):
            with self.assertRaises(self.io.BackupUnavailable):
                await self.create()
        self.assertFalse(self.backup.exists())

    async def test_original_ownership_is_held_during_payload_copy(self):
        original = self.module.copy_file
        attempts = []
        def inspect(*args, **kwargs):
            with self.assertRaises(ValueError):
                with stream_writer(self.account):
                    pass
            with self.assertRaises(ValueError):
                with capture_writer(self.capture):
                    pass
            attempts.append(True)
            return original(*args, **kwargs)
        with patch.object(self.module, 'copy_file', side_effect=inspect):
            await self.create()
        self.assertEqual(len(attempts), 2)

    async def test_terminal_reasons_and_trusted_times_remain_exact(self):
        with stream_writer(self.account) as owner:
            owner.latch('clock_regression')
        with capture_writer(self.capture) as owner:
            owner.fail('rate_blocked', at=self.now)
        self.now += timedelta(seconds=1)
        prior = read_stream(self.account)
        await self.create()
        await self.module.verify_restore(self.backup, self.restored)
        saved = self.manifest(self.restored)
        self.assertEqual(saved['account']['control'], prior['control'])
        self.assertEqual(saved['account']['control']['last_error'], 'clock_regression')
        self.assertEqual(saved['capture']['state']['last_error'], 'rate_blocked')
        self.assertFalse(saved['boundaries']['execution_authorized'])

    async def test_hot_journal_is_refused_without_source_repair(self):
        before = self.original_bytes()
        journal = self.account / 'stream.sqlite3-journal'
        journal.write_bytes(b'uncertain journal evidence')
        journal.chmod(0o600)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.create()
        self.assertEqual(before, self.original_bytes())
        self.assertEqual(journal.read_bytes(), b'uncertain journal evidence')
        self.assertFalse(self.backup.exists())

    async def test_source_substitution_before_backup_refuses(self):
        self.capture.rename(self.root / 'moved-capture')
        with self.assertRaises(self.io.BackupUnavailable):
            await self.create()
        self.assertFalse(self.backup.exists())

    async def test_source_substitution_during_copy_is_uncertain_not_success(self):
        original = self.module.copy_file
        moved = []
        def substitute(*args, **kwargs):
            result = original(*args, **kwargs)
            if not moved:
                self.capture.rename(self.root / 'moved-capture')
                moved.append(True)
            return result
        with patch.object(self.module, 'copy_file', side_effect=substitute):
            with self.assertRaises(self.io.BackupStorageError):
                await self.create()
        self.assertTrue(self.backup.exists())
        self.assertFalse((self.backup / 'backup.json').exists())

    async def test_existing_and_nested_destinations_never_change_inputs(self):
        before = self.original_bytes()
        for output in (self.account, self.capture, self.account / 'backup', self.capture / 'backup'):
            with self.subTest(output=output.name):
                with self.assertRaises(self.io.BackupUnavailable):
                    await self.module.create_backup(self.account, output, clock=lambda: self.now)
        self.assertEqual(before, self.original_bytes())
        await self.create()
        prior = {p.name: p.read_bytes() for p in self.backup.iterdir()}
        with self.assertRaises(self.io.BackupUnavailable):
            await self.create()
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.verify_restore(self.backup, self.backup / 'nested')
        self.assertEqual(prior, {p.name: p.read_bytes() for p in self.backup.iterdir()})

    async def test_payload_corruption_and_truncation_are_refused(self):
        await self.create()
        path = self.backup / 'account-database.bin'
        original = path.read_bytes()
        for changed in (original[:-1], original[:100] + b'X' + original[101:], original + b'X'):
            with self.subTest(length=len(changed)):
                path.write_bytes(changed)
                with self.assertRaises(self.io.BackupUnavailable):
                    await self.module.check_backup(self.backup)
        path.write_bytes(original)

    async def test_manifest_duplicate_fields_noncanonical_types_and_extra_members_refused(self):
        await self.create()
        path = self.backup / 'backup.json'
        original = path.read_bytes()
        value = json.loads(original)
        bad = [b'{"version":1,' + original[1:], original + b'\n']
        value['version'] = True
        bad.append(encode(value))
        value = json.loads(original)
        value['boundaries']['execution_authorized'] = True
        bad.append(encode(value))
        for changed in bad:
            path.write_bytes(changed)
            with self.assertRaises(self.io.BackupUnavailable):
                await self.module.check_backup(self.backup)
        path.write_bytes(original)
        (self.backup / 'unexpected').touch(mode=0o600)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)

    async def test_derived_manifest_evidence_must_match_actual_payload(self):
        await self.create()
        meta = self.manifest()
        meta['account']['control']['phase'] = 'running'
        self.write_manifest(meta)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)

    async def test_digest_consistent_forged_cash_is_rejected_by_full_replay(self):
        await self.create()
        db = self.backup / 'account-database.bin'
        with sqlite3.connect(db) as connection:
            head = json.loads(connection.execute('SELECT body FROM stream_head').fetchone()[0])
            head['engine']['cash'] = str(Decimal(head['engine']['cash']) + 1)
            head['engine']['equity'] = str(Decimal(head['engine']['equity']) + 1)
            head['engine_sha256'] = sha(head['engine'])
            seq, raw = connection.execute('SELECT sequence,body FROM stream_events ORDER BY sequence DESC LIMIT 1').fetchone()
            event = json.loads(raw)
            event['engine_sha256'] = head['engine_sha256']
            event['sha256'] = sha({k: v for k, v in event.items() if k != 'sha256'})
            head['event_head_sha256'] = event['sha256']
            connection.execute('UPDATE stream_events SET body=?,sha256=? WHERE sequence=?',
                               (encode(event).decode(), event['sha256'], seq))
            connection.execute('UPDATE stream_head SET body=?,sha256=?', (encode(head).decode(), sha(head)))
        meta = self.manifest()
        meta['payloads']['account-database.bin'] = dict(size=db.stat().st_size,
                                                      sha256=hashlib.sha256(db.read_bytes()).hexdigest())
        meta['account']['engine_sha256'] = head['engine_sha256']
        meta['account']['event_head_sha256'] = head['event_head_sha256']
        self.write_manifest(meta)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)

    async def test_fingerprint_change_cannot_be_bypassed_by_backup_verification(self):
        await self.create()
        with patch('trading_bot.paper_stream._engine_fingerprint', return_value='0' * 64):
            with self.assertRaises(self.io.BackupUnavailable):
                await self.module.check_backup(self.backup)

    async def test_exclusive_bundle_owner_refuses_check_and_restore(self):
        await self.create()
        fd = os.open(self.backup / '.backup.lock', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.verify_restore(self.backup, self.restored)
        self.assertFalse(self.restored.exists())

    async def test_unsafe_payload_permissions_and_links_refuse(self):
        await self.create()
        path = self.backup / 'capture-database.bin'
        path.chmod(0o644)
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)
        path.chmod(0o600)
        os.link(path, self.root / 'extra-link')
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)

    async def test_invalid_or_regressed_creation_clock_leaves_no_output(self):
        for now in (BASE.replace(tzinfo=None), BASE, 'invalid'):
            with self.subTest(now=now):
                with self.assertRaises(self.io.BackupUnavailable):
                    await self.module.create_backup(self.account, self.backup, clock=lambda: now)
                self.assertFalse(self.backup.exists())

    async def test_interruption_before_output_and_during_publication_is_explicit(self):
        with self.assertRaises(self.io.BackupInterrupted) as caught:
            await self.module.create_backup(self.account, self.backup, clock=lambda: self.now, stop=lambda: 2)
        self.assertEqual(caught.exception.signum, 2)
        self.assertFalse(self.backup.exists())
        with self.assertRaises(self.io.BackupInterrupted) as caught:
            await self.module.create_backup(self.account, self.backup, clock=lambda: self.now,
                stop=lambda: 15 if (self.backup / 'backup.json').exists() else 0)
        self.assertEqual(caught.exception.signum, 15)
        self.assertTrue(self.backup.exists())

    async def test_fsync_failure_preserves_possible_output_and_reports_uncertainty(self):
        before = self.original_bytes()
        with patch('trading_bot.paper_backup_io.os.fsync', side_effect=OSError('private failure path')):
            with self.assertRaises(self.io.BackupStorageError):
                await self.create()
        self.assertEqual(before, self.original_bytes())
        self.assertTrue(self.backup.exists())

    async def test_original_move_during_final_sync_cannot_report_success(self):
        before = self.original_bytes()
        sync = self.module.PrivateDirectory.sync
        for name in ('account', 'capture'):
            original = getattr(self, name)
            moved = self.root / (name + '-moved')
            self.backup = self.root / ('backup-' + name)
            calls = []

            def move_after_final_sync(directory):
                sync(directory)
                calls.append(True)
                if len(calls) == 2:
                    original.rename(moved)

            with self.subTest(source=name):
                try:
                    with patch.object(self.module.PrivateDirectory, 'sync', move_after_final_sync):
                        with self.assertRaises(self.io.BackupStorageError):
                            await self.create()
                    self.assertEqual(len(calls), 2)
                    self.assertFalse(original.exists())
                    self.assertTrue(self.backup.exists(), 'Possible output must be preserved')
                    archived = await self.module.check_backup(self.backup)
                    self.assertEqual(archived['status'], 'verified_backup')
                finally:
                    if moved.exists():
                        moved.rename(original)
        self.assertEqual(before, self.original_bytes())

    async def test_invalid_restore_input_never_creates_destination(self):
        await self.create()
        (self.backup / 'backup.json').write_bytes(b'{}')
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.verify_restore(self.backup, self.restored)
        self.assertFalse(self.restored.exists())

    async def test_destination_parent_replacement_during_audit_cannot_redirect_creation(self):
        parent = self.root / 'output-parent'
        parent.mkdir(mode=0o700)
        self.backup = parent / 'backup'
        moved = self.root / 'original-parent'
        verify = self.module._verify
        switched = []
        async def replace_after_audit(*args, **kwargs):
            result = await verify(*args, **kwargs)
            if not switched:
                parent.rename(moved)
                parent.mkdir(mode=0o700)
                switched.append(True)
            return result
        with patch.object(self.module, '_verify', side_effect=replace_after_audit):
            with self.assertRaises(self.io.BackupUnavailable):
                await self.create()
        self.assertFalse((parent / 'backup').exists())
        self.assertFalse((moved / 'backup').exists())

    async def test_restore_parent_replacement_during_input_audit_is_refused_before_output(self):
        await self.create()
        parent = self.root / 'output-parent'
        parent.mkdir(mode=0o700)
        self.restored = parent / 'recovered'
        moved = self.root / 'original-parent'
        verify = self.module._verify
        switched = []
        async def replace_after_audit(*args, **kwargs):
            result = await verify(*args, **kwargs)
            if not switched:
                parent.rename(moved)
                parent.mkdir(mode=0o700)
                switched.append(True)
            return result
        with patch.object(self.module, '_verify', side_effect=replace_after_audit):
            with self.assertRaises(self.io.BackupUnavailable):
                await self.module.verify_restore(self.backup, self.restored)
        self.assertFalse((parent / 'recovered').exists())
        self.assertFalse((moved / 'recovered').exists())

    async def test_process_death_during_copy_leaves_refused_artifact_and_releases_owners(self):
        before = self.original_bytes()
        code = '''
import asyncio, os, sys
from datetime import datetime
from unittest.mock import patch
from trading_bot import paper_backup, paper_backup_io
write = os.write
def die_after_write(fd, data):
    written = write(fd, data)
    os.fsync(fd)
    os._exit(19)
with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \\
     patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \\
     patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')), \\
     patch.object(paper_backup_io.os, 'write', side_effect=die_after_write):
    asyncio.run(paper_backup.create_backup(sys.argv[1], sys.argv[2],
        clock=lambda: datetime.fromisoformat(sys.argv[3])))
'''
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / part) for part in
            ('packages/core', 'packages/data', 'packages/strategy', 'apps/server')))
        outcome = subprocess.run([sys.executable, '-c', code, str(self.account), str(self.backup),
                                  self.now.isoformat()], env=environment, capture_output=True,
                                 timeout=20, check=False)
        self.assertEqual(outcome.returncode, 19, outcome.stderr.decode())
        self.assertEqual(outcome.stdout, b'')
        self.assertTrue((self.backup / 'account-database.bin').exists())
        self.assertFalse((self.backup / 'backup.json').exists())
        with self.assertRaises(self.io.BackupUnavailable):
            await self.module.check_backup(self.backup)
        self.assertEqual(before, self.original_bytes())
        with stream_writer(self.account), capture_writer(self.capture):
            pass


if __name__ == '__main__':
    unittest.main()
