"""PAPER monitor event journal behavior and refusal boundary tests."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'paper_monitor_events.py'
spec = importlib.util.spec_from_file_location('paper_monitor_events', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def observation(states=('running', 'current', 'current'), statuses=(0, 0, 0)):
    checks = {}
    expected = {'service': 'running', 'capture': 'current', 'account': 'current'}
    for i, name in enumerate(expected):
        healthy = states[i] == expected[name] and statuses[i] == 0
        checks[name] = {'healthy': healthy, 'status': states[i],
                        'exit_code': statuses[i]}
    ok = all(x['healthy'] for x in checks.values())
    return {'schema_version': 1, 'source': 'paper_monitor',
            'status': 'healthy' if ok else 'attention_required',
            'exit_code': 0 if ok else 1, 'checks': checks}


class EventTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'private'
        self.root.mkdir(mode=0o700)
        self.input = Path(tmp.name) / 'monitor.json'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(TOOL), *args],
                              capture_output=True, text=True, timeout=8)

    def submit(self, raw):
        self.input.write_text(json.dumps(raw))
        self.input.chmod(0o600)
        return self.run_cli('record', '--directory', str(self.root),
                            '--input', str(self.input))

    def test_first_healthy_and_repeat_deduplicates(self):
        first = self.submit(observation())
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertTrue(json.loads(first.stdout)['event_created'])
        second = self.submit(observation())
        self.assertEqual(second.returncode, 0)
        self.assertFalse(json.loads(second.stdout)['event_created'])
        check = self.run_cli('check', '--directory', str(self.root))
        self.assertEqual(json.loads(check.stdout)['event_count'], 1)
        self.assertEqual(json.loads(check.stdout)['observation_count'], 2)

    def test_failure_and_recovery_new_events(self):
        self.submit(observation())
        failure = observation(states=('running', 'stale', 'current'),
                              statuses=(0, 1, 0))
        self.assertTrue(json.loads(self.submit(failure).stdout)['event_created'])
        recovered = self.submit(observation())
        self.assertTrue(json.loads(recovered.stdout)['event_created'])
        self.assertEqual(json.loads(recovered.stdout)['event_sequence'], 3)

    def test_fault_signature_change_is_new_event(self):
        self.submit(observation(states=('running', 'stale', 'current'),
                                statuses=(0, 1, 0)))
        result = self.submit(observation(states=('running', 'timeout', 'current'),
                                         statuses=(0, 1, 0)))
        self.assertTrue(json.loads(result.stdout)['event_created'])

    def test_reject_incomplete_service_only_monitor(self):
        value = observation()
        value['checks'].pop('capture')
        self.assertEqual(self.submit(value).returncode, 2)
        self.assertFalse((self.root / 'paper-monitor-events.sqlite').exists())

    def test_reject_false_healthy(self):
        value = observation(states=('stopped', 'current', 'current'),
                            statuses=(1, 0, 0))
        value['status'], value['exit_code'] = 'healthy', 0
        self.assertEqual(self.submit(value).returncode, 2)

    def test_reject_inconsistent_probe(self):
        value = observation()
        value['checks']['service']['healthy'] = False
        self.assertEqual(self.submit(value).returncode, 2)

    def test_reject_world_readable_input(self):
        self.input.write_text(json.dumps(observation()))
        self.input.chmod(0o644)
        result = self.run_cli('record', '--directory', str(self.root),
                              '--input', str(self.input))
        self.assertEqual(result.returncode, 2)

    def test_reject_symlink_input(self):
        self.input.write_text(json.dumps(observation()))
        self.input.chmod(0o600)
        alias = self.input.parent / 'alias'
        alias.symlink_to(self.input)
        result = self.run_cli('record', '--directory', str(self.root),
                              '--input', str(alias))
        self.assertEqual(result.returncode, 2)

    def test_reject_symlink_root(self):
        alias = self.root.parent / 'alias'
        alias.symlink_to(self.root)
        self.assertEqual(self.run_cli('check', '--directory', str(alias)).returncode, 2)

    def test_refuse_existing_db_symlink(self):
        (self.root / 'paper-monitor-events.sqlite').symlink_to(self.input)
        self.assertEqual(self.submit(observation()).returncode, 2)

    def test_refuse_clock_regression_and_preserve_state(self):
        db = module._open_database(self.root)
        _, signature = module._observe(observation())
        module._record(db, 'healthy', signature, now='2026-11-15T10:00:00.000000+00:00')
        with self.assertRaises(module.Invalid):
            module._record(db, 'healthy', signature,
                           now='2026-11-14T10:00:00.000000+00:00')
        self.assertEqual(module._check(db)['observation_count'], 1)

    def test_capacity_refuses_transition_preserves_state(self):
        db = module._open_database(self.root)
        _, signature = module._observe(observation())
        module._record(db, 'healthy', signature)
        with mock.patch.object(module, 'MAX_EVENTS', 1):
            with self.assertRaises(module.Invalid):
                module._record(db, 'attention_required', signature + 'x')
        self.assertEqual(module._check(db)['event_count'], 1)

    def test_read_only_check_does_not_create_database(self):
        result = self.run_cli('check', '--directory', str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_arguments_return_json(self):
        result = self.run_cli('record')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')

    def test_duplicate_json_keys_refused(self):
        raw = json.dumps(observation())
        self.input.write_text(raw[:-1] + ', "status": "healthy"}')
        self.input.chmod(0o600)
        self.assertEqual(self.run_cli('record', '--directory', str(self.root),
                                      '--input', str(self.input)).returncode, 2)

    def test_symlink_parent_of_input_refused(self):
        folder = self.root.parent / 'folder'
        folder.mkdir()
        target = folder / 'monitor.json'
        target.write_text(json.dumps(observation()))
        target.chmod(0o600)
        alias = self.root.parent / 'linked-folder'
        alias.symlink_to(folder)
        self.assertEqual(self.run_cli('record', '--directory', str(self.root),
                                      '--input', str(alias / 'monitor.json')).returncode, 2)

    def test_concurrent_writers_deduplicate(self):
        import concurrent.futures
        self.input.write_text(json.dumps(observation()))
        self.input.chmod(0o600)
        def run(_):
            return self.run_cli('record', '--directory', str(self.root),
                                '--input', str(self.input))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, range(6)))
        self.assertTrue(all(r.returncode == 0 for r in results),
                        [(r.returncode, r.stdout, r.stderr) for r in results])
        state = module._check(self.root / 'paper-monitor-events.sqlite')
        self.assertEqual(state['observation_count'], 6)
        self.assertEqual(state['event_count'], 1)

    def test_db_readonly_uri_encodes_special_path(self):
        special = self.root.parent / 'private?mode=memory'
        special.mkdir(mode=0o700)
        _, signature = module._observe(observation())
        db = module._open_database(special)
        module._record(db, 'healthy', signature)
        self.assertEqual(module._check(db)['event_count'], 1)

    def test_wall_clock_sampled_after_writer_lock(self):
        from datetime import datetime as real_datetime, timezone
        db = module._open_database(self.root)
        _, signature = module._observe(observation())
        acquired = []
        original_connect = module._connect

        class Wrapped:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, *args):
                result = self.inner.execute(sql, *args)
                if sql == 'BEGIN IMMEDIATE':
                    acquired.append(True)
                return result

            def close(self):
                self.inner.close()

        class FakeClock:
            @staticmethod
            def now(zone):
                if not acquired:
                    raise AssertionError('clock sampled before acquiring write lock')
                return real_datetime(2026, 11, 15, tzinfo=timezone.utc)

        with mock.patch.object(module, '_connect',
                               side_effect=lambda path: Wrapped(original_connect(path))), \
             mock.patch.object(module, 'datetime', FakeClock):
            self.assertTrue(module._record(db, 'healthy', signature)['event_created'])

    def test_database_private(self):
        self.submit(observation())
        db = self.root / 'paper-monitor-events.sqlite'
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
