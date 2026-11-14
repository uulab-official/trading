"""Explicit run-to-report orchestration uses original studies and fresh audits."""
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
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
import test_order_market_study_report as report_tests
from trading_bot import market_paper_study as study
from trading_bot import market_study_html
from trading_core.paper_evidence import PaperEvidencePublishedError, _publish


def replace_contents(source, target):
    """Replace a test bundle's contents while preserving its root inode."""
    for path in target.iterdir():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in source.iterdir():
        if path.is_dir():
            shutil.copytree(path, target / path.name)
        else:
            shutil.copy2(path, target / path.name)


class StudyWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'synthetic-history.json'
        self.source.write_text(json.dumps(fixture(64), indent=2))
        self.plan = self.root / 'plan.json'
        self.plan.write_text(json.dumps(plan()))
        self.directory = self.root / 'study'
        self.output = self.root / 'report.html'
        spec = importlib.util.spec_from_file_location('study_workflow_cli', ROOT / 'tools/paper_study.py')
        self.command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.command)
        guards = ExitStack()
        self.addCleanup(guards.close)
        for name in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect'):
            guards.enter_context(patch(name, side_effect=AssertionError('network forbidden')))

    def args(self, output=None):
        return ['run', str(self.source), '--plan', str(self.plan), '--directory',
                str(self.directory), '--html', str(self.output if output is None else output)]

    def local_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.command.main(list(map(str, args)))
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def cli(self, *args):
        return report_tests.StudyReportCLITests.cli(self, *args)

    def assert_failure(self, result, code, phase):
        self.assertEqual(result, {'error': code, 'phase': phase})
        self.assertNotIn(str(self.root), json.dumps(result))

    def script_cli(self, script):
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
        completed = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT),
            str(self.source), str(self.plan), str(self.directory), str(self.output)],
            capture_output=True, text=True, timeout=40, env=env)
        return completed, json.loads(completed.stdout)

    def test_run_once_then_fresh_inspection_preserves_both_results(self):
        events, captured = [], {}
        real_run, real_inspect = study.run_market_study, study.inspect_market_study
        real_render = market_study_html.render_study_report
        original_inputs = (self.source.read_bytes(), self.plan.read_bytes())

        async def execute(source, selected_plan, *, directory):
            self.assertEqual(Path(source), self.source)
            self.assertEqual(selected_plan, plan())
            events.append('execute')
            result = await real_run(source, selected_plan, directory=directory)
            captured['execution'] = deepcopy(result)
            print('execution diagnostic')
            return result

        async def inspect(directory):
            self.assertEqual(Path(directory), self.directory)
            events.append('inspect')
            before = inventory(self.directory)
            result = await real_inspect(directory)
            self.assertEqual(inventory(self.directory), before)
            captured['inspection'] = deepcopy(result)
            print('inspection diagnostic')
            return result

        def render(report):
            events.append('render')
            self.assertEqual(report, captured['inspection'])
            self.assertTrue(report['read_only'])
            return real_render(report)

        with patch.object(study, 'run_market_study', execute), patch.object(
                study, 'inspect_market_study', inspect), patch.object(
                market_study_html, 'render_study_report', render):
            code, result, stderr = self.local_cli(*self.args())
        self.assertEqual(code, 0, result)
        self.assertEqual(events, ['execute', 'inspect', 'render'])
        self.assertEqual(set(result), {'schema_version', 'source', 'mode', 'operation',
            'read_only', 'live_enabled', 'resume_authorized', 'status', 'report_published',
            'execution', 'inspection'})
        self.assertEqual(result['source'], 'market_paper_study_workflow')
        self.assertEqual(result['operation'], 'execute_new_study_with_report')
        self.assertEqual(result['status'], 'verified_study')
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['mode'], 'paper')
        self.assertFalse(result['read_only'])
        self.assertFalse(result['live_enabled'])
        self.assertFalse(result['resume_authorized'])
        self.assertTrue(result['report_published'])
        self.assertEqual(result['execution'], captured['execution'])
        self.assertEqual(result['inspection'], captured['inspection'])
        self.assertIn('execution diagnostic', stderr)
        self.assertIn('inspection diagnostic', stderr)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(original_inputs, (self.source.read_bytes(), self.plan.read_bytes()))
        self.assertEqual((self.directory / 'source.json').read_bytes(), original_inputs[0])

    def test_real_subprocess_and_separate_check_keep_original_audited_results(self):
        completed, result = self.cli(*self.args())
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertFalse(result['execution']['read_only'])
        self.assertTrue(result['inspection']['read_only'])
        self.assertEqual([r['id'] for r in result['inspection']['candidates']], ['ma-fast', 'rsi-14'])
        before = inventory(self.directory)
        checked, report = self.cli('check', self.directory)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(report['operation'], 'inspect_existing_study')
        self.assertEqual(report['source_sha256'], result['inspection']['source_sha256'])
        self.assertEqual([r['metrics'] for r in report['candidates']],
                         [r['metrics'] for r in result['inspection']['candidates']])
        self.assertEqual(inventory(self.directory), before)
        self.assertIn('<!doctype html', self.output.read_text().lower())

    def test_invalid_html_preflight_cannot_start_execution_or_create_study(self):
        existing = self.root / 'existing.html'
        existing.write_bytes(b'preserve')
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        linked = self.root / 'linked.html'
        linked.symlink_to(self.source)
        targets = [existing, public / 'report.html', self.root / 'missing' / 'report.html',
                   '', self.directory, self.directory / 'report.html', self.source, self.plan, linked]
        before = (self.source.read_bytes(), self.plan.read_bytes())
        with patch.object(study, 'run_market_study', side_effect=AssertionError('unexpected execution')):
            for target in targets:
                with self.subTest(target=str(target)):
                    code, result, _ = self.local_cli(*self.args(output=target))
                    self.assertEqual(code, 2)
                    self.assert_failure(result, 'market_study_report_output_invalid', 'preflight')
                    self.assertFalse(self.directory.exists())
        self.assertEqual(existing.read_bytes(), b'preserve')
        self.assertTrue(linked.is_symlink())
        self.assertEqual(before, (self.source.read_bytes(), self.plan.read_bytes()))

    def test_existing_study_or_final_symlink_is_rejected_before_execution(self):
        real = self.root / 'existing'
        real.mkdir(mode=0o700)
        (real / 'keep').write_bytes(b'evidence')
        linked = self.root / 'alias'
        linked.symlink_to(real, target_is_directory=True)
        dangling = self.root / 'dangling'
        dangling.symlink_to(self.root / 'not-created', target_is_directory=True)
        with patch.object(study, 'run_market_study', side_effect=AssertionError('unexpected execution')):
            for directory in (real, linked, dangling):
                with self.subTest(directory=directory.name):
                    self.directory = directory
                    code, result, _ = self.local_cli(*self.args())
                    self.assertEqual(code, 2)
                    self.assert_failure(result, 'market_study_directory_not_new', 'preflight')
        self.assertEqual((real / 'keep').read_bytes(), b'evidence')
        self.assertTrue(linked.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(self.output.exists())

    def test_invalid_later_candidate_still_leaves_no_study_or_html(self):
        bad = plan()
        bad['candidates'][1]['rsi_period'] = 0
        self.plan.write_text(json.dumps(bad))
        code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_invalid_plan', 'execution')
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.output.exists())

    def test_execution_errors_keep_primary_exit_and_never_start_reporting(self):
        cases = [(ValueError('private-token'), 2, 'market_study_failed_inspect_directory'),
                 (PaperEvidencePublishedError('paper_evidence_published_check_required'), 3,
                  'paper_evidence_published_check_required'),
                 (ValueError('durable_replay_stop_unconfirmed'), 3, 'durable_replay_stop_unconfirmed'),
                 (asyncio.CancelledError(), 130, 'market_study_interrupted_inspect_directory')]
        for index, (error, expected, public_code) in enumerate(cases):
            with self.subTest(error=type(error).__name__, code=public_code):
                self.directory = self.root / ('partial-' + str(index))
                calls = []
                async def fail(*args, directory, **kwargs):
                    calls.append('execute')
                    Path(directory).mkdir(mode=0o700)
                    (Path(directory) / 'keep').write_bytes(b'partial evidence')
                    raise error
                async def forbidden(*args, **kwargs):
                    calls.append('inspect')
                    raise AssertionError('unexpected inspection')
                with patch.object(study, 'run_market_study', fail), patch.object(
                        study, 'inspect_market_study', forbidden):
                    code, result, stderr = self.local_cli(*self.args())
                self.assertEqual(code, expected)
                self.assert_failure(result, public_code, 'execution')
                self.assertEqual(calls, ['execute'])
                self.assertEqual((self.directory / 'keep').read_bytes(), b'partial evidence')
                self.assertFalse(self.output.exists())
                self.assertNotIn('private-token', json.dumps(result) + stderr)

    def test_bound_missing_or_corrupt_receipt_remains_diagnostic_without_repair(self):
        real_run = study.run_market_study
        for action, expected, status in [('remove', 4, 'complete_without_receipt'),
                                          ('corrupt', 2, 'blocked')]:
            with self.subTest(action=action):
                self.directory = self.root / action
                self.output = self.root / (action + '.html')
                async def changed(*args, directory, **kwargs):
                    result = await real_run(*args, directory=directory, **kwargs)
                    receipt = Path(directory) / 'study-result.json'
                    if action == 'remove':
                        receipt.unlink()
                    else:
                        receipt.write_bytes(b'{broken')
                    return result
                with patch.object(study, 'run_market_study', changed):
                    code, result, _ = self.local_cli(*self.args())
                self.assertEqual(code, expected, result)
                self.assertIn('execution', result)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['execution']['status'], 'verified_study')
                self.assertEqual(result['inspection']['status'], status)
                self.assertFalse(result['inspection']['receipt_validated'])
                self.assertTrue(result['report_published'])
                self.assertTrue(self.output.is_file())
                receipt = self.directory / 'study-result.json'
                if action == 'remove':
                    self.assertFalse(receipt.exists())
                else:
                    self.assertEqual(receipt.read_bytes(), b'{broken')

    def test_different_successful_account_with_same_source_plan_is_not_exported(self):
        foreign = self.root / 'foreign'
        asyncio.run(study.run_market_study(self.source, plan(), directory=foreign))
        real_inspect = study.inspect_market_study
        async def replaced(directory):
            before = Path(directory).stat().st_ino
            replace_contents(foreign, Path(directory))
            self.assertEqual(Path(directory).stat().st_ino, before)
            report = await real_inspect(directory)
            self.assertEqual(report['status'], 'verified_study')
            return report
        with patch.object(study, 'inspect_market_study', replaced):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_changed', 'inspection')
        self.assertFalse(self.output.exists())
        self.assertTrue((self.directory / 'study-result.json').is_file())

    def test_foreign_source_or_plan_diagnostic_is_not_attributed_to_execution(self):
        real_inspect = study.inspect_market_study
        for kind in ('source', 'plan'):
            with self.subTest(kind=kind):
                foreign = self.root / ('foreign-' + kind)
                other_source = self.root / ('other-' + kind + '.json')
                other_source.write_text(json.dumps(fixture(68 if kind == 'source' else 64), indent=2))
                other_plan = plan()
                if kind == 'plan':
                    other_plan['common']['initial_balance'] = '2000000'
                asyncio.run(study.run_market_study(other_source, other_plan, directory=foreign))
                (foreign / 'study-result.json').unlink()
                self.directory = self.root / ('selected-' + kind)
                async def replaced(directory):
                    replace_contents(foreign, Path(directory))
                    report = await real_inspect(directory)
                    self.assertEqual(report['status'], 'complete_without_receipt')
                    return report
                with patch.object(study, 'inspect_market_study', replaced):
                    code, result, _ = self.local_cli(*self.args())
                self.assertEqual(code, 2)
                self.assert_failure(result, 'market_study_changed', 'inspection')
                self.assertFalse(self.output.exists())

    def test_unbound_minimal_inspection_is_not_exported(self):
        real_run = study.run_market_study
        async def changed(*args, directory, **kwargs):
            result = await real_run(*args, directory=directory, **kwargs)
            (Path(directory) / 'source.json').write_bytes(b'{invalid')
            return result
        with patch.object(study, 'run_market_study', changed):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_changed', 'inspection')
        self.assertFalse(self.output.exists())
        self.assertEqual((self.directory / 'source.json').read_bytes(), b'{invalid')

    def test_identical_root_replacement_or_final_symlink_after_pin_is_blocked(self):
        real_inspect = study.inspect_market_study
        for kind in ('copy', 'symlink'):
            with self.subTest(kind=kind):
                self.directory = self.root / ('study-' + kind)
                moved = self.root / ('original-' + kind)
                async def replaced(directory):
                    report = await real_inspect(directory)
                    Path(directory).rename(moved)
                    if kind == 'copy':
                        shutil.copytree(moved, directory)
                    else:
                        Path(directory).symlink_to(moved, target_is_directory=True)
                    return report
                with patch.object(study, 'inspect_market_study', replaced):
                    code, result, _ = self.local_cli(*self.args())
                self.assertEqual(code, 2)
                self.assert_failure(result, 'market_study_changed', 'inspection')
                self.assertFalse(self.output.exists())
                self.assertTrue((moved / 'study-result.json').is_file())

    def test_root_is_rechecked_after_rendering_before_publication(self):
        real_render = market_study_html.render_study_report
        moved = self.root / 'original-study'
        def replaced(report):
            document = real_render(report)
            self.directory.rename(moved)
            shutil.copytree(moved, self.directory)
            return document
        with patch.object(market_study_html, 'render_study_report', replaced):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_changed', 'report_publication')
        self.assertFalse(self.output.exists())
        self.assertTrue((moved / 'study-result.json').is_file())

    def test_output_created_during_execution_is_preserved_without_rerun(self):
        real_run = study.run_market_study
        calls = []
        async def changed(*args, **kwargs):
            calls.append('run')
            result = await real_run(*args, **kwargs)
            self.output.write_bytes(b'another writer')
            return result
        with patch.object(study, 'run_market_study', changed):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_report_output_invalid', 'report_publication')
        self.assertEqual(calls, ['run'])
        self.assertEqual(self.output.read_bytes(), b'another writer')
        self.assertTrue((self.directory / 'study-result.json').is_file())

    def test_output_parent_rebound_to_study_does_not_write_into_it(self):
        parent = self.root / 'reports'
        parent.mkdir(mode=0o700)
        self.output = parent / 'report.html'
        real_inspect = study.inspect_market_study
        async def changed(directory):
            report = await real_inspect(directory)
            parent.rename(self.root / 'original-reports')
            parent.symlink_to(self.directory, target_is_directory=True)
            return report
        with patch.object(study, 'inspect_market_study', changed):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 2)
        self.assert_failure(result, 'market_study_report_output_invalid', 'report_publication')
        self.assertFalse((self.directory / 'report.html').exists())
        self.assertTrue((self.directory / 'study-result.json').is_file())

    def test_html_failure_and_uncertainty_preserve_completed_study(self):
        real_inspect = study.inspect_market_study
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                self.directory = self.root / ('study-' + str(uncertain))
                self.output = self.root / (str(uncertain) + '.html')
                def fail(raw, target, **kwargs):
                    if uncertain:
                        _publish(raw, target, **kwargs)
                        raise PaperEvidencePublishedError('paper_evidence_published_check_required')
                    raise OSError('private-account-path-token')
                stack = ExitStack()
                with stack:
                    async def inspected(directory):
                        result = await real_inspect(directory)
                        stack.enter_context(patch('trading_core.paper_evidence._publish', fail))
                        return result
                    with patch.object(study, 'inspect_market_study', inspected):
                        code, result, stderr = self.local_cli(*self.args())
                self.assertEqual(code, 3 if uncertain else 2)
                self.assert_failure(result, 'paper_evidence_published_check_required' if uncertain
                                    else 'market_study_failed_inspect_directory', 'report_publication')
                self.assertEqual(self.output.exists(), uncertain)
                self.assertNotIn('private-account', json.dumps(result) + stderr)
                self.assertTrue((self.directory / 'study-result.json').is_file())
                self.assertEqual(asyncio.run(real_inspect(self.directory))['status'], 'verified_study')

    def test_interrupted_inspection_preserves_execution_without_html(self):
        async def cancelled(directory):
            raise asyncio.CancelledError()
        with patch.object(study, 'inspect_market_study', cancelled):
            code, result, _ = self.local_cli(*self.args())
        self.assertEqual(code, 130)
        self.assert_failure(result, 'market_study_interrupted_inspect_directory', 'inspection')
        self.assertTrue((self.directory / 'study-result.json').is_file())
        self.assertFalse(self.output.exists())

    def test_real_sigterm_after_fill_preserves_partial_study_and_never_reports(self):
        # The first MA candidate has no fills in 64 bars; 100 reaches its first
        # fill, so interruption must leave the later RSI candidate unstarted.
        self.source.write_text(json.dumps(fixture(100), indent=2))
        script = """
import asyncio, os, signal, sys
from pathlib import Path
root = Path(sys.argv[1])
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server', 'tools'):
    sys.path.insert(0, str(root / part))
from trading_core.paper_session import _PaperAdapter
original = _PaperAdapter.create_order
async def stopped(self, *args, **kwargs):
    result = await original(self, *args, **kwargs)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0)
    return result
_PaperAdapter.create_order = stopped
import paper_study
raise SystemExit(paper_study.main(['run', sys.argv[2], '--plan', sys.argv[3],
    '--directory', sys.argv[4], '--html', sys.argv[5]]))
"""
        completed, result = self.script_cli(script)
        self.assertEqual(completed.returncode, 143, completed.stdout + completed.stderr)
        self.assertEqual(result, {'error': 'market_study_interrupted_inspect_directory',
            'phase': 'execution', 'signal': 'SIGTERM', 'account_state': 'inspect_saved_record'})
        self.assertFalse(self.output.exists())
        self.assertFalse((self.directory / 'study-result.json').exists())
        self.assertFalse((self.directory / 'runs' / 'rsi-14').exists())
        inspected = asyncio.run(study.inspect_market_study(self.directory))
        self.assertEqual(inspected['status'], 'incomplete_study')
        self.assertEqual([r['status'] for r in inspected['candidates']],
                         ['reconciliation_required', 'not_started'])
        from trading_core.paper_session_observer import PaperSessionObserver
        saved = PaperSessionObserver(self.directory / 'runs/ma-fast/run/account.sqlite').read_evidence()
        self.assertEqual(len(saved['payload']['book']['trades']), 1)
        self.assertEqual(saved['payload']['control']['mode'], 'halted')

    def test_real_sigterm_after_html_publication_cannot_become_success(self):
        script = """
import os, signal, sys
from pathlib import Path
root = Path(sys.argv[1])
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server', 'tools'):
    sys.path.insert(0, str(root / part))
import trading_core.paper_evidence as evidence
original = evidence._publish
def stopped(raw, target, **kwargs):
    result = original(raw, target, **kwargs)
    if Path(target) == Path(sys.argv[5]):
        os.kill(os.getpid(), signal.SIGTERM)
    return result
evidence._publish = stopped
import paper_study
raise SystemExit(paper_study.main(['run', sys.argv[2], '--plan', sys.argv[3],
    '--directory', sys.argv[4], '--html', sys.argv[5]]))
"""
        completed, result = self.script_cli(script)
        self.assertEqual(completed.returncode, 143, completed.stdout + completed.stderr)
        self.assertEqual(result, {'error': 'market_study_interrupted_inspect_directory',
            'phase': 'report_publication', 'signal': 'SIGTERM', 'account_state': 'inspect_saved_record'})
        self.assertTrue(self.output.is_file())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(asyncio.run(study.inspect_market_study(self.directory))['status'], 'verified_study')


if __name__ == '__main__':
    unittest.main()
