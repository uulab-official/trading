"""Pure health validation and pipe-backed monitor tests; no socket substitution.

These exercise the monitor below its platform/AF_UNIX transport boundary. An idle
owned pipe is only a selector input; no health connection is opened or simulated.
"""
from copy import deepcopy
import importlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))

SERVICE = '1' * 32
ATTEMPT = '2' * 32
CHALLENGE = '3' * 32


def _child_reply(pid):
    return dict(pid=pid, guarded=True, owned=True, progress_age_ms=20,
        returncode=None, native_result=None, forced=False, reaped=False,
        stderr_bytes=0, stderr_truncated=False, stdout_bytes=400)


def _health_reply():
    # Literal protocol fixture, independently specified from _report/snapshot.
    return dict(schema_version=1, source='paper_service', operation='check',
        mode='paper', live_enabled=False, read_only=True, service_id=SERVICE,
        attempt_id=ATTEMPT, attempt_number=1, status='running', exit_code=0,
        children=dict(capture=_child_reply(12001), stream=_child_reply(12002)),
        supervisor_response=True, challenge=CHALLENGE,
        boundaries=dict(data_readiness='not_assessed', live_execution=False))


class _Owner:
    def __init__(self):
        self.manifest = dict(service_id=SERVICE, policy=dict(startup_seconds=10,
            heartbeat_seconds=5, stop_seconds=1),
            pair=dict(capture_path='/unused-capture', account_path='/unused-account'))
        self.check_fault = None

    def check(self):
        if self.check_fault is not None:
            self.check_fault()


class _Attempt:
    attempt_id = ATTEMPT
    number = 1

    def __init__(self):
        self.saved = []

    def check(self):
        pass

    def save_result(self, result, stderr):
        self.saved.append((deepcopy(result), dict(stderr)))


class _OwnedHandle:
    """A process-handle double that owns real pipes; it never signals a PID."""
    def __init__(self, role, pid, *, signal_fault=None):
        self.role, self.pid = role, pid
        self.returncode = None
        self.signal_fault = signal_fault
        self.signals = []
        self.killed = False
        self._writes = {}
        for name in ('stdout', 'stderr'):
            read, write = os.pipe()
            setattr(self, name, os.fdopen(read, 'rb', buffering=0))
            self._writes[name] = write

    def poll(self):
        return self.returncode

    def send_signal(self, number):
        self.signals.append(number)
        if self.signal_fault is not None:
            fault, self.signal_fault = self.signal_fault, None
            raise fault
        self.returncode = 128 + number
        self._close_writes()

    def kill(self):
        self.killed = True
        self.returncode = -signal.SIGKILL
        self._close_writes()

    def _close_writes(self):
        for descriptor in self._writes.values():
            os.close(descriptor)
        self._writes.clear()

    def close(self):
        self._close_writes()
        self.stdout.close()
        self.stderr.close()


_WORKER = r'''
import os,socket,sys,time
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server'):
 sys.path.insert(0,str(root/part))
def forbidden(*args,**kwargs):raise AssertionError('network forbidden')
socket.getaddrinfo=socket.create_connection=socket.socket.connect=forbidden
socket.socket.connect_ex=socket.socket.sendto=forbidden
from trading_bot import paper_service_worker as worker
def native(role,directory,stop,sleep):
 sleep(.01)
 while True:
  number=stop()
  if number:
   key='capture' if role=='capture' else 'control'
   return {'exit_code':128+number,'observation':{'status':'stopped',key:{'phase':'stopped','last_error':None}}}
  sleep(.01)
worker._run_native=native
raise SystemExit(worker.worker_main(sys.argv[2],'unused',sys.argv[3],int(sys.argv[4])))
'''


