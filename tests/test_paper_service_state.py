"""Native pair preflight and private service evidence; no provider traffic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import errno
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
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

CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=50,
              retention_bars=200, poll_seconds=15)
OPTIONS = dict(initial_balance='1000', fee_pct='0', slippage_pct='0',
               position_size_pct='100', max_daily_loss_pct='100', max_drawdown_pct='100')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def fixture(origin, first, count, now):
    rows = []
    for index in range(first, first + count):
        values = ('100', '101', '99', '100') if index < 50 else (
            ('105', '115', '100', '110') if index == 50 else ('120', '125', '115', '120'))
        rows.append([origin + index * 60000, *values, '1'])
    return dict(mode='paper', symbol='BTC/KRW', timeframe='1m', candles=rows,
        data_provenance=dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
            unit_minutes=1, end_exclusive_ms=rows[-1][0] + 60000,
            retrieved_at=now.isoformat(), bar_count=count, candles_sha256=candles_hash(rows),
            gap_policy='reject', missing_intervals=0, authenticated=False))


class PaperServiceStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_bot.paper_service_state'),
                             'private PAPER service state is not implemented')
        self.module = importlib.import_module('trading_bot.paper_service_state')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.account, self.capture = self.root / 'account', self.root / 'capture'
        self.service = self.root / 'service'
        self.now = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
        self.origin = int((self.now - timedelta(minutes=52)).timestamp()) * 1000
        self.now += timedelta(seconds=6)
        for target in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.sendto'):
            guard = patch(target, side_effect=AssertionError('network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        original_connect = socket.socket.connect
        def local_connect(sock, address):
            if sock.family != socket.AF_UNIX:
                raise AssertionError('network forbidden')
            return original_connect(sock, address)
        guard = patch('socket.socket.connect', new=local_connect)
        guard.start()
        self.addCleanup(guard.stop)
        initial = self.now - timedelta(minutes=2)
        with capture_writer(self.capture, config=CONFIG, now=initial) as owner:
            owner.apply(fixture(self.origin, 0, 50, initial), at=initial)
        created = await run_stream(self.account, capture=self.capture, config_options=OPTIONS,
                                   max_polls=1, clock=lambda: initial)
        self.assertEqual(created['exit_code'], 0)
        with capture_writer(self.capture) as owner:
            owner.apply(fixture(self.origin, 49, 3, self.now), at=self.now)
        continued = await run_stream(self.account, max_polls=1, clock=lambda: self.now)
        self.assertEqual(continued['exit_code'], 0)
        with capture_writer(self.capture) as owner:
            owner.stop(self.now)

    async def configure(self, **kwargs):
        return await self.module.configure_service(self.account, self.service, **kwargs)

    def originals(self):
        return ((self.account / 'stream.sqlite3').read_bytes(),
                (self.capture / 'capture.sqlite3').read_bytes())

    def final_result(self, service, attempt, *, cleanup=True):
        return dict(schema_version=1, source='paper_service', operation='run', mode='paper',
                    live_enabled=False, status='stopped', exit_code=143,
                    service_id=service.manifest['service_id'], attempt_id=attempt.attempt_id,
                    cleanup_confirmed=cleanup, children={},
                    boundaries=dict(data_readiness='not_assessed', live_execution=False))

    async def new_attempt(self):
        await self.configure()
        prepared = await self.module.prepare_pair(self.account)
        service = self.module.ServiceDirectory(self.service, exclusive=True)
        self.addCleanup(service.close)
        return service, service.new_attempt(prepared['audit'])

    def require_unix_socket(self):
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as error:
            if error.errno == errno.EPERM and os.environ.get('PAPER_SERVICE_REQUIRE_SOCKET') != '1':
                self.skipTest('AF_UNIX socket creation denied by executor policy (EPERM); Linux CI required')
            raise
        else:
            probe.close()

    async def test_prepare_replays_real_pair_without_modification(self):
        before = self.originals()
        prepared = await self.module.prepare_pair(self.account)
        self.assertEqual(set(prepared), {'pair', 'audit'})
        self.assertEqual(prepared['audit']['status'], 'verified_prefix')
        self.assertEqual(prepared['audit']['events_verified'], 2)
        self.assertEqual(prepared['pair']['account_path'], str(self.account))
        self.assertEqual(prepared['pair']['capture_path'], str(self.capture))
        self.assertEqual(prepared['pair']['stream_id'], prepared['audit']['stream_id'])
        self.assertEqual(before, self.originals())
        self.assertFalse(self.service.exists())

    async def test_expected_pair_does_not_freeze_mutable_progress(self):
        first = await self.module.prepare_pair(self.account)
        self.now += timedelta(minutes=1)
        with capture_writer(self.capture) as owner:
            owner.apply(fixture(self.origin, 51, 2, self.now), at=self.now)
        self.assertEqual((await run_stream(self.account, max_polls=1,
                         clock=lambda: self.now))['exit_code'], 0)
        with capture_writer(self.capture) as owner:
            owner.stop(self.now)
        # The fixture may have reached the actual minute; choose a wall floor
        # solely for preflight's chronology observation, not native economics.
        with patch.object(self.module, '_now', return_value=self.now + timedelta(seconds=1)):
            second = await self.module.prepare_pair(self.account, expected=first['pair'])
        self.assertEqual(second['pair'], first['pair'])
        self.assertEqual(second['audit']['events_verified'], 3)

    async def test_actual_account_owner_refuses_before_publication(self):
        with stream_writer(self.account):
            with self.assertRaises(self.module.ServiceUnavailable):
                await self.configure()
        self.assertFalse(self.service.exists())

    async def test_actual_capture_owner_refuses_before_publication(self):
        with capture_writer(self.capture):
            with self.assertRaises(self.module.ServiceUnavailable):
                await self.configure()
        self.assertFalse(self.service.exists())

    async def test_terminal_consumer_reason_is_preserved_and_refused(self):
        with stream_writer(self.account) as owner:
            owner.latch('clock_regression')
        before = self.originals()
        with self.assertRaises(self.module.ServiceRefused) as caught:
            await self.configure()
        self.assertEqual(caught.exception.reason, 'clock_regression')
        self.assertEqual(before, self.originals())
        self.assertFalse(self.service.exists())

    async def test_terminal_capture_reason_in_stopped_phase_is_refused(self):
        with capture_writer(self.capture) as owner:
            owner.fail('rate_blocked', at=self.now)
            owner.stop(self.now)
        before = self.originals()
        with self.assertRaises(self.module.ServiceRefused) as caught:
            await self.configure()
        self.assertEqual(caught.exception.reason, 'rate_blocked')
        self.assertEqual(before, self.originals())

    async def test_uncertain_fetching_capture_is_refused_without_relabel(self):
        with capture_writer(self.capture) as owner:
            owner.attempt(self.now)
        before = self.originals()
        with self.assertRaises(self.module.ServiceRefused) as caught:
            await self.configure()
        self.assertEqual(caught.exception.reason, 'capture_fetching')
        self.assertEqual(before, self.originals())

    async def test_future_saved_time_is_refused(self):
        with capture_writer(self.capture) as owner:
            owner.stop(datetime.now(timezone.utc) + timedelta(hours=1))
        with self.assertRaises(self.module.ServiceRefused) as caught:
            await self.configure()
        self.assertEqual(caught.exception.reason, 'clock_regression')
        self.assertFalse(self.service.exists())

    async def test_fingerprint_mismatch_refuses_before_output(self):
        with patch('trading_bot.paper_stream._engine_fingerprint', return_value='0' * 64):
            with self.assertRaises(self.module.ServiceUnavailable):
                await self.configure()
        self.assertFalse(self.service.exists())

    async def test_full_replay_rejects_digest_consistent_forged_cash(self):
        with sqlite3.connect(self.account / 'stream.sqlite3') as connection:
            head = json.loads(connection.execute('SELECT body FROM stream_head').fetchone()[0])
            for key in ('cash', 'equity', 'peak_equity'):
                head['engine'][key] = str(Decimal(head['engine'][key]) + 1)
            head['engine_sha256'] = hashlib.sha256(encoded(head['engine'])).hexdigest()
            event = json.loads(connection.execute(
                'SELECT body FROM stream_events ORDER BY sequence DESC LIMIT 1').fetchone()[0])
            event['engine_sha256'] = head['engine_sha256']
            event['sha256'] = hashlib.sha256(encoded(
                {key: value for key, value in event.items() if key != 'sha256'})).hexdigest()
            head['event_head_sha256'] = event['sha256']
            connection.execute('UPDATE stream_events SET body=?,sha256=? WHERE sequence=?',
                               (encoded(event).decode(), event['sha256'], event['sequence']))
            raw = encoded(head)
            connection.execute('UPDATE stream_head SET body=?,sha256=?',
                               (raw.decode(), hashlib.sha256(raw).hexdigest()))
        self.assertEqual(read_stream(self.account)['engine']['cash'], head['engine']['cash'])
        before = self.originals()
        with self.assertRaises(self.module.ServiceUnavailable):
            await self.configure()
        self.assertEqual(before, self.originals())

    async def test_configure_publishes_private_fixed_canonical_configuration(self):
        before = self.originals()
        report = await self.configure()
        self.assertEqual((report['status'], report['exit_code']), ('configured', 0))
        self.assertFalse(report['live_enabled'])
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertEqual({p.name for p in self.service.iterdir()},
                         {'.service.lock', 'service.json', 'attempts'})
        raw = (self.service / 'service.json').read_bytes()
        meta = json.loads(raw)
        self.assertEqual(raw, encoded(meta))
        self.assertEqual(meta['runtime']['python'], str(Path(sys.executable).absolute()))
        self.assertEqual(meta['policy'], dict(startup_seconds=180, heartbeat_seconds=30,
                         stop_seconds=15, max_attempts=64, auto_restart=False))
        self.assertEqual(stat.S_IMODE(self.service.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.service / 'attempts').stat().st_mode), 0o700)
        for name in ('.service.lock', 'service.json'):
            info = (self.service / name).stat()
            self.assertEqual((stat.S_IMODE(info.st_mode), info.st_nlink), (0o600, 1))
        self.assertEqual(before, self.originals())

    async def test_policy_bounds_reject_before_output(self):
        for kwargs in ({'startup_seconds': True}, {'startup_seconds': 9},
                       {'startup_seconds': 901}, {'heartbeat_seconds': 4},
                       {'heartbeat_seconds': 121}, {'stop_seconds': 0}, {'stop_seconds': 61}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(self.module.ServiceUnavailable):
                    await self.configure(**kwargs)
                self.assertFalse(self.service.exists())

    async def test_destination_never_clobbers_or_overlaps_inputs(self):
        before = self.originals()
        for destination in (self.capture, self.capture / 'service', self.account,
                            self.account / 'service', self.root):
            with self.subTest(destination=destination):
                with self.assertRaises(self.module.ServiceUnavailable):
                    await self.module.configure_service(self.account, destination)
        self.assertEqual(before, self.originals())

    async def test_destination_parent_is_pinned_before_replay(self):
        parent = self.root / 'output-parent'
        parent.mkdir(mode=0o700)
        self.service = parent / 'service'
        actual = self.module._verify
        async def moved(*args, **kwargs):
            result = await actual(*args, **kwargs)
            parent.rename(self.root / 'original-parent')
            parent.mkdir(mode=0o700)
            return result
        with patch.object(self.module, '_verify', side_effect=moved):
            with self.assertRaises(self.module.ServiceUnavailable):
                await self.configure()
        self.assertFalse(self.service.exists())
        self.assertFalse((self.root / 'original-parent' / 'service').exists())

    async def test_original_change_after_final_sync_never_reports_success(self):
        actual = self.module.PrivateDirectory.sync
        moved = []
        def final_sync(directory):
            result = actual(directory)
            if directory.path == self.service and (self.service / 'service.json').exists() and not moved:
                self.capture.rename(self.root / 'capture-moved')
                moved.append(True)
            return result
        with patch.object(self.module.PrivateDirectory, 'sync', new=final_sync):
            with self.assertRaises(self.module.ServiceStorageError):
                await self.configure()
        self.assertTrue(self.service.exists())
        self.assertTrue((self.root / 'capture-moved' / 'capture.sqlite3').exists())

    async def test_attempts_directory_substitution_at_config_final_sync_is_refused(self):
        actual = self.module.PrivateDirectory.sync
        replaced = []
        def substitute(directory):
            result = actual(directory)
            if directory.path == self.service and (self.service / 'service.json').exists() and not replaced:
                (self.service / 'attempts').rename(self.root / 'original-attempts')
                (self.service / 'attempts').mkdir(mode=0o700)
                replaced.append(True)
            return result
        with patch.object(self.module.PrivateDirectory, 'sync', new=substitute):
            with self.assertRaises(self.module.ServiceStorageError):
                await self.configure()
        self.assertTrue((self.root / 'original-attempts').exists())

    async def test_new_attempt_cannot_reopen_substitution_after_final_sync(self):
        await self.configure()
        audit = (await self.module.prepare_pair(self.account))['audit']
        actual = self.module.PrivateDirectory.sync
        replaced = []
        with self.module.ServiceDirectory(self.service, exclusive=True) as service:
            original = self.service / 'attempts' / '000001'
            moved = self.root / 'original-attempt'
            def substitute(directory):
                result = actual(directory)
                if directory.path == self.service and original.exists() and not replaced:
                    raw = (original / 'started.json').read_bytes()
                    original.rename(moved)
                    original.mkdir(mode=0o700)
                    (original / 'started.json').write_bytes(raw)
                    (original / 'started.json').chmod(0o600)
                    replaced.append(True)
                return result
            with patch.object(self.module.PrivateDirectory, 'sync', new=substitute):
                with self.assertRaises(self.module.ServiceStorageError):
                    service.new_attempt(audit)
            self.assertTrue((moved / 'started.json').exists())

    async def test_exhausted_database_capacity_refuses_before_publication(self):
        database = self.account / 'stream.sqlite3'
        with database.open('r+b') as output:
            output.truncate(256 * 1024 * 1024)
        with self.assertRaises(self.module.ServiceRefused) as caught:
            await self.configure()
        self.assertEqual(caught.exception.reason, 'capacity')
        self.assertFalse(self.service.exists())

    async def test_hot_journal_is_never_repaired_by_preflight(self):
        journal = self.account / 'stream.sqlite3-journal'
        journal.write_bytes(b'uncertain retained journal')
        journal.chmod(0o600)
        before = self.originals()
        with self.assertRaises(self.module.ServiceUnavailable):
            await self.configure()
        self.assertEqual(before, self.originals())
        self.assertEqual(journal.read_bytes(), b'uncertain retained journal')
        self.assertFalse(self.service.exists())

    async def test_config_sync_failure_wins_over_simultaneous_stop(self):
        actual = self.module.PrivateDirectory.sync
        signum = [0]
        def failure(directory):
            if directory.path == self.service:
                signum[0] = 15
                raise OSError('injected directory sync failure')
            return actual(directory)
        with patch.object(self.module.PrivateDirectory, 'sync', new=failure):
            with self.assertRaises(self.module.ServiceStorageError):
                await self.configure(stop=lambda: signum[0])
        self.assertTrue(self.service.exists())

    async def test_signal_after_confirmed_config_preserves_possible_output(self):
        actual = self.module.PrivateDirectory.sync
        signum = [0]
        def interrupted(directory):
            result = actual(directory)
            if directory.path == self.service and (self.service / 'service.json').exists():
                signum[0] = 2
            return result
        with patch.object(self.module.PrivateDirectory, 'sync', new=interrupted):
            with self.assertRaises(self.module.ServiceInterrupted) as caught:
                await self.configure(stop=lambda: signum[0])
        self.assertEqual(caught.exception.signum, 2)
        with self.module.ServiceDirectory(self.service) as observer:
            self.assertFalse(observer.owner_present())

    async def test_both_native_locks_remain_held_during_publication(self):
        actual = self.module.write_file
        observed = []
        def during_write(*args, **kwargs):
            with self.assertRaises(ValueError):
                with stream_writer(self.account):
                    pass
            with self.assertRaises(ValueError):
                with capture_writer(self.capture):
                    pass
            observed.append(True)
            return actual(*args, **kwargs)
        with patch.object(self.module, 'write_file', side_effect=during_write):
            await self.configure()
        self.assertTrue(observed)

    async def test_exact_stop_intent_and_interruption_leave_inputs_unchanged(self):
        before = self.originals()
        for value in (True, 1, 9, '15'):
            with self.subTest(value=value):
                with self.assertRaises(self.module.ServiceUnavailable):
                    await self.configure(stop=lambda: value)
        for value in (2, 15):
            with self.subTest(value=value):
                with self.assertRaises(self.module.ServiceInterrupted) as caught:
                    await self.configure(stop=lambda: value)
                self.assertEqual(caught.exception.signum, value)
        self.assertFalse(self.service.exists())
        self.assertEqual(before, self.originals())

    async def test_passive_observer_works_under_exclusive_service_lease(self):
        await self.configure()
        with self.module.ServiceDirectory(self.service, exclusive=True) as owner:
            with self.module.ServiceDirectory(self.service) as observer:
                observer.check()
                self.assertTrue(observer.owner_present())
                self.assertTrue(owner.owner_present())
                self.assertIsNone(observer.latest_attempt())
            with self.assertRaises(self.module.ServiceUnavailable):
                with self.module.ServiceDirectory(self.service, exclusive=True):
                    pass
        with self.module.ServiceDirectory(self.service) as observer:
            self.assertFalse(observer.owner_present())

    async def test_extra_root_members_and_noncanonical_config_are_refused(self):
        await self.configure()
        unknown = self.service / 'unknown'
        unknown.write_bytes(b'')
        unknown.chmod(0o600)
        with self.assertRaises(self.module.ServiceUnavailable):
            self.module.ServiceDirectory(self.service)
        unknown.unlink()
        config = self.service / 'service.json'
        original = config.read_bytes()
        config.write_bytes(original + b'\n')
        with self.assertRaises(self.module.ServiceUnavailable):
            self.module.ServiceDirectory(self.service)

    async def test_config_mutation_is_detected_by_held_observer(self):
        await self.configure()
        with self.module.ServiceDirectory(self.service) as service:
            meta = service.manifest
            meta['policy']['stop_seconds'] = 10
            self.assertEqual(service.manifest['policy']['stop_seconds'], 15)
            (self.service / 'service.json').write_bytes(encoded(meta))
            with self.assertRaises(self.module.ServiceUnavailable):
                service.check()

    async def test_new_attempt_is_private_consecutive_and_immutable(self):
        service, attempt = await self.new_attempt()
        self.assertEqual(attempt.number, 1)
        self.assertEqual(attempt.path.name, '000001')
        self.assertEqual(attempt.record['audit']['events_verified'], 2)
        self.assertEqual(attempt.record['config_sha256'], service.config_sha256)
        self.assertEqual(stat.S_IMODE(attempt.path.stat().st_mode), 0o700)
        self.assertEqual((attempt.path / 'started.json').read_bytes(), encoded(attempt.record))
        attempt.save_result(self.final_result(service, attempt), {'capture': b'c', 'stream': b's'})
        second = service.new_attempt(attempt.record['audit'])
        self.assertEqual(second.number, 2)
        self.assertNotEqual(second.attempt_id, attempt.attempt_id)
        self.assertEqual(service.latest_attempt().attempt_id, second.attempt_id)

    async def test_read_only_handle_cannot_create_attempt(self):
        await self.configure()
        audit = (await self.module.prepare_pair(self.account))['audit']
        with self.module.ServiceDirectory(self.service) as observer:
            with self.assertRaises(self.module.ServiceUnavailable):
                observer.new_attempt(audit)
        self.assertEqual(list((self.service / 'attempts').iterdir()), [])

    async def test_incomplete_or_unconfirmed_cleanup_blocks_next_attempt(self):
        service, attempt = await self.new_attempt()
        with self.assertRaises(self.module.ServiceRefused) as caught:
            service.new_attempt(attempt.record['audit'])
        self.assertEqual(caught.exception.reason, 'previous_attempt_unconfirmed')
        self.assertEqual(service.latest_attempt().attempt_id, attempt.attempt_id)
        attempt.save_result(self.final_result(service, attempt, cleanup=False),
                            {'capture': b'', 'stream': b''})
        with self.assertRaises(self.module.ServiceRefused) as caught:
            service.new_attempt(attempt.record['audit'])
        self.assertEqual(caught.exception.reason, 'previous_attempt_unconfirmed')

    async def test_final_evidence_is_bounded_no_clobber_and_exact(self):
        service, attempt = await self.new_attempt()
        result = self.final_result(service, attempt)
        attempt.save_result(result, {'capture': b'capture stderr\n', 'stream': b'stream stderr\n'})
        before = {p.name: p.read_bytes() for p in attempt.path.iterdir()}
        self.assertEqual(before['result.json'], encoded(result))
        self.assertEqual(before['capture.stderr'], b'capture stderr\n')
        with self.assertRaises(self.module.ServiceUnavailable):
            attempt.save_result(result, {'capture': b'replaced', 'stream': b''})
        self.assertEqual(before, {p.name: p.read_bytes() for p in attempt.path.iterdir()})

    async def test_oversized_final_evidence_refuses_without_partial_files(self):
        service, attempt = await self.new_attempt()
        with self.assertRaises(self.module.ServiceUnavailable):
            attempt.save_result(self.final_result(service, attempt),
                                {'capture': b'x' * (256 * 1024 + 1), 'stream': b''})
        self.assertEqual({p.name for p in attempt.path.iterdir()}, {'started.json'})

    async def test_partial_final_write_is_preserved_and_blocks_next_attempt(self):
        service, attempt = await self.new_attempt()
        actual = self.module.write_file
        def fail_second(directory, name, *args, **kwargs):
            if name == 'stream.stderr':
                raise OSError('injected stderr publication failure')
            return actual(directory, name, *args, **kwargs)
        with patch.object(self.module, 'write_file', side_effect=fail_second):
            with self.assertRaises(self.module.ServiceStorageError):
                attempt.save_result(self.final_result(service, attempt),
                                    {'capture': b'preserve me', 'stream': b'not written'})
        self.assertEqual((attempt.path / 'capture.stderr').read_bytes(), b'preserve me')
        self.assertFalse((attempt.path / 'result.json').exists())
        self.assertEqual(service.latest_attempt().attempt_id, attempt.attempt_id)
        with self.assertRaises(self.module.ServiceRefused):
            service.new_attempt(attempt.record['audit'])

    async def test_attempt_capacity_preserves_all_prior_records(self):
        service, attempt = await self.new_attempt()
        audit = attempt.record['audit']
        for number in range(1, 65):
            self.assertEqual(attempt.number, number)
            attempt.save_result(self.final_result(service, attempt), {'capture': b'', 'stream': b''})
            if number < 64:
                attempt = service.new_attempt(audit)
        with self.assertRaises(self.module.ServiceRefused) as caught:
            service.new_attempt(audit)
        self.assertEqual(caught.exception.reason, 'attempt_capacity')
        self.assertEqual({p.name for p in (self.service / 'attempts').iterdir()},
                         {f'{number:06d}' for number in range(1, 65)})

    async def test_attempt_inventory_rejects_gap_or_unknown_member(self):
        service, attempt = await self.new_attempt()
        unknown = self.service / 'attempts' / '000003'
        unknown.mkdir(mode=0o700)
        with self.assertRaises(self.module.ServiceUnavailable):
            service.check()

    async def test_ci_required_socket_capability_cannot_be_skipped(self):
        with patch.dict(os.environ, {'PAPER_SERVICE_REQUIRE_SOCKET': '1'}):
            with patch('socket.socket', side_effect=PermissionError(errno.EPERM, 'denied')):
                try:
                    with self.assertRaises(PermissionError):
                        self.require_unix_socket()
                except unittest.SkipTest:
                    self.fail('required Linux socket capability must fail, not skip')

    async def test_socket_uses_held_fd_private_mode_and_never_reuses_name(self):
        self.require_unix_socket()
        service, attempt = await self.new_attempt()
        listener = attempt.bind_health_socket()
        self.assertFalse(listener.getblocking())
        address = attempt.health_address()
        self.assertTrue(address.startswith('/proc/self/fd/'))
        self.assertTrue(address.endswith('/health.sock'))
        info = (attempt.path / 'health.sock').lstat()
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_nlink, info.st_uid),
                         (0o600, 1, os.geteuid()))
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(address)
            connection, _ = listener.accept()
            connection.close()
        with self.assertRaises(self.module.ServiceUnavailable):
            attempt.bind_health_socket()

    async def test_socket_substitution_or_attempt_relocation_is_refused(self):
        self.require_unix_socket()
        service, attempt = await self.new_attempt()
        attempt.bind_health_socket()
        socket_path = attempt.path / 'health.sock'
        socket_path.unlink()
        socket_path.write_bytes(b'not a socket')
        socket_path.chmod(0o600)
        with self.assertRaises(self.module.ServiceUnavailable):
            attempt.health_address()

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX fork')
    async def test_forked_close_does_not_unlock_parent_service_lease(self):
        await self.configure()
        with self.module.ServiceDirectory(self.service, exclusive=True) as service:
            child = os.fork()
            if child == 0:
                try:
                    try:
                        service.check()
                    except self.module.ServiceUnavailable:
                        pass
                    else:
                        os._exit(3)
                    service.close()
                    with self.module.ServiceDirectory(self.service) as observer:
                        os._exit(0 if observer.owner_present() else 4)
                except BaseException:
                    os._exit(5)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertTrue(service.owner_present())


if __name__ == '__main__':
    unittest.main()
