"""One-shot monitoring cannot start workers or declare live readiness."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'paper_monitor.py'
SPEC = importlib.util.spec_from_file_location('paper_monitor', SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MonitorTests(unittest.TestCase):
    def test_known_healthy_states(self):
        args = SimpleNamespace(service='/tmp/s', capture='/tmp/c', account='/tmp/a', timeout_seconds=10)
        def good(name, directory, seconds):
            return {'healthy': True, 'exit_code': 0,
                    'status': 'running' if name == 'service' else 'current'}
        result = module.inspect(args, probe=good)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(set(result['checks']), {'service', 'capture', 'account'})
        self.assertFalse(result['boundaries']['production_ready'])
        self.assertFalse(result['boundaries']['notification_delivered'])

    def test_degraded_failure_propagates(self):
        args = SimpleNamespace(service='/tmp/s', capture='/tmp/c', account=None, timeout_seconds=10)
        result = module.inspect(args, lambda name, directory, seconds:
                                {'healthy': name == 'service',
                                 'exit_code': 0 if name == 'service' else 1,
                                 'status': 'running' if name == 'service' else 'stale'})
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['status'], 'attention_required')

    def test_parse_refuses_missing_service(self):
        with self.assertRaises(ValueError):
            module._arguments(['--capture', '/tmp/c'])

    def test_parse_refuses_relative(self):
        with self.assertRaises(ValueError):
            module._arguments(['--service', '../tmp/s'])

    def test_parse_refuses_invalid_timeout(self):
        with self.assertRaises(ValueError):
            module._arguments(['--service', '/tmp/s', '--timeout-seconds', '0'])

    def test_probe_correct_command_and_clean_environment(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=b'{"status":"running"}', stderr=b'')
        with mock.patch.object(module.subprocess, 'run', return_value=fake) as run:
            result = module._run_probe('service', '/tmp/s', 9)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[1:3], ['-I', '-B'])
        self.assertEqual(cmd[-2:], ['check', '/tmp/s'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertTrue(result['healthy'])

    def test_wrong_status_cannot_pass(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=b'{"status":"stopped"}', stderr=b'')
        with mock.patch.object(module.subprocess, 'run', return_value=fake):
            self.assertFalse(module._run_probe('service', '/tmp/s', 10)['healthy'])

    def test_timeout_distinguished(self):
        with mock.patch.object(module.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('probe', 1)):
            self.assertEqual(module._run_probe('service', '/tmp/s', 1)['status'], 'timeout')

    def test_bad_response_refused(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout=b'not json', stderr=b'')
        with mock.patch.object(module.subprocess, 'run', return_value=fake):
            self.assertEqual(module._run_probe('service', '/tmp/s', 10)['status'],
                             'invalid_response')

    def test_real_invalid_missing_service_never_starts(self):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), '--service',
                                 '/this-missing-service-directory'],
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['checks']['service']['healthy'])


if __name__ == '__main__':
    unittest.main()
