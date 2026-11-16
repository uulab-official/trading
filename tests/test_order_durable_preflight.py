"""Read-only recovery inspection with original replay, real accounts and CLI."""
import asyncio
from contextlib import ExitStack
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from trading_bot import durable_paper_resume as recovery
from trading_bot.durable_paper_run import run_durable_replay
from trading_bot.paper_runner import PaperRunner, PaperRunConfig
from trading_core.paper_session import PaperTradingSession, _PaperAdapter
from trading_core.paper_session_observer import PaperSessionObserver


def fixture(n=100):
    values = [100 + (i % 20 if (i // 20) % 2 == 0 else 20-i % 20) for i in range(n)]
    return [[1700000000000+i*3600000, str(x), str(x+2), str(x-2),
             str(Decimal(x)+Decimal('.5')), '1'] for i, x in enumerate(values)]


def inventory(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode,
            p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'run'
        self.config = PaperRunConfig(initial_balance='1000', max_daily_loss_pct=100,
                                    max_drawdown_pct=100)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('no network'))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def module(self):
        try:
            mod = importlib.import_module('trading_bot.durable_paper_preflight')
        except ModuleNotFoundError:
            mod = None
        self.assertIsNotNone(mod, 'missing read-only recovery preflight')
        return mod

    async def interrupted(self, kind='confirmed', *, config=None):
        config = config or self.config
        if kind in ('no-fill', 'lost-ack'):
            original = _PaperAdapter.create_order
            async def lose(adapter, *args, **kwargs):
                if kind == 'lost-ack':
                    await original(adapter, *args, **kwargs)
                raise TimeoutError('private-provider-detail')
            with patch.object(_PaperAdapter, 'create_order', lose):
                with self.assertRaises(Exception):
                    await run_durable_replay(fixture(), config=config, directory=self.path, resumable=True)
        else:
            original = PaperRunner._execute_pending
            async def stop(runner, exchange, manager, pending, state):
                await original(runner, exchange, manager, pending, state)
                if exchange.snapshot()['trades']:
                    raise RuntimeError('test-stop')
            with patch.object(PaperRunner, '_execute_pending', stop):
                with self.assertRaisesRegex(RuntimeError, 'test-stop'):
                    await run_durable_replay(fixture(), config=config, directory=self.path, resumable=True)

    async def inspect(self):
        return await self.module().inspect_durable_replay(self.path)

    def cli(self, *args):
        script = ROOT / 'tools/paper_preflight.py'
        self.assertTrue(script.is_file(), 'missing preflight CLI')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
                              capture_output=True, text=True, timeout=20, env=env)

    async def test_confirmed_prefix_and_remaining_count(self):
        self.module()
        await self.interrupted()
        before = inventory(self.path)
        report = await self.inspect()
        expected = await PaperRunner(self.config).run(fixture())
        self.assertEqual(report['status'], 'compatible_snapshot')
        self.assertEqual(report['counts']['expected_fills'], len(expected['trades']))
        self.assertEqual(report['counts']['saved_fills'], 1)
        self.assertEqual(report['counts']['remaining_fills'], len(expected['trades'])-1)
        self.assertEqual(report['counts']['unconfirmed_saved_fills'], 0)
        self.assertFalse(report['resume_authorized'])
        self.assertEqual(report['owner_state'], 'unobserved')
        self.assertEqual(inventory(self.path), before)

    async def test_never_opens_writer_reconciles_halts_or_creates_lock(self):
        self.module()
        await self.interrupted('lost-ack')
        (self.path / '.resume.lock').unlink()
        before = inventory(self.path)
        with ExitStack() as stack:
            for module, name in [(recovery, 'recovery_lock'), (recovery, 'reconcile_order'),
                                 (recovery, '_halt'), (recovery, '_publish')]:
                stack.enter_context(patch.object(module, name, side_effect=AssertionError('write attempted')))
            stack.enter_context(patch.object(PaperTradingSession, '__init__', side_effect=AssertionError('owner opened')))
            stack.enter_context(patch.object(_PaperAdapter, 'create_order', side_effect=AssertionError('durable order')))
            report = await self.inspect()
        self.assertEqual(report['status'], 'reconciliation_required')
        self.assertEqual(report['counts']['unconfirmed_saved_fills'], 1)
        self.assertEqual(inventory(self.path), before)
        self.assertFalse((self.path / '.resume.lock').exists())

    async def test_unknown_without_fill_is_blocked(self):
        self.module()
        await self.interrupted('no-fill')
        before = inventory(self.path)
        report = await self.inspect()
        self.assertEqual((report['status'], report['reason']), ('blocked', 'missing_fill'))
        self.assertNotIn('counts', report)
        self.assertEqual(inventory(self.path), before)

    async def test_missing_directory_is_not_created(self):
        report = await self.inspect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(self.path.exists())

    async def test_missing_manifest_is_not_manufactured(self):
        self.module()
        self.path.mkdir(mode=0o700)
        self.assertEqual((await self.inspect())['reason'], 'manifest_missing')
        self.assertEqual(list(self.path.iterdir()), [])

    async def test_completion_marker_is_not_a_completion_certificate(self):
        self.module()
        self.path.mkdir(mode=0o700)
        (self.path/'result.json').write_text('not a report')
        with patch.object(PaperRunner, 'run', side_effect=AssertionError('unneeded replay')):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'completion_present')
        self.assertIs(report['completion_validated'], False)

    async def test_completed_opt_in_run_does_not_reopen_account(self):
        self.module()
        await run_durable_replay(fixture(4), directory=self.path, resumable=True)
        before = inventory(self.path)
        self.assertEqual((await self.inspect())['reason'], 'completion_present')
        self.assertEqual(inventory(self.path), before)

    async def test_changed_engine_is_fixed_reason_and_read_only(self):
        self.module()
        await self.interrupted()
        before = inventory(self.path)
        with patch.object(recovery, '_engine_fingerprint', return_value='0'*64):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'engine_changed')
        self.assertEqual(inventory(self.path), before)

    async def test_modified_input_is_rejected(self):
        self.module()
        await self.interrupted()
        path = self.path/'input.json'
        value = json.loads(path.read_bytes()); value['candles'][-1][4] = '100'
        path.write_text(recovery._canonical(value)+'\n')
        self.assertEqual((await self.inspect())['reason'], 'input_changed')

    async def test_invalid_input_never_leaks_raw_paths(self):
        self.module()
        await self.interrupted()
        (self.path/'input.json').write_text('/private/token/credential')
        raw = json.dumps(await self.inspect())
        self.assertNotIn('/private/token', raw)
        self.assertNotIn(str(self.path), raw)

    async def test_corrupt_account_is_unavailable_not_zero(self):
        self.module()
        await self.interrupted()
        (self.path/'account.sqlite').write_bytes(b'not sqlite')
        report = await self.inspect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertNotIn('counts', report)

    async def test_foreign_account_is_blocked(self):
        self.module()
        await self.interrupted()
        other = self.root/'other.sqlite'
        async with PaperTradingSession(other, create=True, initial_balance='1000', enable_control=True):
            pass
        os.replace(other, self.path/'account.sqlite')
        self.assertEqual((await self.inspect())['reason'], 'account_changed')

    async def test_held_run_lock_does_not_block_read_or_claim_availability(self):
        self.module()
        await self.interrupted()
        with recovery.recovery_lock(self.path):
            report = await self.inspect()
        self.assertEqual(report['status'], 'compatible_snapshot')
        self.assertEqual(report['owner_state'], 'unobserved')

    async def test_held_account_owner_can_be_observed_without_reopening(self):
        self.module()
        await self.interrupted()
        async with PaperTradingSession(self.path/'account.sqlite'):
            before = inventory(self.path)
            report = await self.inspect()
            self.assertEqual(report['status'], 'compatible_snapshot')
            self.assertEqual(inventory(self.path), before)

    async def test_changed_account_during_replay_refuses_stale_counts(self):
        self.module()
        await self.interrupted()
        original = PaperRunner.run
        async def change(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            async with PaperTradingSession(self.path/'account.sqlite'):
                pass  # Owner claim advances the committed revision.
            return result
        with patch.object(PaperRunner, 'run', change):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'observation_changed')
        self.assertNotIn('counts', report)

    async def test_input_replacement_during_replay_is_refused(self):
        self.module()
        await self.interrupted()
        original = PaperRunner.run
        async def replace(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            target = self.path/'input.json'; other = self.path/'replacement'
            other.write_bytes(target.read_bytes()); other.chmod(0o600); os.replace(other, target)
            return result
        with patch.object(PaperRunner, 'run', replace):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'source_changed')

    async def test_completion_appearing_during_replay_is_refused(self):
        self.module()
        await self.interrupted()
        original = PaperRunner.run
        async def complete(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            (self.path/'result.json').write_text('{}')
            return result
        with patch.object(PaperRunner, 'run', complete):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'completion_present')

    async def test_symlinked_directory_is_refused(self):
        self.module()
        actual = self.root/'actual'; actual.mkdir(mode=0o700); self.path.symlink_to(actual)
        self.assertEqual((await self.inspect())['status'], 'blocked')
        self.assertEqual(list(actual.iterdir()), [])

    async def test_nonprivate_directory_is_refused(self):
        self.module()
        self.path.mkdir(mode=0o755)
        self.assertEqual((await self.inspect())['reason'], 'private_directory_required')

    async def test_replay_cancellation_propagates_without_mutation(self):
        self.module()
        await self.interrupted()
        before = inventory(self.path)
        with patch.object(PaperRunner, 'run', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.inspect()
        self.assertEqual(inventory(self.path), before)

    async def test_original_resume_still_succeeds_after_inspection(self):
        self.module()
        await self.interrupted('lost-ack')
        fingerprint = recovery._engine_fingerprint()
        self.assertEqual((await self.inspect())['status'], 'reconciliation_required')
        self.assertEqual(recovery._engine_fingerprint(), fingerprint)
        result = await recovery.resume_durable_replay(self.path, reconcile_saved_fills=True)
        self.assertEqual(result['recovery']['saved_fills_reconciled'], 1)
        self.assertEqual(result['replay'], await PaperRunner(self.config).run(fixture()))

    async def test_rsi_krw_matches_original_counts(self):
        self.module()
        cfg = PaperRunConfig(strategy='rsi', symbol='BTC/KRW', initial_balance='1000',
                            max_daily_loss_pct=100, max_drawdown_pct=100)
        await self.interrupted(config=cfg)
        report = await self.inspect()
        expected = await PaperRunner(cfg).run(fixture())
        self.assertEqual(report['status'], 'compatible_snapshot')
        self.assertEqual(report['counts']['expected_fills'], len(expected['trades']))

    async def test_cli_success_one_json_no_trace(self):
        self.module()
        await self.interrupted()
        before = inventory(self.path)
        result = self.cli(self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'compatible_snapshot')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(inventory(self.path), before)

    async def test_cli_reconciliation_has_distinct_exit(self):
        self.module()
        await self.interrupted('lost-ack')
        result = self.cli(self.path)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'reconciliation_required')

    async def test_cli_no_inputs_returns_json(self):
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['reason'], 'invalid_arguments')
        self.assertEqual(result.stderr, '')

    async def test_cli_missing_source_does_not_create_files(self):
        result = self.cli(self.path)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'unavailable')
        self.assertNotIn(str(self.path), result.stdout)
        self.assertFalse(self.path.exists())

    async def test_only_read_only_sqlite_connections_are_opened(self):
        self.module()
        await self.interrupted()
        original = sqlite3.connect
        calls = []
        def read_only(path, *args, **kwargs):
            self.assertIn('mode=ro', str(path))
            calls.append(str(path))
            return original(path, *args, **kwargs)
        with patch('sqlite3.connect', side_effect=read_only):
            report = await self.inspect()
        self.assertEqual(report['status'], 'compatible_snapshot')
        self.assertEqual(len(calls), 2)

    async def test_active_mode_is_preserved_not_authorized(self):
        self.module()
        await self.interrupted()
        async with PaperTradingSession(self.path/'account.sqlite') as session:
            await session.set_operating_mode('active',
                expected_generation=session.control_status['generation'], reason='operator')
            before = inventory(self.path)
            report = await self.inspect()
            self.assertEqual(report['stored_mode'], 'active')
            self.assertFalse(report['resume_authorized'])
            self.assertEqual(report['owner_state'], 'unobserved')
            self.assertEqual(inventory(self.path), before)

    async def test_directory_replacement_is_refused(self):
        self.module()
        await self.interrupted()
        original = PaperRunner.run
        async def replace(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            old = self.root/'moved'; self.path.rename(old)
            shutil.copytree(old, self.path)
            return result
        with patch.object(PaperRunner, 'run', replace):
            report = await self.inspect()
        self.assertEqual(report['reason'], 'source_changed')
        self.assertNotIn('counts', report)

    async def test_account_inode_replacement_is_not_a_compatible_read(self):
        self.module()
        await self.interrupted()
        original = PaperRunner.run
        async def replace(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            account = self.path/'account.sqlite'; temp = self.path/'new.sqlite'
            temp.write_bytes(account.read_bytes()); temp.chmod(0o600); os.replace(temp, account)
            return result
        with patch.object(PaperRunner, 'run', replace):
            report = await self.inspect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertNotIn('counts', report)

    async def test_empty_saved_prefix_is_not_an_unknown_order(self):
        self.module()
        with patch.object(PaperRunner, '_run', side_effect=RuntimeError('before strategy')):
            with self.assertRaises(RuntimeError):
                await run_durable_replay(fixture(), config=self.config,
                                        directory=self.path, resumable=True)
        report = await self.inspect()
        self.assertEqual(report['status'], 'compatible_snapshot')
        self.assertEqual(report['counts']['saved_fills'], 0)
        self.assertEqual(report['counts']['remaining_fills'], report['counts']['expected_fills'])

    async def test_cli_has_no_reconcile_or_execution_switch(self):
        for option in ('--reconcile-saved-fills', '--live', '--force'):
            result = self.cli(self.path, option)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['reason'], 'invalid_arguments')
            self.assertEqual(result.stderr, '')
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
