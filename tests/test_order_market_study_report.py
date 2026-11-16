"""Private HTML export exercises real study inspection and publication boundaries."""
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))

from test_order_market_run import fixture
from test_order_market_study import inventory, plan
from trading_bot import market_paper_study
from trading_core.paper_evidence import PaperEvidencePublishedError, _publish


class StudyReportCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_root = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture_root.cleanup)
        base = Path(cls.fixture_root.name)
        source = base / 'synthetic-history.json'
        source.write_text(json.dumps(fixture(64)))
        cls.original = base / 'study'
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            asyncio.run(market_paper_study.run_market_study(
                source, plan(), directory=cls.original))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.study = self.root / 'study'
        shutil.copytree(self.original, self.study)
        self.output = self.root / 'report.html'
        self.before = inventory(self.study)
        guard = ExitStack()
        self.addCleanup(guard.close)
        guard.enter_context(patch('socket.create_connection', side_effect=AssertionError('network forbidden')))
        guard.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')))

    def cli(self, *args):
        # Also forbid network inside the child interpreter, not only this test.
        guard = self.root / 'guard'
        guard.mkdir(exist_ok=True)
        (guard / 'sitecustomize.py').write_text(
            "import socket\n"
            "def blocked(*a, **k): raise AssertionError('network forbidden')\n"
            "socket.create_connection = blocked\n"
            "socket.getaddrinfo = blocked\n"
            "socket.socket.connect = blocked\n")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=str(guard) + os.pathsep + os.environ.get('PYTHONPATH', ''))
        completed = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/paper_study.py'),
                                    *map(str, args)], capture_output=True, text=True,
                                   timeout=40, env=env)
        return completed, json.loads(completed.stdout)

    def local_cli(self, *args):
        spec = importlib.util.spec_from_file_location('study_report_cli', ROOT / 'tools/paper_study.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = module.main(list(map(str, args)))
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def assert_source_unchanged(self):
        self.assertEqual(inventory(self.study), self.before)

    def test_check_exports_private_html_and_preserves_json_and_accounts(self):
        process, report = self.cli('check', self.study, '--html', self.output)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'verified_study')
        self.assertTrue(report['receipt_validated'])
        self.assertTrue(report['read_only'])
        self.assertEqual(report['operation'], 'inspect_existing_study')
        self.assertEqual([row['id'] for row in report['candidates']], ['ma-fast', 'rsi-14'])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertIn('<!doctype html', self.output.read_text().lower())
        self.assert_source_unchanged()

    def test_missing_receipt_stays_incomplete_and_is_not_repaired(self):
        (self.study / 'study-result.json').unlink()
        self.before = inventory(self.study)
        process, report = self.cli('check', self.study, '--html', self.output)
        self.assertEqual(process.returncode, 4, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'complete_without_receipt')
        self.assertFalse(report['receipt_validated'])
        self.assertEqual(len(report['candidates']), 2)
        self.assertTrue(self.output.is_file())
        self.assertFalse((self.study / 'study-result.json').exists())
        self.assert_source_unchanged()

    def test_corrupt_receipt_keeps_rows_and_attention_exit_with_html(self):
        (self.study / 'study-result.json').write_text('{broken')
        self.before = inventory(self.study)
        process, report = self.cli('check', self.study, '--html', self.output)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(report.get('status'), 'blocked')
        self.assertFalse(report['receipt_validated'])
        self.assertEqual(len(report['candidates']), 2)
        self.assertTrue(self.output.is_file())
        self.assert_source_unchanged()

    def test_unavailable_study_has_an_honest_empty_diagnostic_report(self):
        missing = self.root / 'missing'
        process, report = self.cli('check', missing, '--html', self.output)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(report.get('status'), 'unavailable')
        self.assertEqual(report['candidates'], [])
        self.assertTrue(self.output.is_file())
        self.assertFalse(missing.exists())

    def test_plain_check_keeps_existing_behavior_without_any_html_write(self):
        process, report = self.cli('check', self.study)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'verified_study')
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()

    def test_existing_output_is_never_clobbered(self):
        self.output.write_bytes(b'preserve this report')
        process, report = self.cli('check', self.study, '--html', self.output)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(report['error'], 'market_study_report_output_invalid')
        self.assertEqual(self.output.read_bytes(), b'preserve this report')
        self.assert_source_unchanged()

    def test_explicit_empty_html_path_is_not_silently_ignored(self):
        process, report = self.cli('check', self.study, '--html', '')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(report, {'error': 'market_study_report_output_invalid'})
        self.assert_source_unchanged()

    def test_symlink_output_is_rejected_without_following_it(self):
        self.output.symlink_to(self.study / 'study-result.json')
        process, report = self.cli('check', self.study, '--html', self.output)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(report['error'], 'market_study_report_output_invalid')
        self.assertTrue(self.output.is_symlink())
        self.assert_source_unchanged()

    def test_source_descendant_and_alias_are_rejected(self):
        alias = self.root / 'source-alias'
        alias.symlink_to(self.study, target_is_directory=True)
        for target in (self.study / 'report.html', alias / 'runs' / 'report.html'):
            with self.subTest(target=target.name):
                process, report = self.cli('check', self.study, '--html', target)
                self.assertEqual(process.returncode, 2)
                self.assertEqual(report['error'], 'market_study_report_output_invalid')
                self.assertFalse(target.exists())
        self.assert_source_unchanged()

    def test_unsafe_output_parent_is_rejected_before_inspection(self):
        parent = self.root / 'shared'
        parent.mkdir(mode=0o755)
        marker = self.study / 'unexpected-inspection'
        async def unexpected(_):
            marker.write_text('inspection should never have started')
            raise AssertionError('unexpected inspection')
        with patch.object(market_paper_study, 'inspect_market_study', unexpected):
            code, result, _ = self.local_cli('check', self.study, '--html', parent / 'report.html')
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'market_study_report_output_invalid')
        self.assertFalse(marker.exists())
        self.assertFalse((parent / 'report.html').exists())
        self.assert_source_unchanged()

    def test_output_parent_alias_changed_during_check_cannot_write_into_study(self):
        parent = self.root / 'reports'
        parent.mkdir(mode=0o700)
        original = market_paper_study.inspect_market_study
        async def changed(directory):
            report = await original(directory)
            parent.rename(self.root / 'previous-reports')
            parent.symlink_to(self.study, target_is_directory=True)
            return report
        with patch.object(market_paper_study, 'inspect_market_study', changed):
            code, result, _ = self.local_cli('check', self.study, '--html', parent / 'report.html')
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'market_study_report_output_invalid')
        self.assertFalse((self.study / 'report.html').exists())
        self.assert_source_unchanged()

    def test_output_created_during_check_is_preserved(self):
        original = market_paper_study.inspect_market_study
        async def changed(directory):
            report = await original(directory)
            self.output.write_text('newer file from another command')
            return report
        with patch.object(market_paper_study, 'inspect_market_study', changed):
            code, result, _ = self.local_cli('check', self.study, '--html', self.output)
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], 'market_study_report_output_invalid')
        self.assertEqual(self.output.read_text(), 'newer file from another command')
        self.assert_source_unchanged()

    def test_publisher_entry_alias_swap_cannot_write_into_source(self):
        parent = self.root / 'reports'
        parent.mkdir(mode=0o700)
        moved = self.root / 'original-reports'
        def swapped(raw, target, **kwargs):
            parent.rename(moved)
            parent.symlink_to(self.study, target_is_directory=True)
            return _publish(raw, target, **kwargs)
        with patch('trading_core.paper_evidence._publish', swapped):
            code, report, _ = self.local_cli('check', self.study, '--html', parent / 'report.html')
        self.assertEqual(code, 2)
        self.assertEqual(report, {'error': 'market_study_failed_inspect_directory'})
        self.assertFalse((self.study / 'report.html').exists())
        self.assertEqual(list(moved.iterdir()), [])
        self.assert_source_unchanged()

    def test_same_parent_inode_moved_into_source_is_also_rejected(self):
        parent = self.root / 'reports'
        parent.mkdir(mode=0o700)
        moved = self.study / 'moved-reports'
        def relocated(raw, target, **kwargs):
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
            return _publish(raw, target, **kwargs)
        with patch('trading_core.paper_evidence._publish', relocated):
            code, report, _ = self.local_cli('check', self.study, '--html', parent / 'report.html')
        self.assertEqual(code, 2)
        self.assertEqual(report, {'error': 'market_study_failed_inspect_directory'})
        self.assertEqual(list(moved.iterdir()), [])
        self.assert_source_unchanged()

    def test_publication_uncertainty_returns_three_and_retains_output(self):
        def uncertain(raw, target, **kwargs):
            _publish(raw, target, **kwargs)
            raise PaperEvidencePublishedError('paper_evidence_published_check_required')
        with patch('trading_core.paper_evidence._publish', uncertain):
            code, result, _ = self.local_cli('check', self.study, '--html', self.output)
        self.assertEqual(code, 3)
        self.assertEqual(result, {'error': 'paper_evidence_published_check_required'})
        self.assertTrue(self.output.is_file())
        self.assert_source_unchanged()

    def test_publication_failure_never_leaks_path_or_returns_success(self):
        with patch('trading_core.paper_evidence._publish', side_effect=OSError('private-account-path-token')):
            code, result, stderr = self.local_cli('check', self.study, '--html', self.output)
        self.assertEqual(code, 2)
        self.assertEqual(result, {'error': 'market_study_failed_inspect_directory'})
        self.assertNotIn('private-account', json.dumps(result) + stderr)
        self.assertFalse(self.output.exists())
        self.assert_source_unchanged()

    def test_run_html_with_unavailable_source_does_not_create_a_study(self):
        target = self.root / 'not-created'
        process, result = self.cli('run', self.root / 'unused.json', '--plan',
                                   ROOT / 'config/examples/market-study-plan.json',
                                   '--directory', target, '--html', self.output)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(result, {'error': 'market_study_failed_inspect_directory',
                                  'phase': 'execution'})
        self.assertFalse(target.exists())
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
