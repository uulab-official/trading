"""Supervised native PAPER economics, explicit restart and pair containment."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server', 'tests'):
    sys.path.insert(0, str(ROOT / component))

import paper_service_fixtures as fixtures
import test_paper_service as coordinator_tests


def _events(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines(keepends=True)
            if line.endswith('\n')]


def _pair_subreaper(service_directory, events, expected_parent):
    """Isolated harness; only this subprocess adopts and reaps the native pair."""
    import ctypes
    from trading_bot.paper_service_worker import arm_parent_death
    from trading_bot.paper_service_state import ServiceDirectory
    from trading_bot.paper_stream_store import read_stream
    from trading_bot import paper_service

    arm_parent_death(expected_parent)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise AssertionError('Linux subreaper unavailable')
    real_socket = fixtures.has_unix_sockets()
    root = fixtures.ROOT
    body = r'''
import os,sys
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server','tests'):
 sys.path.insert(0,str(root/part))
from trading_bot.paper_service_worker import arm_parent_death
arm_parent_death(int(sys.argv[4]))
from paper_service_fixtures import network_forbidden,service_process
with network_forbidden():
 raise SystemExit(service_process(sys.argv[2],'native1',sys.argv[3],headless=sys.argv[5]=='1'))
'''
    process = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', body,
        str(root), str(service_directory), str(events), str(os.getpid()), '0' if real_socket else '1'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True, start_new_session=True)
    children = {}
    reaped = set()
    try:
        with ServiceDirectory(service_directory) as service:
            pair = service.manifest['pair']
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                out, err = process.communicate()
                raise AssertionError('pair supervisor ended before kill: ' + repr((out, err)))
            try:
                if real_socket:
                    report = paper_service.probe_service(service_directory)
                else:
                    observations = [entry['value'] for entry in _events(events)
                                    if entry['event'] == 'test_monitor_observation']
                    report = observations[-1] if observations else None
                current = read_stream(pair['account_path'])
            except (OSError, ValueError):
                pass
            else:
                if (report is not None and report['status'] == 'running'
                        and current['engine']['next_index'] == 4):
                    children = {role: child['pid'] for role, child in report['children'].items()}
                    if set(children) != {'capture', 'stream'}:
                        raise AssertionError('pair ownership incomplete')
                    break
            time.sleep(.03)
        else:
            raise AssertionError('native pair did not reach owned progress')
        process.kill()
        process.wait(timeout=5)
        outcomes = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(reaped) < 2:
            for role, pid in children.items():
                if pid in reaped:
                    continue
                found, status = os.waitpid(pid, os.WNOHANG)
                if found:
                    reaped.add(pid)
                    outcomes[role] = os.waitstatus_to_exitcode(status)
            if len(reaped) < 2:
                time.sleep(.01)
        if len(reaped) != 2:
            raise AssertionError('native orphan owner remained alive')
        for directory, lock in ((pair['account_path'], '.stream.lock'),
                                (pair['capture_path'], '.capture.lock')):
            fd = os.open(Path(directory) / lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        databases = [Path(pair['account_path']) / 'stream.sqlite3',
                     Path(pair['capture_path']) / 'capture.sqlite3']
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in databases]
        time.sleep(.15)
        stable = before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in databases]
        with ServiceDirectory(service_directory) as service:
            attempt = service.latest_attempt()
            owner_gone = not service.owner_present()
            missing_result = not (attempt.path / 'result.json').exists()
        print(json.dumps(dict(supervisor=process.returncode, children=outcomes,
            locks_released=True, stable=stable, owner_gone=owner_gone,
            result_unconfirmed=missing_result, actual_socket=real_socket)), flush=True)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        # All discovered PIDs were spawned by this harness's known supervisor.
        # Reap before considering SIGKILL, so already reaped/reused IDs are not signalled.
        discovered = set(children.values()) | {
            entry['pid'] for entry in _events(events) if entry['event'] == 'spawn'}
        for pid in discovered - reaped:
            try:
                found, _ = os.waitpid(pid, os.WNOHANG)
                if not found:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
            except (ChildProcessError, ProcessLookupError):
                pass


class PaperServiceIntegrationTests(unittest.TestCase):
    # Reuse only lifecycle helpers; importing the module or assigning methods
    # does not rediscover or inherit its coordinator TestCase's test methods.
    setUp = coordinator_tests.PaperServiceTests.setUp
    cleanup_process = coordinator_tests.PaperServiceTests.cleanup_process
    start = coordinator_tests.PaperServiceTests.start
    wait_running = coordinator_tests.PaperServiceTests.wait_running
    observe = coordinator_tests.PaperServiceTests.observe
    finish = coordinator_tests.PaperServiceTests.finish
    event_list = coordinator_tests.PaperServiceTests.event_list

    def saved(self):
        from trading_bot.paper_stream_store import read_stream
        return read_stream(self.account)

    def wait_consumed(self, process, count):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), 'native service terminated before progress')
            try:
                state = self.saved()
            except (OSError, ValueError):
                pass
            else:
                if state['engine']['next_index'] == count:
                    return state
                self.assertLess(state['engine']['next_index'], count)
            time.sleep(.03)
        self.fail('native consumer did not reach the expected complete prefix')

    def verify_stopped_prefix(self, count, original):
        from trading_bot.market_capture_store import read_capture
        from trading_bot.paper_stream import audit_stream
        from trading_bot.paper_stream_store import stream_reader
        before = [(self.account / 'stream.sqlite3').read_bytes(),
                  (self.capture / 'capture.sqlite3').read_bytes()]
        audit = asyncio.run(audit_stream(self.account))
        self.assertEqual((audit['status'], audit['events_verified']), ('verified_prefix', count))
        saved = self.saved()
        captured = read_capture(self.capture)
        self.assertEqual(saved['manifest'], original['manifest'])
        self.assertEqual(saved['manifest']['stream_id'], original['manifest']['stream_id'])
        self.assertEqual(captured['state']['capture_id'], original['manifest']['capture']['capture_id'])
        self.assertEqual(audit['engine_sha256'], saved['engine_sha256'])
        self.assertEqual(audit['event_head_sha256'], saved['event_head_sha256'])
        self.assertEqual(saved['engine']['next_index'], count)
        self.assertEqual(saved['control']['phase'], 'stopped')
        self.assertEqual(captured['state']['phase'], 'stopped')
        self.assertIsNone(saved['control']['last_error'])
        self.assertIsNone(captured['state']['last_error'])
        with stream_reader(self.account, audit=True) as reader:
            events = list(reader.events())
        self.assertEqual(before, [(self.account / 'stream.sqlite3').read_bytes(),
                                  (self.capture / 'capture.sqlite3').read_bytes()])
        return saved, events

    def test_two_explicit_native_runs_preserve_one_account_and_exact_economics(self):
        original = self.saved()
        self.assertEqual(original['engine']['next_index'], 2)
        self.assertEqual(original['engine']['fill_count'], 1)
        first_process = self.start('native1')
        first_live = self.wait_running(first_process)
        self.wait_consumed(first_process, 4)
        first_process.send_signal(signal.SIGTERM)
        first_result = self.finish(first_process, 143)
        middle, first_events = self.verify_stopped_prefix(4, original)
        self.assertEqual(middle['engine']['fill_count'], 1)
        self.assertEqual(Decimal(middle['engine']['holding']), Decimal('8.33333333'))
        self.assertEqual(middle['engine']['pending_signal'], {'action': 'sell', 'source_index': 3})
        second_process = self.start('native2')
        second_live = self.wait_running(second_process)
        self.wait_consumed(second_process, 6)
        second_process.send_signal(signal.SIGTERM)
        second_result = self.finish(second_process, 143)
        final, all_events = self.verify_stopped_prefix(6, original)
        self.assertEqual(all_events[:4], first_events)
        self.assertEqual(final['engine']['fill_count'], 2)
        self.assertEqual(Decimal(final['engine']['cash']), Decimal('916.66666670'))
        self.assertEqual(Decimal(final['engine']['equity']), Decimal('916.66666670'))
        self.assertEqual(Decimal(final['engine']['holding']), 0)
        self.assertEqual(Decimal(final['engine']['total_fees']), 0)
        self.assertFalse(final['engine']['daily_loss_halted'])
        self.assertFalse(final['engine']['drawdown_halted'])
        self.assertIsNone(final['engine']['pending_signal'])
        self.assertNotEqual(first_live['attempt_id'], second_live['attempt_id'])
        self.assertEqual(first_result['service_id'], second_result['service_id'])
        starts = [json.loads((self.service / 'attempts' / name / 'started.json').read_bytes())
                  for name in ('000001', '000002')]
        self.assertEqual([item['audit']['events_verified'] for item in starts], [2, 4])
        self.assertEqual([entry['role'] for entry in self.event_list() if entry['event'] == 'stopped'],
                         ['stream', 'capture', 'stream', 'capture'])
        self.assertEqual(len([entry for entry in self.event_list() if entry['event'] == 'spawn']), 4)
        for result in (first_result, second_result):
            self.assertEqual(set(result['children']), {'capture', 'stream'})
            self.assertEqual({child['returncode'] for child in result['children'].values()}, {143})

    def test_owned_native_pair_refuses_another_configuration_and_service_launch(self):
        from trading_bot.market_capture_store import capture_writer
        from trading_bot.paper_stream_store import stream_writer
        alternate = self.root / 'alternate-service'
        asyncio.run(self.module.configure_service(self.account, alternate,
            startup_seconds=10, heartbeat_seconds=5, stop_seconds=1))
        process = self.start('native1')
        self.wait_running(process)
        self.wait_consumed(process, 4)
        with self.assertRaises(ValueError):
            with stream_writer(self.account):
                pass
        with self.assertRaises(ValueError):
            with capture_writer(self.capture):
                pass
        refused = self.root / 'refused-service'
        with self.assertRaises(self.state.ServiceUnavailable):
            asyncio.run(self.module.configure_service(self.account, refused))
        self.assertFalse(refused.exists())
        original = self.service
        try:
            self.service = alternate
            second = self.start('native1')
            stdout, stderr = second.communicate(timeout=15)
            self.assertEqual(second.returncode, 2, repr((stdout, stderr)))
            self.assertEqual(json.loads(stdout), {'error': 'fixture_service_unavailable'})
        finally:
            self.service = original
        self.assertEqual(len([entry for entry in self.event_list() if entry['event'] == 'spawn']), 2)
        self.assertEqual(list((alternate / 'attempts').iterdir()), [])
        self.assertIsNone(process.poll())
        process.send_signal(signal.SIGTERM)
        self.finish(process, 143)

    def test_saved_terminal_capture_refuses_before_any_native_spawn(self):
        from trading_bot.market_capture_store import capture_writer
        now = datetime.now(timezone.utc)
        with capture_writer(self.capture) as owner:
            owner.fail('rate_blocked', at=now)
            owner.stop(now)
        before = [(self.account / 'stream.sqlite3').read_bytes(),
                  (self.capture / 'capture.sqlite3').read_bytes()]
        process = self.start('native1')
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 3, repr((stdout, stderr)))
        self.assertEqual(json.loads(stdout), {'error': 'fixture_service_refused'})
        self.assertEqual(self.event_list(), [])
        self.assertEqual(list((self.service / 'attempts').iterdir()), [])
        self.assertEqual(before, [(self.account / 'stream.sqlite3').read_bytes(),
                                  (self.capture / 'capture.sqlite3').read_bytes()])

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux child subreaper')
    def test_parent_only_sigkill_reaps_both_native_owners_and_stops_activity(self):
        process = subprocess.run([sys.executable, '-I', '-B', '-u', str(Path(__file__)),
            'subreaper', str(self.service), str(self.events), str(os.getpid())],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=25)
        self.assertEqual(process.returncode, 0, repr((process.stdout, process.stderr)))
        self.assertEqual(len(process.stdout.splitlines()), 1)
        result = json.loads(process.stdout)
        self.assertEqual(result['supervisor'], -signal.SIGKILL)
        self.assertEqual(result['children'], {'capture': -signal.SIGKILL, 'stream': -signal.SIGKILL})
        self.assertTrue(result['locks_released'])
        self.assertTrue(result['stable'])
        self.assertTrue(result['owner_gone'])
        self.assertTrue(result['result_unconfirmed'])
        self.assertEqual(result['actual_socket'], self.actual_sockets)
        self.assertEqual(self.saved()['engine']['next_index'], 4)
        prepared = asyncio.run(self.state.prepare_pair(self.account))
        with self.state.ServiceDirectory(self.service, exclusive=True) as service:
            with self.assertRaises(self.state.ServiceRefused) as caught:
                service.new_attempt(prepared['audit'])
        self.assertEqual(caught.exception.reason, 'previous_attempt_unconfirmed')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'subreaper':
        with fixtures.network_forbidden():
            _pair_subreaper(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    else:
        unittest.main()
