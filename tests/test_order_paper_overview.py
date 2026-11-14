"""Read-only multi-run inspection, real historical engine and static HTML output."""
import asyncio
from contextlib import ExitStack
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))
from trading_bot.durable_paper_run import run_durable_replay
from trading_bot.paper_runner import PaperRunConfig
from trading_core.paper_session import _PaperAdapter, PaperTradingSession


def fixture(n=80):
    values = [100+(i % 20 if (i//20) % 2 == 0 else 20-i % 20) for i in range(n)]
    return [[1700000000000+i*3600000, str(x), str(x+2), str(x-2),
             str(Decimal(x)+Decimal('.5')), '1'] for i, x in enumerate(values)]


def inventory(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


class OverviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = PaperRunConfig(initial_balance='1000', max_daily_loss_pct=100,
                                    max_drawdown_pct=100)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def module(self):
        try:
            value = importlib.import_module('trading_bot.durable_paper_overview')
        except ModuleNotFoundError:
            value = None
        self.assertIsNotNone(value, 'missing multi-run overview')
        return value

    async def create(self, name='run', bars=12, lose=False, resumable=True):
        self.module()
        path = self.root/name
        original = _PaperAdapter.create_order
        async def lost(adapter, *args, **kwargs):
            await original(adapter, *args, **kwargs)
            raise TimeoutError('private-provider-message')
        with ExitStack() as stack:
            if lose:
                stack.enter_context(patch.object(_PaperAdapter, 'create_order', lost))
            try:
                await run_durable_replay(fixture(bars), config=self.config,
                    directory=path, resumable=resumable)
            except Exception:
                if not lose:
                    raise
        return path

    def cli(self, *args):
        script = ROOT/'tools/paper_overview.py'
        self.assertTrue(script.is_file(), 'missing overview CLI')
        return subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
            capture_output=True, text=True, timeout=40,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    async def test_completed_incomplete_and_missing_rows_are_all_kept(self):
        mod = self.module()
        complete = await self.create('complete')
        pending = await self.create('pending')
        (pending/'result.json').unlink()
        before = inventory(self.root)
        with patch.object(PaperTradingSession, '__init__', side_effect=AssertionError('writer forbidden')):
            report = await mod.inspect_runs([('완료', complete), ('pending', pending),
                                            ('missing', self.root/'missing')])
        self.assertEqual([r['status'] for r in report['runs']],
            ['verified_completion', 'compatible_snapshot', 'unavailable'])
        self.assertEqual(report['counts']['total'], 3)
        self.assertEqual(report['counts']['verified_completion'], 1)
        self.assertFalse(report['all_completed'])
        self.assertFalse(report['resume_authorized'])
        self.assertEqual(mod.exit_code(report), 2)
        self.assertEqual(inventory(self.root), before)
        self.assertNotIn(str(self.root), json.dumps(report))

    async def test_all_complete_and_single_run_counts(self):
        mod = self.module(); path = await self.create()
        report = await mod.inspect_runs([('one', path)])
        self.assertTrue(report['all_completed'])
        self.assertEqual(report['counts']['verified_completion'], 1)
        self.assertEqual(report['runs'][0]['inspection'], 'completion_audit')
        self.assertEqual(mod.exit_code(report), 0)
        self.assertNotIn('total_cash', report)

    async def test_incomplete_is_not_complete_despite_exit_zero(self):
        mod = self.module(); path = await self.create()
        (path/'result.json').unlink()
        report = await mod.inspect_runs([('one', path)])
        self.assertEqual(report['runs'][0]['inspection'], 'preflight')
        self.assertFalse(report['all_completed'])
        self.assertEqual(mod.exit_code(report), 0)

    async def test_lost_ack_is_not_reconciled_and_sets_attention_exit(self):
        mod = self.module(); path = await self.create(bars=80, lose=True)
        before = inventory(path)
        report = await mod.inspect_runs([('lost', path)])
        self.assertEqual(report['runs'][0]['status'], 'reconciliation_required')
        self.assertEqual(mod.exit_code(report), 4)
        self.assertEqual(inventory(path), before)

    async def test_bad_completion_marker_never_falls_back_to_preflight(self):
        mod = self.module(); path = await self.create()
        (path/'result.json').write_text('{}')
        with patch.object(mod, 'inspect_durable_replay', side_effect=AssertionError('fallback forbidden')):
            report = await mod.inspect_runs([('bad', path)])
        self.assertEqual(report['runs'][0]['status'], 'blocked')
        self.assertFalse(report['all_completed'])

    async def test_unmarked_legacy_run_stays_blocked(self):
        mod = self.module(); path = await self.create(resumable=False)
        report = await mod.inspect_runs([('legacy', path)])
        self.assertEqual(report['runs'][0]['reason'], 'manifest_missing')

    async def test_bad_inputs_fail_before_any_inspection(self):
        mod = self.module()
        bad = [[], [('x', self.root)]*9, [('x', self.root), ('x', self.root/'b')],
               [('x', self.root), ('y', self.root/'.')], [('<script>', self.root)],
               [('x', '')], [('x', None)], [('x', self.root, 'extra')]]
        with patch.object(mod, 'audit_durable_replay') as a, patch.object(mod, 'inspect_durable_replay') as p:
            for args in bad:
                with self.subTest(args=args):
                    with self.assertRaises(ValueError):
                        await mod.inspect_runs(args)
            a.assert_not_called(); p.assert_not_called()

    async def test_eight_missing_runs_are_retained_in_order(self):
        mod = self.module()
        report = await mod.inspect_runs([(f'r{i}', self.root/str(i)) for i in range(8)])
        self.assertEqual([r['label'] for r in report['runs']], [f'r{i}' for i in range(8)])
        self.assertEqual(report['counts']['unavailable'], 8)

    async def test_unexpected_failure_is_redacted_and_next_run_still_inspected(self):
        mod = self.module(); a = await self.create('a'); b = await self.create('b')
        original = mod.audit_durable_replay
        async def failing(path):
            if path == a:
                raise RuntimeError('/private/key=do-not-leak')
            return await original(path)
        with patch.object(mod, 'audit_durable_replay', failing):
            report = await mod.inspect_runs([('a', a), ('b', b)])
        self.assertEqual([r['status'] for r in report['runs']], ['unavailable', 'verified_completion'])
        self.assertNotIn('do-not-leak', json.dumps(report))

    async def test_cancel_propagates_without_partial_success(self):
        mod = self.module(); path = await self.create()
        with patch.object(mod, 'audit_durable_replay', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await mod.inspect_runs([('a', path)])

    async def test_directory_symlink_is_not_followed_as_valid_run(self):
        mod = self.module(); path = await self.create()
        alias = self.root/'alias'; alias.symlink_to(path, target_is_directory=True)
        report = await mod.inspect_runs([('alias', alias)])
        self.assertEqual(report['runs'][0]['status'], 'blocked')

    async def test_completion_replaced_after_inspector_returns_invalidates_row(self):
        mod = self.module(); path = await self.create()
        original = mod.audit_durable_replay
        async def replaced(target):
            report = await original(target)
            (target/'result.json').write_text('{}')
            return report
        with patch.object(mod, 'audit_durable_replay', replaced):
            report = await mod.inspect_runs([('a', path)])
        self.assertEqual(report['runs'][0]['reason'], 'source_changed')
        self.assertNotIn('snapshot', report['runs'][0]['diagnostic'])

    async def test_individual_observation_times_not_atomic_portfolio_claim(self):
        mod = self.module(); path = await self.create()
        report = await mod.inspect_runs([('a', path)])
        self.assertFalse(report['simultaneous_snapshot'])
        self.assertEqual(report['owner_state'], 'unobserved')
        self.assertIn('started_at', report['runs'][0]); self.assertIn('finished_at', report['runs'][0])

    async def test_html_has_no_scripts_or_external_requests_and_escapes_details(self):
        mod = self.module(); path = await self.create()
        report = await mod.inspect_runs([('demo', path)])
        renderer = importlib.import_module('trading_bot.paper_overview_html')
        report['runs'][0]['diagnostic']['hostile'] = '<script>alert("x")</script>'
        html = renderer.render_overview(report)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script', html.lower())
        self.assertNotIn('https://', html)
        self.assertIn('Content-Security-Policy', html)
        self.assertIn('실거래', html)
        self.assertIn('<details', html)

    async def test_cli_publishes_new_private_html_and_keeps_json_stdout(self):
        path = await self.create(); out = self.root/'overview.html'
        before = inventory(path)
        result = self.cli('--run', f'ma={path}', '--html', out)
        self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
        self.assertTrue(json.loads(result.stdout)['all_completed'])
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertIn('모의 운용', out.read_text())
        self.assertEqual(inventory(path), before)

    async def test_cli_existing_output_is_not_overwritten(self):
        path = await self.create(); out = self.root/'overview.html'
        out.write_text('KEEP'); out.chmod(0o600)
        result = self.cli('--run', f'ma={path}', '--html', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(out.read_text(), 'KEEP')
        self.assertNotIn(str(self.root), result.stdout)

    async def test_cli_output_inside_source_run_is_refused(self):
        path = await self.create(); before = inventory(path)
        result = self.cli('--run', f'ma={path}', '--html', path/'overview.html')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(inventory(path), before)

    async def test_cli_blocked_run_still_has_report_and_html(self):
        self.module(); out = self.root/'overview.html'
        result = self.cli('--run', f'missing={self.root / "absent"}', '--html', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['counts']['unavailable'], 1)
        self.assertTrue(out.is_file())

    async def test_cli_invalid_arguments_are_json_and_do_not_leak_paths(self):
        self.module()
        for args in [[], ['--live'], ['--run', 'no-equal'], ['--run', 'x='],
                     ['--run', f'a={self.root}', '--force']]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['reason'], 'invalid_arguments')
            self.assertNotIn(str(self.root), result.stdout)

    async def test_directory_replacement_discards_successful_diagnostic(self):
        mod = self.module(); path = await self.create()
        original = mod.audit_durable_replay
        async def replaced(target):
            report = await original(target)
            target.rename(self.root/'old')
            target.mkdir(mode=0o700)
            return report
        with patch.object(mod, 'audit_durable_replay', replaced):
            report = await mod.inspect_runs([('a', path)])
        self.assertEqual(report['runs'][0]['status'], 'blocked')
        self.assertNotIn('counts', report['runs'][0]['diagnostic'])

    async def test_new_completion_marker_during_preflight_discards_result(self):
        mod = self.module(); path = await self.create()
        (path/'result.json').unlink()
        original = mod.inspect_durable_replay
        async def completed(target):
            report = await original(target)
            (target/'result.json').write_text('{}')
            return report
        with patch.object(mod, 'inspect_durable_replay', completed):
            report = await mod.inspect_runs([('a', path)])
        self.assertEqual(report['runs'][0]['reason'], 'source_changed')
        self.assertEqual(mod.exit_code(report), 2)

    async def test_invalid_inspector_output_never_counts_as_success(self):
        mod = self.module(); path = await self.create()
        with patch.object(mod, 'audit_durable_replay', return_value={
                'status':'verified_completion', 'mode':'live', 'reason':'ok'}):
            report = await mod.inspect_runs([('a', path)])
        self.assertEqual(report['runs'][0]['reason'], 'invalid_inspection_result')
        self.assertEqual(report['counts']['verified_completion'], 0)

    async def test_blocked_status_takes_precedence_over_reconciliation_exit(self):
        mod = self.module(); path = await self.create(bars=80, lose=True)
        report = await mod.inspect_runs([('lost', path), ('missing', self.root/'no')])
        self.assertEqual(report['counts']['reconciliation_required'], 1)
        self.assertEqual(mod.exit_code(report), 2)

    async def test_duplicate_dotdot_path_is_rejected(self):
        mod = self.module()
        with self.assertRaises(ValueError):
            await mod.inspect_runs([('a', self.root/'run'), ('b', self.root/'x/../run')])

    async def test_existing_symlink_output_is_not_followed_or_overwritten(self):
        path = await self.create(); original = self.root/'keep'
        original.write_text('KEEP'); out = self.root/'overview.html'; out.symlink_to(original)
        result = self.cli('--run', f'a={path}', '--html', out)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(original.read_text(), 'KEEP')
        self.assertTrue(out.is_symlink())

    async def test_public_output_directory_is_refused(self):
        path = await self.create(); public = self.root/'public'; public.mkdir(mode=0o755)
        result = self.cli('--run', f'a={path}', '--html', public/'overview.html')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((public/'overview.html').exists())

    async def test_private_output_via_source_directory_alias_is_refused(self):
        path = await self.create(); alias = self.root/'alias'; alias.symlink_to(path)
        before = inventory(path)
        result = self.cli('--run', f'a={path}', '--html', alias/'overview.html')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(inventory(path), before)


if __name__ == '__main__':
    unittest.main()
