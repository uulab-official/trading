"""Read real saved reports without research execution or account/file mutation."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('apps/server', 'packages/core', 'tests'):
    sys.path.insert(0, str(ROOT / part))

from test_quant_research import CANDIDATES, SETTINGS, dataset, metrics
from test_research_robustness import report as robust_report
from trading_bot.research_report import MAX_BYTES, write_report
from trading_bot.walk_forward_research import run_research


GUARD = '''
import importlib.abc
import sys

class NoExecutionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('trading_bot.walk_forward_research', 'trading_bot.paper_runner',
                     'trading_bot.market_paper_study', 'trading_bot.market_paper_run',
                     'trading_bot.durable_paper_run', 'trading_core.config_manager',
                     'trading_core.telegram_notifier', 'trading_data',
                     'trading_strategy', 'ccxt', 'pandas', 'numpy', 'quant_research')
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise AssertionError('execution integration imported: ' + fullname)

def no_external_state(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                 'socket.gethostbyaddr', 'socket.getnameinfo', 'socket.sendto',
                 'socket.sendmsg', 'sqlite3.connect'):
        raise AssertionError('inspection attempted network/account access')

sys.meta_path.insert(0, NoExecutionImports())
sys.addaudithook(no_external_state)
'''


class ResearchReportCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'research.json'
        self.output = self.root / 'research.html'
        self.report = run_research(dataset(), 'a' * 64, candidates=CANDIDATES,
                                   settings=SETTINGS, evaluator=lambda *args: metrics('-2'))
        write_report(self.source, self.report)
        self.before = self.source.read_bytes()
        self.script = ROOT / 'tools/paper_research_report.py'
        self.assertTrue(self.script.is_file(), 'read-only research inspection CLI is missing')

    def cli(self, *args):
        guard = self.root / 'guard'
        guard.mkdir(exist_ok=True)
        (guard / 'sitecustomize.py').write_text(GUARD)
        env = dict(os.environ, PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(self.script), *map(str, args)],
                                capture_output=True, text=True, timeout=15, env=env)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result, json.loads(result.stdout)

    def local_cli(self, *args):
        spec = importlib.util.spec_from_file_location('research_snapshot_cli', self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = module.main(list(map(str, args)))
        return code, json.loads(out.getvalue()), err.getvalue()

    def unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.before)

    def test_inspection_returns_original_summary_without_html_or_execution(self):
        result, data = self.cli(self.source)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(data, dict(mode='paper', read_only=True, live_enabled=False,
            status='research_report_checked', validation_scope='saved_report_consistency',
            schema_version=1, run_id=self.report['run_id'], summary=self.report['summary'],
            robustness_available=False, html_written=False))
        self.assertFalse(self.output.exists())
        self.unchanged()

    def test_both_versions_export_private_html_without_execution(self):
        for version, report in ((1, self.report), (2, robust_report())):
            with self.subTest(version=version):
                source = self.root / f'research-{version}.json'
                output = self.root / f'research-{version}.html'
                write_report(source, report)
                before = source.read_bytes()
                result, data = self.cli(source, '--html', output)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertTrue(data['html_written'])
                self.assertEqual(data['robustness_available'], version == 2)
                self.assertEqual(data['summary'], report['summary'])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                document = output.read_text()
                self.assertIn('<!doctype html', document.lower())
                self.assertIn(report['run_id'], document)
                self.assertEqual(source.read_bytes(), before)

    def test_all_skipped_summary_is_not_replaced_by_an_empty_experiment(self):
        skipped = run_research(dataset(), 'b' * 64, candidates=CANDIDATES,
            settings=SETTINGS, evaluator=lambda *args: metrics('0', 0))
        source = self.root / 'skipped.json'
        write_report(source, skipped)
        result, data = self.cli(source, '--html', self.output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data['summary']['total_folds'], 3)
        self.assertEqual(data['summary']['skipped_folds'], 3)
        self.assertEqual(data['summary']['evaluated_folds'], 0)
        self.assertEqual(data['summary']['mean_test_return_pct'], '0')

    def test_inconsistent_and_unknown_report_fields_cannot_publish(self):
        mutations = (
            lambda value: value.update(run_id='b' * 64),
            lambda value: value['folds'][0].update(selected='b'),
            lambda value: value['folds'][0].update(test_start=0),
            lambda value: value['summary'].update(mean_test_return_pct='9'),
            lambda value: value.update(live_enabled=True),
            lambda value: value.update(secret='NEVER_ECHO_PRIVATE_VALUE'),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = copy.deepcopy(self.report)
                mutate(value)
                self.source.write_text(json.dumps(value))
                before = self.source.read_bytes()
                result, data = self.cli(self.source, '--html', self.output)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertNotIn('NEVER_ECHO_PRIVATE_VALUE', result.stdout + result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), before)

    def test_robustness_corruption_cannot_publish(self):
        for section in ('policy', 'summary', 'scenario'):
            with self.subTest(section=section):
                value = robust_report()
                if section == 'policy':
                    value['robustness']['policy']['selection'] = 'future'
                elif section == 'summary':
                    value['robustness']['summary'][0]['mean_excess_return_pp'] = '99'
                else:
                    value['robustness']['folds'][0]['scenarios'][1]['buy_hold']['fees'] = '99'
                self.source.write_text(json.dumps(value))
                result, data = self.cli(self.source, '--html', self.output)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertFalse(self.output.exists())

    def test_duplicate_oversized_and_bad_encoding_reports_fail_closed(self):
        for raw in (b'{"mode":"paper","mode":"live"}', b' ' * (MAX_BYTES + 1),
                    b'\xff\xfeinvalid', b'{"secret":"NEVER_ECHO_PRIVATE_VALUE"}'):
            with self.subTest(length=len(raw)):
                self.source.write_bytes(raw)
                result, data = self.cli(self.source, '--html', self.output)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertNotIn('NEVER_ECHO_PRIVATE_VALUE', result.stdout + result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), raw)

    def test_symlink_directory_fifo_and_missing_sources_are_rejected(self):
        link = self.root / 'source-link.json'
        link.symlink_to(self.source)
        fifo = self.root / 'source-fifo.json'
        os.mkfifo(fifo)
        for source in (link, self.root, fifo, self.root / 'missing.json'):
            with self.subTest(source=source.name):
                result, data = self.cli(source, '--html', self.output)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertFalse(self.output.exists())
        self.unchanged()

    def test_invalid_arguments_and_empty_output_are_bounded_json_errors(self):
        for args in ((), (self.source, '--unknown', 'NEVER_ECHO_PRIVATE_VALUE'),
                     (self.source, '--html', ''), (self.source, '--html')):
            with self.subTest(args=args):
                result, data = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertNotIn('NEVER_ECHO_PRIVATE_VALUE', result.stdout + result.stderr)
                self.assertFalse(self.output.exists())
        self.unchanged()

    def test_existing_source_hardlink_and_symlink_targets_are_preserved(self):
        existing = self.root / 'existing.html'
        existing.write_bytes(b'keep-existing')
        hardlink = self.root / 'hardlink.html'
        os.link(self.source, hardlink)
        symlink = self.root / 'symlink.html'
        symlink.symlink_to(self.source)
        broken = self.root / 'broken.html'
        broken.symlink_to(self.root / 'missing')
        for target in (self.source, existing, hardlink, symlink, broken):
            with self.subTest(target=target.name):
                result, data = self.cli(self.source, '--html', target)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.unchanged()
        self.assertEqual(existing.read_bytes(), b'keep-existing')
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(broken.is_symlink())

    def test_public_and_missing_output_directories_are_not_modified(self):
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        missing = self.root / 'missing-parent'
        for parent in (public, missing):
            with self.subTest(parent=parent.name):
                result, data = self.cli(self.source, '--html', parent / 'out.html')
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', data)
                self.assertFalse((parent / 'out.html').exists())
        self.assertFalse(missing.exists())
        self.assertEqual(public.stat().st_mode & 0o777, 0o755)
        self.unchanged()

    def test_existing_private_parent_alias_is_resolved_consistently(self):
        private = self.root / 'private'
        private.mkdir(mode=0o700)
        alias = self.root / 'alias'
        alias.symlink_to(private, target_is_directory=True)
        result, data = self.cli(self.source, '--html', alias / 'report.html')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(data['html_written'])
        self.assertTrue((private / 'report.html').is_file())
        self.unchanged()

    def test_concurrent_target_creation_is_never_overwritten(self):
        from trading_core import paper_evidence
        publish = paper_evidence._publish

        def race(raw, target, **kwargs):
            self.output.write_bytes(b'concurrent-writer')
            return publish(raw, target, **kwargs)

        with patch.object(paper_evidence, '_publish', side_effect=race):
            code, data, _ = self.local_cli(self.source, '--html', self.output)
        self.assertEqual(code, 2)
        self.assertIn('error', data)
        self.assertEqual(self.output.read_bytes(), b'concurrent-writer')
        self.assertFalse(list(self.root.glob('.paper-evidence-*.tmp')))
        self.unchanged()

    def test_late_parent_replacement_cannot_publish_into_new_directory(self):
        from trading_core import paper_evidence
        publish = paper_evidence._publish
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                parent = self.root / f'output-{symlink}'
                parent.mkdir(mode=0o700)
                moved = self.root / f'old-{symlink}'
                replacement = self.root / f'replacement-{symlink}'
                replacement.mkdir(mode=0o700)

                def race(raw, target, **kwargs):
                    parent.rename(moved)
                    if symlink:
                        parent.symlink_to(replacement, target_is_directory=True)
                    else:
                        parent.mkdir(mode=0o700)
                    return publish(raw, target, **kwargs)

                with patch.object(paper_evidence, '_publish', side_effect=race):
                    code, data, _ = self.local_cli(self.source, '--html', parent / 'report.html')
                self.assertEqual(code, 2)
                self.assertEqual(data, {'error': 'research_report_output_unavailable'})
                self.assertTrue(moved.is_dir(), 'publication must reach the replacement boundary')
                for directory in (parent, moved, replacement):
                    self.assertFalse((directory / 'report.html').exists())
                    self.assertFalse(list(directory.glob('.paper-evidence-*.tmp')))
                self.unchanged()

    def test_same_inode_parent_relocation_is_rejected(self):
        from trading_core import paper_evidence
        publish = paper_evidence._publish
        parent = self.root / 'original'
        parent.mkdir(mode=0o700)
        moved = self.root / 'relocated'
        before = parent.stat()

        def race(raw, target, **kwargs):
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
            return publish(raw, target, **kwargs)

        with patch.object(paper_evidence, '_publish', side_effect=race):
            code, data, _ = self.local_cli(self.source, '--html', parent / 'report.html')
        self.assertEqual(code, 2)
        self.assertIn('error', data)
        self.assertEqual(moved.stat().st_ino, before.st_ino)
        self.assertFalse((moved / 'report.html').exists())
        self.unchanged()

    def test_directory_sync_failure_preserves_published_file_and_returns_uncertain(self):
        from trading_core import paper_evidence
        original = os.fsync

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('private filesystem details must stay private')
            return original(fd)

        with patch.object(paper_evidence.os, 'fsync', side_effect=fail_directory):
            code, data, err = self.local_cli(self.source, '--html', self.output)
        self.assertEqual(code, 3)
        self.assertEqual(data, {'error': 'research_report_published_check_required'})
        self.assertNotIn('private filesystem', json.dumps(data) + err)
        self.assertTrue(self.output.is_file())
        self.assertIn('<!doctype html', self.output.read_text().lower())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.unchanged()

    def test_prepublication_failure_leaves_no_output_or_owned_temporary(self):
        from trading_core import paper_evidence
        with patch.object(paper_evidence.os, 'fsync', side_effect=OSError('private detail')):
            code, data, err = self.local_cli(self.source, '--html', self.output)
        self.assertEqual(code, 2)
        self.assertEqual(data, {'error': 'research_report_output_unavailable'})
        self.assertNotIn('private detail', json.dumps(data) + err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.paper-evidence-*.tmp')))
        self.unchanged()

    def test_keyboard_interrupt_has_explicit_exit_and_no_success_claim(self):
        from trading_bot import research_report
        with patch.object(research_report, 'read_report', side_effect=KeyboardInterrupt):
            code, data, _ = self.local_cli(self.source, '--html', self.output)
        self.assertEqual(code, 130)
        self.assertEqual(data, {'error': 'research_report_interrupted_inspect_output'})
        self.assertFalse(self.output.exists())
        self.unchanged()

    def test_interrupt_after_link_preserves_completed_output_without_success(self):
        from trading_core import paper_evidence
        original = os.link

        def interrupt_after_link(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt

        with patch.object(paper_evidence.os, 'link', side_effect=interrupt_after_link):
            code, data, _ = self.local_cli(self.source, '--html', self.output)
        self.assertEqual(code, 130)
        self.assertEqual(data, {'error': 'research_report_interrupted_inspect_output'})
        self.assertTrue(self.output.is_file())
        self.assertIn('<!doctype html', self.output.read_text().lower())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.unchanged()


if __name__ == '__main__':
    unittest.main()
