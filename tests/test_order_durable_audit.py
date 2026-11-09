"""Completed-run audit with original strategies, real SQLite and immutable inputs."""
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
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))
from trading_bot import durable_paper_resume as recovery
from trading_bot.durable_paper_run import run_durable_replay
from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.order_state_store import _canonical, _digest
from trading_core.paper_session import PaperTradingSession, _PaperAdapter
from trading_core.paper_session_observer import PaperSessionObserver


def fixture(n=80):
    values = [100 + (i % 20 if (i // 20) % 2 == 0 else 20-i % 20) for i in range(n)]
    return [[1700000000000+i*3600000, str(x), str(x+2), str(x-2),
             str(Decimal(x)+Decimal('.5')), '1'] for i, x in enumerate(values)]


def inventory(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode,
            p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'run'
        self.config = PaperRunConfig(initial_balance='1000', max_daily_loss_pct=100,
                                    max_drawdown_pct=100)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def module(self):
        try:
            mod = importlib.import_module('trading_bot.durable_paper_audit')
        except ModuleNotFoundError:
            mod = None
        self.assertIsNotNone(mod, 'missing completed-run audit')
        return mod

    async def create(self, *, bars=None, config=None, resumable=True):
        self.module()
        return await run_durable_replay(fixture() if bars is None else bars,
            config=config or self.config, directory=self.path, resumable=resumable)

    async def audit(self):
        return await self.module().audit_durable_replay(self.path)

    def read(self, name='result.json'):
        return json.loads((self.path / name).read_bytes())

    def write(self, data, name='result.json'):
        (self.path / name).write_text(_canonical(data)+'\n')

    def cli(self, *args):
        script = ROOT / 'tools/paper_audit.py'
        self.assertTrue(script.is_file(), 'missing completed-run audit CLI')
        return subprocess.run([sys.executable, '-B', str(script), *map(str, args)],
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    def assert_blocked(self, report, reason=None):
        self.assertIn(report['status'], ('blocked', 'unavailable'))
        self.assertFalse(report['completion_validated'])
        self.assertFalse(report['resume_authorized'])
        self.assertNotIn('counts', report)
        if reason is not None:
            self.assertEqual(report['reason'], reason)

    async def test_completed_report_matches_current_account_without_changes(self):
        result = await self.create()
        before = inventory(self.path)
        report = await self.audit()
        self.assertEqual(report['status'], 'verified_completion')
        self.assertTrue(report['completion_validated'])
        self.assertFalse(report['resume_authorized'])
        self.assertFalse(report['live_enabled'])
        self.assertEqual(report['owner_state'], 'unobserved')
        self.assertEqual(report['counts']['fills'], len(result['replay']['trades']))
        self.assertEqual(report['snapshot'], result['comparison']['after']['snapshot'])
        self.assertEqual(inventory(self.path), before)

    async def test_original_rsi_krw_and_usdt_reproduction(self):
        self.module()
        for strategy, currency in [('rsi', 'KRW'), ('rsi', 'USDT'), ('ma_cross', 'KRW')]:
            self.path = self.root / (strategy+currency)
            config = PaperRunConfig(strategy=strategy, symbol='BTC/'+currency,
                initial_balance='1000', max_daily_loss_pct=100, max_drawdown_pct=100)
            await self.create(config=config)
            self.assertEqual((await self.audit())['status'], 'verified_completion')

    async def test_no_trade_completed_result_is_valid(self):
        result = await self.create(bars=fixture(12))
        report = await self.audit()
        self.assertEqual(report['status'], 'verified_completion')
        self.assertEqual(report['counts']['fills'], 0)
        self.assertEqual(result['replay']['trades'], [])

    async def test_resumed_saved_fill_completion_is_supported(self):
        self.module()
        original = _PaperAdapter.create_order
        async def lose(adapter, *args, **kwargs):
            await original(adapter, *args, **kwargs)
            raise TimeoutError('private-provider-message')
        with patch.object(_PaperAdapter, 'create_order', lose):
            with self.assertRaises(Exception):
                await run_durable_replay(fixture(), config=self.config,
                                        directory=self.path, resumable=True)
        result = await recovery.resume_durable_replay(self.path, reconcile_saved_fills=True)
        before = inventory(self.path)
        report = await self.audit()
        self.assertEqual(report['status'], 'verified_completion')
        self.assertEqual(report['final_evidence'], result['final_evidence'])
        self.assertFalse(report['checks']['recovery_counters_proven'])
        self.assertEqual(inventory(self.path), before)

    async def test_unmarked_run_is_not_silently_upgraded(self):
        await self.create(resumable=False)
        self.assert_blocked(await self.audit(), 'manifest_missing')

    async def test_missing_result_is_not_completed(self):
        await self.create()
        (self.path/'result.json').unlink()
        self.assert_blocked(await self.audit(), 'completion_missing')

    async def test_empty_completion_marker_is_not_proof(self):
        await self.create()
        (self.path/'result.json').write_bytes(b'')
        self.assert_blocked(await self.audit())

    async def test_shallow_completion_marker_is_not_proof(self):
        await self.create()
        self.write({'status': 'completed_halted'})
        self.assert_blocked(await self.audit(), 'invalid_result')

    async def test_forged_return_is_rejected(self):
        await self.create()
        value = self.read(); value['replay']['total_return_pct'] = '999999'; self.write(value)
        self.assert_blocked(await self.audit(), 'replay_mismatch')

    async def test_forged_risk_status_is_rejected(self):
        await self.create()
        value = self.read(); value['replay']['drawdown_halted'] = True; self.write(value)
        self.assert_blocked(await self.audit(), 'replay_mismatch')

    async def test_bool_is_not_an_integer_in_report(self):
        await self.create(bars=fixture(1))
        value = self.read(); value['replay']['bars_processed'] = True; self.write(value)
        self.assert_blocked(await self.audit(), 'replay_mismatch')

    async def test_forged_comparison_cash_delta_is_rejected(self):
        await self.create()
        value = self.read(); value['comparison']['cash']['delta'] = '50'; self.write(value)
        self.assert_blocked(await self.audit(), 'comparison_mismatch')

    async def test_comparison_timestamp_must_be_real_utc(self):
        await self.create()
        value = self.read(); value['comparison']['compared_at'] = '2026-02-31T00:00:00+00:00'; self.write(value)
        self.assert_blocked(await self.audit(), 'invalid_result')

    async def test_input_hash_must_match_completion(self):
        await self.create()
        value = self.read(); value['input_sha256'] = '0'*64; self.write(value)
        self.assert_blocked(await self.audit(), 'result_identity_mismatch')

    async def test_engine_fingerprint_changed_is_rejected(self):
        await self.create()
        with patch.object(recovery, '_engine_fingerprint', return_value='0'*64):
            self.assert_blocked(await self.audit(), 'engine_changed')

    async def test_final_evidence_path_cannot_escape_directory(self):
        await self.create()
        value = self.read()
        value.update(final_evidence='../secret.paper-evidence.json', recovery={
            'method':'deterministic_prefix_replay', 'existing_fills_skipped':0,
            'saved_fills_reconciled':0})
        self.write(value)
        self.assert_blocked(await self.audit(), 'invalid_result')

    async def test_missing_final_evidence_is_not_completed(self):
        await self.create()
        (self.path/'after.paper-evidence.json').unlink()
        self.assert_blocked(await self.audit())

    async def test_corrupt_evidence_is_not_certified_by_comparison(self):
        await self.create()
        value = self.read('after.paper-evidence.json')
        value['payload']['book']['balances']['USDT']='9000'
        self.write(value, 'after.paper-evidence.json')
        self.assert_blocked(await self.audit())

    async def test_current_db_changed_after_genuine_completion_is_rejected(self):
        await self.create()
        async with PaperTradingSession(self.path/'account.sqlite') as session:
            session.exchange.set_price('BTC/USDT','999',timestamp=1900000000000)
        before = inventory(self.path)
        self.assert_blocked(await self.audit(), 'account_changed_after_completion')
        self.assertEqual(inventory(self.path), before)

    async def test_current_db_missing_is_unavailable_not_offline_success(self):
        await self.create()
        (self.path/'account.sqlite').unlink()
        self.assert_blocked(await self.audit(), 'account_unavailable')

    async def test_result_false_live_flag_and_schema_type(self):
        await self.create()
        original = self.read()
        for key, value in [('live_enabled', True), ('schema_version', True),
                           ('source', 'other'), ('status', 'running')]:
            data=dict(original); data[key]=value; self.write(data)
            self.assert_blocked(await self.audit(), 'invalid_result')

    async def test_result_extra_unverified_fields_are_rejected(self):
        await self.create()
        value=self.read();value['live_authorization']=True;self.write(value)
        self.assert_blocked(await self.audit(), 'invalid_result')

    async def test_original_pending_signal_is_not_ignored(self):
        await self.create()
        value=self.read();value['replay']['pending_signal']={'action':'sell','source_index':79};self.write(value)
        self.assert_blocked(await self.audit(), 'replay_mismatch')

    async def test_never_opens_writer_or_creates_a_lock(self):
        await self.create()
        (self.path/'.resume.lock').unlink()
        before=inventory(self.path)
        with ExitStack() as stack:
            for module, name in [(recovery,'recovery_lock'), (recovery,'reconcile_order'),
                                 (recovery,'_halt'), (recovery,'_publish')]:
                stack.enter_context(patch.object(module,name,side_effect=AssertionError('write attempted')))
            stack.enter_context(patch.object(PaperTradingSession,'__init__',side_effect=AssertionError('writer opened')))
            stack.enter_context(patch.object(_PaperAdapter,'create_order',side_effect=AssertionError('order submitted')))
            report=await self.audit()
        self.assertEqual(report['status'],'verified_completion')
        self.assertEqual(inventory(self.path),before)

    async def test_run_lock_can_be_held_without_authorizing_execution(self):
        await self.create()
        with recovery.recovery_lock(self.path):
            report=await self.audit()
        self.assertEqual(report['status'],'verified_completion')
        self.assertEqual(report['owner_state'],'unobserved')

    async def test_observer_uses_only_sqlite_readonly_mode(self):
        await self.create()
        original=sqlite3.connect; connections=[]
        def readonly(database,*args,**kwargs):
            self.assertIn('mode=ro',str(database));connections.append(database)
            return original(database,*args,**kwargs)
        with patch.object(sqlite3,'connect',readonly):
            report=await self.audit()
        self.assertEqual(report['status'],'verified_completion')
        self.assertGreaterEqual(len(connections),2)

    async def test_result_replaced_during_replay_is_rejected(self):
        await self.create()
        original=PaperRunner.run
        async def change(runner,*args,**kwargs):
            result=await original(runner,*args,**kwargs)
            replacement=self.path/'replacement';replacement.write_bytes((self.path/'result.json').read_bytes())
            replacement.chmod(0o600);os.replace(replacement,self.path/'result.json')
            return result
        with patch.object(PaperRunner,'run',change):
            self.assert_blocked(await self.audit(), 'source_changed')

    async def test_account_replaced_during_replay_is_rejected(self):
        await self.create()
        original=PaperRunner.run
        async def change(runner,*args,**kwargs):
            result=await original(runner,*args,**kwargs)
            replacement=self.path/'replacement';replacement.write_bytes((self.path/'account.sqlite').read_bytes())
            replacement.chmod(0o600);os.replace(replacement,self.path/'account.sqlite')
            return result
        with patch.object(PaperRunner,'run',change):
            self.assert_blocked(await self.audit())

    async def test_changed_account_revision_during_replay_is_rejected(self):
        await self.create()
        original=PaperRunner.run
        async def change(runner,*args,**kwargs):
            result=await original(runner,*args,**kwargs)
            with sqlite3.connect(self.path/'account.sqlite') as db:
                db.execute('UPDATE paper_session SET revision=revision+1')
            return result
        with patch.object(PaperRunner,'run',change):
            self.assert_blocked(await self.audit())

    async def test_private_regular_files_only(self):
        await self.create()
        target=self.path/'result.json';raw=target.read_bytes()
        target.chmod(0o644);self.assert_blocked(await self.audit());target.chmod(0o600)
        target.unlink();other=self.path/'other';other.write_bytes(raw);other.chmod(0o600)
        target.symlink_to(other);self.assert_blocked(await self.audit());target.unlink()
        os.link(other,target);self.assert_blocked(await self.audit())

    async def test_cancellation_does_not_turn_into_validation_success(self):
        await self.create()
        async def cancel(*args,**kwargs):
            raise asyncio.CancelledError()
        before=inventory(self.path)
        with patch.object(PaperRunner,'run',cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.audit()
        self.assertEqual(inventory(self.path),before)

    async def test_cli_success_one_json(self):
        await self.create()
        before=inventory(self.path)
        result=self.cli(self.path)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'verified_completion')
        self.assertEqual(inventory(self.path),before)

    async def test_cli_missing_and_forbidden_flags_have_fixed_json(self):
        self.module()
        for args in [(),(self.path,), (self.path,'--live'), (self.path,'--force'),
                     (self.path,'--reconcile-saved-fills')]:
            result=self.cli(*args)
            self.assertEqual(result.returncode,2)
            data=json.loads(result.stdout)
            self.assertFalse(data['completion_validated'])
            self.assertNotIn(str(self.root),result.stdout)
            self.assertNotIn('Traceback',result.stderr)

    async def test_valid_json_with_duplicate_keys_is_rejected(self):
        await self.create()
        p=self.path/'result.json';p.write_text('{"status":"completed_halted","status":"completed_halted"}\n')
        self.assert_blocked(await self.audit())

    async def test_result_file_limit_is_bounded(self):
        await self.create()
        with (self.path/'result.json').open('wb') as stream:
            stream.truncate(20*1024*1024+1)
        self.assert_blocked(await self.audit())

    async def test_directory_replacement_during_replay_is_rejected(self):
        await self.create()
        original=PaperRunner.run
        async def change(runner,*args,**kwargs):
            result=await original(runner,*args,**kwargs)
            old=self.root/'old';self.path.rename(old);shutil.copytree(old,self.path)
            return result
        with patch.object(PaperRunner,'run',change):
            self.assert_blocked(await self.audit(), 'source_changed')

    async def test_result_changed_during_last_fingerprint_check_is_rejected(self):
        await self.create()
        original = recovery._engine_fingerprint
        calls = 0
        def change():
            nonlocal calls
            calls += 1
            result = original()
            if calls == 3:
                value = self.read()
                value['replay']['cash'] = '9000000'
                self.write(value)
            return result
        with patch.object(recovery, '_engine_fingerprint', change):
            self.assert_blocked(await self.audit(), 'source_changed')
        self.assertEqual(calls, 3)

    async def test_result_recovery_counters_are_not_claimed_as_proven(self):
        await self.create()
        value = self.read()
        name = 'resume-' + '1'*32 + '.paper-evidence.json'
        shutil.copyfile(self.path/'after.paper-evidence.json', self.path/name)
        (self.path/name).chmod(0o600)
        value.update(final_evidence=name, recovery=dict(method='deterministic_prefix_replay',
            existing_fills_skipped=0, saved_fills_reconciled=0))
        self.write(value)
        report = await self.audit()
        self.assertEqual(report['status'], 'verified_completion')
        self.assertFalse(report['checks']['recovery_counters_proven'])
        self.assertNotIn('recovery', report)
        value['recovery']['existing_fills_skipped'] = 9999
        self.write(value)
        self.assert_blocked(await self.audit(), 'invalid_result')

    def test_cli_cancellation_and_import_failure_keep_json(self):
        self.module()
        import importlib.util
        from contextlib import redirect_stdout
        import io
        spec = importlib.util.spec_from_file_location('audit_cli_test', ROOT/'tools/paper_audit.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        async def cancel(*args):
            raise asyncio.CancelledError()
        out = io.StringIO()
        with patch.object(self.module(), 'audit_durable_replay', cancel), redirect_stdout(out):
            code = cli.main([str(self.path)])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(out.getvalue())['reason'], 'audit_interrupted')
        import builtins
        original = builtins.__import__
        def blocked(name, *args, **kwargs):
            if name == 'trading_bot.durable_paper_audit':
                raise ImportError('private-support-detail')
            return original(name, *args, **kwargs)
        out = io.StringIO()
        with patch('builtins.__import__', blocked), redirect_stdout(out):
            code = cli.main([str(self.path)])
        self.assertEqual(code, 2)
        self.assertNotIn('private-support-detail', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['reason'], 'audit_failed')


if __name__ == '__main__':
    unittest.main()
