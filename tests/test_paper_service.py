"""Real subprocess lifecycle, output pressure and fresh local service probes."""
import asyncio
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from paper_service_fixtures import create_pair, has_unix_sockets, network_forbidden

FIXTURE = Path(__file__).with_name('paper_service_fixtures.py')


class PaperServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_bot.paper_service'),
                             'guarded PAPER pair supervisor is not implemented')
        self.module = importlib.import_module('trading_bot.paper_service')
        self.state = importlib.import_module('trading_bot.paper_service_state')
        self.actual_sockets = has_unix_sockets()
        self.temp = tempfile.TemporaryDirectory(prefix='SECRET-service-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = network_forbidden()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.account, self.capture = asyncio.run(create_pair(self.root))
        self.service = self.root / 'service'
        self.events = self.root / 'events.jsonl'
        result = asyncio.run(self.module.configure_service(self.account, self.service,
            startup_seconds=10, heartbeat_seconds=5, stop_seconds=1))
        self.assertEqual(result['exit_code'], 0)

    def cleanup_process(self, process):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def start(self, mode='normal'):
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(FIXTURE),
            'service' if self.actual_sockets else 'service_without_socket',
            str(self.service), mode, str(self.events)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True)
        self.addCleanup(self.cleanup_process, process)
        return process

    def wait_running(self, process):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail('service exited before ownership: ' + repr((process.returncode, stdout, stderr)))
            try:
                report = self.observe(process)
            except (ValueError, OSError):
                pass
            else:
                if report['status'] == 'running':
                    self.assertEqual(report['exit_code'], 0)
                    return report
            time.sleep(.03)
        self.fail('service did not reach observed native ownership')

    def observe(self, process):
        if self.actual_sockets:
            return self.module.probe_service(self.service)
        # This branch proves coordinator observations only, not live probe IO.
        if process.poll() is not None:
            raise ValueError('test supervisor already ended')
        observations = [e['value'] for e in self.event_list()
                        if e['event'] == 'test_monitor_observation']
        if not observations:
            raise ValueError('test coordinator has no observation yet')
        return observations[-1]

    def finish(self, process, expected):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, expected, repr((stdout, stderr)))
        self.assertEqual(len(stdout.splitlines()), 1, repr((stdout, stderr)))
        self.assertNotIn(b'SECRET', stdout)
        self.assertNotIn(str(self.root).encode(), stdout)
        result = json.loads(stdout)
        self.assertEqual(result['exit_code'], expected)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertTrue(all(child['reaped'] for child in result['children'].values()))
        return result

    def event_list(self):
        if not self.events.exists():
            return []
        # Ignore an in-flight last append; never mistake it for completed evidence.
        lines = self.events.read_text().splitlines(keepends=True)
        return [json.loads(line) for line in lines if line.endswith('\n')]

    def test_unstarted_service_is_not_live_and_probe_does_not_create_attempts(self):
        before = {str(p.relative_to(self.service)): p.read_bytes()
                  for p in self.service.rglob('*') if p.is_file()}
        report = self.module.probe_service(self.service)
        self.assertEqual(report['exit_code'], 1)
        self.assertEqual(report['status'], 'not_running')
        self.assertFalse(report['supervisor_response'])
        self.assertEqual(before, {str(p.relative_to(self.service)): p.read_bytes()
            for p in self.service.rglob('*') if p.is_file()})

    def test_requested_sigterm_stops_and_reaps_consumer_before_collector(self):
        process = self.start()
        live = self.wait_running(process)
        self.assertFalse(live['boundaries']['live_execution'])
        self.assertEqual(live['boundaries']['data_readiness'], 'not_assessed')
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, 143)
        self.assertEqual([e['role'] for e in self.event_list() if e['event'] == 'stopped'],
                         ['stream', 'capture'])
        self.assertEqual({c['returncode'] for c in result['children'].values()}, {143})
        stopped = self.module.probe_service(self.service)
        self.assertEqual(stopped['exit_code'], 1)
        self.assertFalse(stopped['supervisor_response'])

    def test_repeated_signals_preserve_first_intent_and_finish_cleanup(self):
        process = self.start()
        self.wait_running(process)
        process.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(e['event'] == 'stopped' for e in self.event_list()):
            time.sleep(.01)
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, 130)
        self.assertEqual({c['returncode'] for c in result['children'].values()}, {130})

    def test_children_use_separate_sessions_and_do_not_inherit_service_lease(self):
        process = self.start()
        live = self.wait_running(process)
        for child in live['children'].values():
            self.assertEqual(os.getpgid(child['pid']), child['pid'])
            self.assertNotEqual(os.getpgid(child['pid']), os.getpgid(process.pid))
            links = []
            for path in Path('/proc', str(child['pid']), 'fd').iterdir():
                try:
                    links.append(os.readlink(path))
                except FileNotFoundError:
                    pass
            self.assertFalse(any('.service.lock' in value for value in links), links)
        process.send_signal(signal.SIGTERM)
        self.finish(process, 143)

    def test_duplicate_supervisor_does_not_signal_existing_children(self):
        process = self.start()
        before = self.wait_running(process)
        with self.assertRaises(self.state.ServiceUnavailable):
            self.module.run_service(self.service)
        after = self.observe(process)
        self.assertEqual({r: c['pid'] for r, c in before['children'].items()},
                         {r: c['pid'] for r, c in after['children'].items()})
        self.assertEqual(after['status'], 'running')
        process.send_signal(signal.SIGTERM)
        self.finish(process, 143)

    def test_unsolicited_success_ends_the_attempt_without_respawn(self):
        process = self.start('exit_zero')
        result = self.finish(process, 4)
        self.assertEqual(result['children']['capture']['returncode'], 0)
        self.assertEqual(len([e for e in self.event_list() if e['event'] == 'spawn'
                              and e['role'] == 'capture']), 1)

    def test_progress_without_owned_frame_cannot_extend_startup_budget(self):
        began = time.monotonic()
        result = self.finish(self.start('unowned'), 4)
        self.assertGreaterEqual(time.monotonic() - began, 10)
        self.assertEqual(result['reason'], 'worker_startup_timeout')
        self.assertTrue(result['children']['capture']['guarded'])
        self.assertFalse(result['children']['capture']['owned'])
        self.assertEqual(set(result['children']), {'capture'})

    def test_native_refusal_stops_sibling_and_preserves_policy_outcome(self):
        process = self.start('exit_refusal')
        result = self.finish(process, 3)
        self.assertEqual(result['children']['stream']['returncode'], 3)
        self.assertTrue(result['children']['stream']['native_result']['terminal'])
        self.assertEqual(len([e for e in self.event_list() if e['event'] == 'spawn']), 2)

    def test_unrequested_worker_signal_is_not_a_successful_operator_stop(self):
        process = self.start('self_term')
        result = self.finish(process, 4)
        self.assertEqual(result['children']['stream']['returncode'], 143)

    def test_terminal_reason_takes_priority_over_requested_shutdown(self):
        process = self.start('terminal_on_stop')
        self.wait_running(process)
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, 3)
        self.assertEqual(result['children']['capture']['returncode'], 143)
        self.assertEqual(result['children']['capture']['native_result']['reason'], 'clock_regression')

    def test_storage_failure_takes_priority_over_requested_shutdown(self):
        process = self.start('storage_on_stop')
        self.wait_running(process)
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, 4)
        self.assertEqual(result['children']['capture']['returncode'], 4)

    def test_uncooperative_worker_is_killed_and_shutdown_is_uncertain(self):
        process = self.start('ignore_capture')
        self.wait_running(process)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        result = self.finish(process, 4)
        self.assertLess(time.monotonic() - started, 8)
        self.assertTrue(result['children']['capture']['forced'])
        self.assertEqual(result['children']['capture']['returncode'], -signal.SIGKILL)

    def test_stderr_flood_is_bounded_and_both_children_are_reaped(self):
        process = self.start('stderr_flood')
        result = self.finish(process, 4)
        self.assertTrue(result['children']['capture']['stderr_truncated'])
        for path in (self.service / 'attempts').glob('*/capture.stderr'):
            self.assertLessEqual(path.stat().st_size, 262144)

    def test_unterminated_stdout_flood_cannot_fill_unbounded_memory(self):
        process = self.start('stdout_flood')
        result = self.finish(process, 4)
        self.assertFalse(result['children']['capture']['forced'])

    def test_malformed_worker_frame_ends_pair_without_adopting_saved_state(self):
        process = self.start('malformed')
        self.finish(process, 4)
        self.assertLessEqual(len([e for e in self.event_list() if e['event'] == 'spawn']), 2)

    def test_sigstopped_worker_loses_progress_health_and_is_contained(self):
        process = self.start()
        live = self.wait_running(process)
        os.kill(live['children']['stream']['pid'], signal.SIGSTOP)
        result = self.finish(process, 4)
        self.assertTrue(result['children']['stream']['forced'])
        self.assertNotEqual(self.module.probe_service(self.service)['exit_code'], 0)

    def test_malformed_and_idle_health_clients_do_not_block_control_loop(self):
        if not self.actual_sockets:
            self.skipTest('executor prohibits AF_UNIX; actual sockets required by Linux CI')
        process = self.start()
        self.wait_running(process)
        with self.state.ServiceDirectory(self.service) as owner:
            attempt = owner.latest_attempt()
            try:
                sockets = []
                for _ in range(4):
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(2)
                    client.connect(attempt.health_address())
                    sockets.append(client)
                sockets[0].sendall(b'x' * 257)
                process.send_signal(signal.SIGTERM)
                self.finish(process, 143)
            finally:
                for client in sockets:
                    client.close()
                attempt.close()

    def test_unavailable_socket_capability_never_launches_worker_or_creates_attempt(self):
        from unittest.mock import patch
        with patch.object(self.module.socket, 'socket', side_effect=PermissionError('forbidden')), \
                patch.object(self.module, '_spawn_worker') as spawn:
            with self.assertRaises(self.state.ServiceUnavailable):
                self.module.run_service(self.service)
            spawn.assert_not_called()
        self.assertEqual(list((self.service / 'attempts').iterdir()), [])

    def test_config_inode_replacement_stops_pair_without_writing_replacement(self):
        process = self.start()
        self.wait_running(process)
        config = self.service / 'service.json'
        old = self.service / 'old-config'
        original = config.read_bytes()
        config.rename(old)
        config.write_bytes(original)
        config.chmod(0o600)
        try:
            result = self.finish(process, 4)
            self.assertTrue(result['cleanup_confirmed'])
            self.assertEqual(config.read_bytes(), original)
        finally:
            config.unlink()
            old.rename(config)

    def test_replaced_attempt_directory_is_not_used_for_result_publication(self):
        process = self.start()
        live = self.wait_running(process)
        attempt = self.service / 'attempts' / '000001'
        moved = self.root / 'moved-attempt'
        attempt.rename(moved)
        attempt.mkdir(mode=0o700)
        sentinel = attempt / 'keep.txt'
        sentinel.write_bytes(b'preserve')
        self.finish(process, 4)
        self.assertEqual([p.name for p in attempt.iterdir()], ['keep.txt'])
        self.assertEqual(sentinel.read_bytes(), b'preserve')
        self.assertEqual(live['attempt_id'], json.loads((moved / 'started.json').read_text())['attempt_id'])

    def test_explicit_second_run_keeps_distinct_attempt_and_original_pair(self):
        before = [(self.account / 'stream.sqlite3').read_bytes(),
                  (self.capture / 'capture.sqlite3').read_bytes()]
        identities = []
        for _ in range(2):
            process = self.start()
            identities.append(self.wait_running(process)['attempt_id'])
            process.send_signal(signal.SIGTERM)
            self.finish(process, 143)
        self.assertEqual(len(set(identities)), 2)
        self.assertEqual(sorted(p.name for p in (self.service / 'attempts').iterdir()), ['000001', '000002'])
        self.assertEqual(before, [(self.account / 'stream.sqlite3').read_bytes(),
                                  (self.capture / 'capture.sqlite3').read_bytes()])


if __name__ == '__main__':
    unittest.main()