class HealthResponseTests(unittest.TestCase):
    def setUp(self):
        self.service = importlib.import_module('trading_bot.paper_service')
        self.protocol = importlib.import_module('trading_bot.paper_service_protocol')
        self.owner, self.attempt = _Owner(), _Attempt()

    def decode(self, value):
        raw = self.protocol.encode_document(value, 8191)
        return self.service._health_response(raw, self.owner, self.attempt, CHALLENGE)

    def reject(self, value):
        with self.assertRaises(ValueError):
            self.decode(value)

    def test_fresh_exact_reply_is_accepted_and_challenge_does_not_escape(self):
        value = _health_reply()
        observed = self.decode(value)
        self.assertEqual(observed['status'], 'running')
        self.assertEqual(observed['exit_code'], 0)
        self.assertNotIn('challenge', observed)
        self.assertEqual(value['challenge'], CHALLENGE)

    def test_wrong_challenge_identity_and_top_level_types_are_refused(self):
        mutations = [('challenge', '4'*32), ('challenge', 3), ('service_id', '9'*32),
            ('attempt_id', '8'*32), ('attempt_number', True), ('schema_version', True),
            ('live_enabled', 0), ('read_only', 1), ('supervisor_response', 1),
            ('exit_code', False), ('unknown', 'SECRET')]
        for key, replacement in mutations:
            with self.subTest(key=key, replacement=replacement):
                value = _health_reply()
                value[key] = replacement
                self.reject(value)

    def test_nested_live_execution_flag_requires_boolean_false(self):
        value = _health_reply()
        value['boundaries']['live_execution'] = 0
        self.reject(value)

    def test_child_numeric_types_and_internal_exit_state_must_be_consistent(self):
        mutations = [('pid', True), ('pid', 0), ('progress_age_ms', True),
            ('progress_age_ms', -1), ('stderr_bytes', False), ('stdout_bytes', -1),
            ('reaped', 0), ('returncode', False), ('returncode', 143),
            ('native_result', {'exit_code': 3, 'terminal': 1, 'state': 'blocked', 'reason': 'capacity'})]
        for key, replacement in mutations:
            with self.subTest(key=key, replacement=replacement):
                value = _health_reply()
                value['children']['capture'][key] = replacement
                self.reject(value)

    def test_running_cannot_claim_missing_ownership_stale_progress_or_finished_worker(self):
        mutations = [('guarded', False), ('owned', False), ('progress_age_ms', None),
            ('progress_age_ms', 5001), ('forced', True),
            ('native_result', {'exit_code': 143, 'terminal': False, 'state': 'stopped', 'reason': None})]
        for key, replacement in mutations:
            with self.subTest(key=key):
                value = _health_reply()
                value['children']['stream'][key] = replacement
                self.reject(value)
        value = _health_reply()
        del value['children']['stream']
        self.reject(value)

    def test_running_requires_two_distinct_direct_child_processes(self):
        value = _health_reply()
        value['children']['stream']['pid'] = value['children']['capture']['pid']
        self.reject(value)

    def test_known_output_overflow_cannot_be_reported_as_running(self):
        value = _health_reply()
        value['children']['capture'].update(stderr_bytes=262145, stderr_truncated=True)
        self.reject(value)

    def test_degraded_reply_can_preserve_real_terminal_outcome(self):
        value = _health_reply()
        value.update(status='degraded', exit_code=1)
        value['children']['stream'].update(returncode=3, reaped=True,
            native_result=dict(exit_code=3, terminal=True, state='blocked', reason='capacity'))
        observed = self.decode(value)
        self.assertTrue(observed['children']['stream']['native_result']['terminal'])
        self.assertEqual(observed['exit_code'], 1)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.service = importlib.import_module('trading_bot.paper_service')
        self.protocol = importlib.import_module('trading_bot.paper_service_protocol')
        self.owner, self.attempt = _Owner(), _Attempt()
        read, self.listener_write = os.pipe()
        self.listener = os.fdopen(read, 'rb', buffering=0)
        self.monitor = self.service._Supervisor(self.owner, self.attempt, self.listener, None)
        self.handles = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for process in self.handles:
            if isinstance(process, subprocess.Popen):
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
            else:
                process.close()
        self.listener.close()
        os.close(self.listener_write)
        self.monitor.selector.close()

    def child(self, role, *, signal_fault=None):
        process = _OwnedHandle(role, 12001 if role == 'capture' else 12002,
                               signal_fault=signal_fault)
        self.handles.append(process)
        child = self.service._Child(role, process, ATTEMPT)
        child.frames.feed(self.protocol.encode_frame('guarded', ATTEMPT, role, 0)
            + self.protocol.encode_frame('owned', ATTEMPT, role, 1))
        child.last_progress = time.monotonic()
        self.monitor.children[role] = child
        return child

    def ready_pair(self):
        self.child('capture')
        self.child('stream')
        self.monitor.phase = 'running'

    def assert_degraded_snapshot(self, expected):
        report = self.monitor.snapshot()
        self.assertEqual(report['status'], expected)
        self.assertEqual(report['exit_code'], 1)
        report['challenge'] = CHALLENGE
        decoded = self.service._health_response(
            self.protocol.encode_document(report, 8191), self.owner, self.attempt, CHALLENGE)
        self.assertEqual(decoded['status'], expected)

    def test_snapshot_of_newly_exited_child_is_degraded_without_waiting_for_next_inspection(self):
        self.ready_pair()
        self.monitor.children['stream'].process.returncode = 143
        self.assert_degraded_snapshot('degraded')

    def test_snapshot_of_expired_progress_is_degraded_without_waiting_for_next_inspection(self):
        self.ready_pair()
        self.monitor.children['stream'].last_progress -= 6
        self.assert_degraded_snapshot('degraded')

    def test_snapshot_of_latched_stop_is_stopping_before_cleanup_phase_changes(self):
        self.ready_pair()
        self.monitor.requested = 15
        self.assert_degraded_snapshot('stopping')

    def spawn_real(self, role, directory, attempt, parent):
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', _WORKER,
            str(ROOT), role, attempt, str(parent)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        self.handles.append(process)
        return process

    def test_failed_second_spawn_stops_and_reaps_the_actual_first_child(self):
        launches = []
        def spawn(role, directory, attempt, parent):
            launches.append(role)
            if role == 'stream':
                raise OSError('injected second spawn failure')
            return self.spawn_real(role, directory, attempt, parent)
        with patch.object(self.service, '_spawn_worker', spawn):
            result = self.monitor.run()
        self.assertEqual(launches, ['capture', 'stream'])
        self.assertEqual(result['exit_code'], 4)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(result['children']['capture']['returncode'], 143)
        self.assertIsNotNone(self.handles[0].poll())
        self.assertTrue(self.handles[0].stdout.closed)
        self.assertTrue(self.handles[0].stderr.closed)
        self.assertEqual(self.attempt.saved[0][0]['exit_code'], 4)

    def test_pipe_setup_failure_cannot_lose_the_already_spawned_handle(self):
        actual = os.set_blocking
        calls = []
        def setup(descriptor, blocking):
            calls.append(descriptor)
            if len(calls) == 1:
                raise OSError('injected pipe setup failure')
            return actual(descriptor, blocking)
        with patch.object(self.service, '_spawn_worker', self.spawn_real), \
             patch.object(self.service.os, 'set_blocking', setup), \
             patch.object(self.service, '_REAP_SECONDS', .1):
            result = self.monitor.run()
        self.assertEqual(result['exit_code'], 4)
        self.assertEqual(len(self.handles), 1)
        self.assertIsNotNone(self.handles[0].poll())
        self.assertTrue(self.handles[0].stdout.closed)
        self.assertTrue(self.handles[0].stderr.closed)

    def test_pipe_read_refuses_a_descriptor_that_cannot_be_made_nonblocking(self):
        child = self.child('capture')
        self.assertTrue(os.get_blocking(child.process.stdout.fileno()))
        # Fault the OS boundary rather than actually hanging the test runner.
        # A failed conversion must prevent the potentially blocking os.read.
        with patch.object(self.service.os, 'get_blocking', return_value=True), \
             patch.object(self.service.os, 'set_blocking', side_effect=OSError('conversion failed')), \
             patch.object(self.service.os, 'read', side_effect=OSError('unsafe read attempted')) as read:
            with self.assertRaises(OSError):
                self.monitor._pipe('stdout', child)
            read.assert_not_called()

    def test_repeated_owner_check_failure_still_drains_and_reaps_both_actual_children(self):
        def fail_after_pair_started():
            if len(self.monitor.children) == 2:
                raise ValueError('injected directory identity change')
        self.owner.check_fault = fail_after_pair_started
        with patch.object(self.service, '_spawn_worker', self.spawn_real):
            result = self.monitor.run()
        self.assertEqual(result['exit_code'], 4)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(len(self.handles), 2)
        self.assertTrue(all(process.poll() is not None for process in self.handles))
        self.assertEqual(set(self.attempt.saved[0][1]), {'capture', 'stream'})

    def test_unexpected_signal_handle_error_does_not_abandon_either_owned_child(self):
        self.child('capture')
        self.child('stream', signal_fault=RuntimeError('injected signalling failure'))
        self.monitor.requested = 15
        caught = None
        result = None
        try:
            with patch.object(self.service, '_REAP_SECONDS', .1):
                result = self.monitor.run()
        except BaseException as error:
            caught = error
        self.assertIsNone(caught, 'cleanup raised before preserving/containing the pair: ' + repr(caught))
        self.assertEqual(result['exit_code'], 4)
        self.assertTrue(all(process.poll() is not None for process in self.handles))
        self.assertTrue(self.attempt.saved)

    def test_native_refusal_is_retained_and_storage_failure_outweighs_signal(self):
        capture = self.child('capture')
        stream = self.child('stream')
        self.monitor.phase = 'stopping'
        self.monitor.requested = 15
        for child in (capture, stream):
            child.sent_signal = 15
        capture.frames.feed(self.protocol.encode_frame('result', ATTEMPT, 'capture', 2,
            exit_code=143, terminal=True, state='stopped', reason='rate_blocked'))
        capture.process.returncode = 143
        self.monitor._inspect_children()
        self.assertEqual(self.monitor.failure, 3)
        stream.frames.feed(self.protocol.encode_frame('result', ATTEMPT, 'stream', 2,
            exit_code=4, terminal=False, state=None, reason='worker_storage_failed'))
        stream.process.returncode = 4
        self.monitor._inspect_children()
        self.assertEqual(self.monitor.failure, 4)
        self.assertEqual(capture.public(time.monotonic())['native_result']['reason'], 'rate_blocked')

    def test_stderr_overflow_is_bounded_and_drain_continues_after_failure(self):
        child = self.child('capture')
        os.set_blocking(child.process.stderr.fileno(), False)
        for _ in range(65):
            os.write(child.process._writes['stderr'], b'x' * 4096)
            self.monitor._pipe('stderr', child)
        self.assertEqual(len(child.stderr), 262144)
        self.assertEqual(child.stderr_bytes, 266240)
        self.assertTrue(child.stderr_truncated)
        self.assertEqual(self.monitor.failure, 4)
        os.write(child.process._writes['stderr'], b'end')
        self.monitor._pipe('stderr', child)
        self.assertEqual(len(child.stderr), 262144)
        self.assertEqual(child.stderr_bytes, 266243)

    def test_frame_rate_overflow_is_uncertain_even_when_frames_are_individually_valid(self):
        child = self.child('capture')
        os.set_blocking(child.process.stdout.fileno(), False)
        payload = b''.join(self.protocol.encode_frame('progress', ATTEMPT, 'capture', number)
                           for number in range(2, 35))
        for offset in range(0, len(payload), 3000):
            os.write(child.process._writes['stdout'], payload[offset:offset+3000])
            self.monitor._pipe('stdout', child)
        self.assertTrue(child.protocol_failed)
        self.assertEqual(self.monitor.failure, 4)
        self.assertLessEqual(len(child.frame_times), 32)
        os.write(child.process._writes['stdout'], b'not stored after failure')
        self.monitor._pipe('stdout', child)
        self.assertEqual(child.stdout_bytes, len(payload) + 24)


if __name__ == '__main__':
    unittest.main()
