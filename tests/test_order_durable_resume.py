"""Explicit historical recovery: real sessions, original engine, no live backend."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D
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
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from trading_bot.durable_paper_run import run_durable_replay
from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.paper_session import PaperTradingSession, _PaperAdapter
from trading_core.paper_session_observer import PaperSessionObserver
from trading_core.paper_evidence import verify_evidence


def fixture(n=100):
    values = [100 + (i % 20 if (i // 20) % 2 == 0 else 20-i % 20) for i in range(n)]
    return [[1700000000000+i*3600000,str(x),str(x+2),str(x-2),str(D(x)+D('.5')),'1'] for i,x in enumerate(values)]


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/'run'
        self.config = PaperRunConfig(initial_balance=D('1000'),max_daily_loss_pct=100,max_drawdown_pct=100)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def module(self):
        try: mod=importlib.import_module('trading_bot.durable_paper_resume')
        except ModuleNotFoundError: mod=None
        self.assertIsNotNone(mod,'historical recovery module missing')
        return mod

    async def interrupted(self, kind='confirmed', *, config=None):
        self.module()
        config=config or self.config
        if kind=='no-fill':
            async def lose(*a,**kw): raise TimeoutError('private')
            with patch.object(_PaperAdapter,'create_order',lose):
                with self.assertRaises(Exception):
                    await run_durable_replay(fixture(),config=config,directory=self.path,resumable=True)
        elif kind=='lost-ack':
            original=_PaperAdapter.create_order
            async def lose(adapter,*a,**kw):
                await original(adapter,*a,**kw)
                raise TimeoutError('private')
            with patch.object(_PaperAdapter,'create_order',lose):
                with self.assertRaises(Exception):
                    await run_durable_replay(fixture(),config=config,directory=self.path,resumable=True)
        else:
            original=PaperRunner._execute_pending
            async def after(runner,exchange,manager,pending,state):
                await original(runner,exchange,manager,pending,state)
                if exchange.snapshot()['trades']: raise RuntimeError('interrupted')
            with patch.object(PaperRunner,'_execute_pending',after):
                with self.assertRaisesRegex(RuntimeError,'interrupted'):
                    await run_durable_replay(fixture(),config=config,directory=self.path,resumable=True)
        self.assertFalse((self.path/'result.json').exists())

    async def resume(self, **kwargs):
        return await self.module().resume_durable_replay(self.path, **kwargs)

    async def test_read_access_time_is_not_a_content_change(self):
        mod=self.module(); target=self.root/'meta.json'
        target.write_bytes(b'{"a":1}\n');target.chmod(0o600)
        info=target.stat();os.utime(target,ns=(0,info.st_mtime_ns))
        self.assertEqual(mod._read_private(target)[1],{'a':1})

    async def test_default_v31_does_not_opt_in(self):
        await run_durable_replay(fixture(4),config=self.config,directory=self.path)
        self.assertFalse((self.path/'resume.json').exists())

    async def test_explicit_manifest_precedes_first_account_order(self):
        self.module()
        original=_PaperAdapter.create_order
        async def check(adapter,*a,**kw):
            self.assertTrue((self.path/'resume.json').is_file())
            return await original(adapter,*a,**kw)
        with patch.object(_PaperAdapter,'create_order',check):
            r=await run_durable_replay(fixture(),config=self.config,directory=self.path,resumable=True)
        self.assertTrue(r['boundaries']['resume_supported'])
        self.assertEqual(json.loads((self.path/'resume.json').read_text())['account_id'],r['account_id'])

    async def test_confirmed_prefix_never_submits_again(self):
        await self.interrupted()
        before=PaperSessionObserver(self.path/'account.sqlite').read_evidence()
        old=deepcopy(before['payload']['book']['trades']); calls=[]
        original=_PaperAdapter.create_order
        async def track(adapter,*a,**kw):
            calls.append(kw.get('client_order_id'))
            return await original(adapter,*a,**kw)
        with patch.object(_PaperAdapter,'create_order',track): result=await self.resume()
        self.assertEqual(result['replay'],await PaperRunner(self.config).run(fixture()))
        self.assertEqual(result['replay']['trades'][:len(old)],old)
        self.assertEqual(len(calls),len(result['replay']['trades'])-len(old))
        self.assertEqual(result['recovery']['existing_fills_skipped'],len(old))
        self.assertEqual(PaperSessionObserver(self.path/'account.sqlite').read()['control']['mode'],'halted')

    async def test_lost_ack_requires_explicit_reconciliation(self):
        await self.interrupted('lost-ack')
        raw=(self.path/'account.sqlite').read_bytes()
        with self.assertRaisesRegex(Exception,'reconciliation_required'):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)
        result=await self.resume(reconcile_saved_fills=True)
        self.assertEqual(result['recovery']['saved_fills_reconciled'],1)
        self.assertEqual(result['replay'],await PaperRunner(self.config).run(fixture()))

    async def test_unknown_without_fill_never_retried_even_with_opt_in(self):
        await self.interrupted('no-fill')
        raw=(self.path/'account.sqlite').read_bytes()
        with patch.object(_PaperAdapter,'create_order',side_effect=AssertionError('resubmission')):
            with self.assertRaisesRegex(Exception,'missing_fill'):await self.resume(reconcile_saved_fills=True)
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_input_change_is_rejected_without_account_write(self):
        await self.interrupted()
        raw=(self.path/'account.sqlite').read_bytes()
        f=self.path/'input.json'; data=json.loads(f.read_text());data['candles'][-1][4]='100';f.write_text(json.dumps(data))
        with self.assertRaises(Exception):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_engine_fingerprint_change_is_refused(self):
        await self.interrupted()
        with patch.object(self.module(),'_engine_fingerprint',return_value='0'*64):
            with self.assertRaisesRegex(Exception,'engine_changed'):await self.resume()

    async def test_completed_directory_is_not_reopened(self):
        self.module()
        await run_durable_replay(fixture(4),config=self.config,directory=self.path,resumable=True)
        raw=(self.path/'account.sqlite').read_bytes()
        with patch.object(PaperTradingSession,'__init__',side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(Exception,'already_complete'):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_unmarked_legacy_interruption_is_refused(self):
        self.module()
        self.path.mkdir(mode=0o700)
        with self.assertRaises(Exception):await self.resume()
        self.assertFalse((self.path/'account.sqlite').exists())

    async def test_existing_account_owner_prevents_resume(self):
        await self.interrupted()
        async with PaperTradingSession(self.path/'account.sqlite'):
            with self.assertRaises(Exception):await self.resume()
        self.assertFalse((self.path/'result.json').exists())

    async def test_run_lock_prevents_concurrent_resume(self):
        await self.interrupted()
        with self.module().recovery_lock(self.path):
            with self.assertRaises(Exception):await self.resume()

    async def test_changed_before_evidence_is_refused(self):
        await self.interrupted()
        f=self.path/'before.paper-evidence.json';f.write_bytes(f.read_bytes()+b' ')
        with self.assertRaises(Exception):await self.resume()

    async def test_foreign_valid_account_is_refused(self):
        await self.interrupted()
        foreign=self.root/'other.sqlite'
        async with PaperTradingSession(foreign,create=True,initial_balance='1000',enable_control=True):pass
        os.replace(foreign,self.path/'account.sqlite')
        raw=(self.path/'account.sqlite').read_bytes()
        with self.assertRaisesRegex(Exception,'account_changed'):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_symlinked_input_is_refused(self):
        await self.interrupted(); f=self.path/'input.json'; other=self.root/'saved.json';f.rename(other);f.symlink_to(other)
        with self.assertRaises(Exception):await self.resume()

    async def test_cancellation_keeps_account_halted(self):
        await self.interrupted()
        original=PaperRunner._run
        async def cancel(runner,*a,**kw):
            if kw.get('_session') is not None:raise asyncio.CancelledError()
            return await original(runner,*a,**kw)
        with patch.object(PaperRunner,'_run',cancel):
            with self.assertRaises(asyncio.CancelledError):await self.resume()
        self.assertEqual(PaperSessionObserver(self.path/'account.sqlite').read()['control']['mode'],'halted')
        self.assertFalse((self.path/'result.json').exists())

    async def test_rsi_krw_and_usdt_parity(self):
        self.module()
        for i,(strategy,currency) in enumerate([('rsi','USDT'),('rsi','KRW'),('ma_cross','KRW')]):
            self.path=self.root/f'case-{i}'
            config=PaperRunConfig(strategy=strategy,symbol='BTC/'+currency,initial_balance=D('1000'),max_daily_loss_pct=100,max_drawdown_pct=100)
            await self.interrupted(config=config)
            result=await self.resume()
            self.assertEqual(result['replay'],await PaperRunner(config).run(fixture()))

    async def test_failure_after_all_fills_can_finish_without_new_orders(self):
        self.module()
        from trading_bot import durable_paper_run as module
        original=module._publish
        def fail(raw,path):
            if Path(path).name=='result.json':raise OSError('publication failed')
            return original(raw,path)
        with patch.object(module,'_publish',fail):
            with self.assertRaises(OSError):await run_durable_replay(fixture(),config=self.config,directory=self.path,resumable=True)
        after=(self.path/'after.paper-evidence.json').read_bytes()
        with patch.object(_PaperAdapter,'create_order',side_effect=AssertionError('duplicate')): result=await self.resume()
        self.assertEqual((self.path/'after.paper-evidence.json').read_bytes(),after)
        self.assertEqual(result['replay'],await PaperRunner(self.config).run(fixture()))

    async def test_failed_resume_halt_never_reports_complete(self):
        await self.interrupted()
        original=PaperTradingSession.set_operating_mode;calls=[]
        async def halt_fail(session,mode,**kw):
            if mode=='halted':
                calls.append(mode)
                if len(calls)>1:raise OSError('private storage error')
            return await original(session,mode,**kw)
        with patch.object(PaperTradingSession,'set_operating_mode',halt_fail):
            with self.assertRaisesRegex(Exception,'stop_unconfirmed'):await self.resume()
        self.assertFalse((self.path/'result.json').exists())

    async def test_different_valid_trade_history_cannot_be_used(self):
        await self.interrupted()
        async with PaperTradingSession(self.path/'account.sqlite') as session:
            await session.set_operating_mode('active',expected_generation=session.control_status['generation'],reason='operator')
            from trading_core.order_manager import OrderSide,OrderType
            row=fixture()[-1]
            session.exchange.set_price('BTC/USDT',row[4],timestamp=row[0])
            await session.manager.place_order('BTC/USDT',OrderSide.SELL,OrderType.MARKET,D('.1'),client_order_id='foreign-decision')
            await session.set_operating_mode('halted',expected_generation=session.control_status['generation'],reason='operator')
        raw=(self.path/'account.sqlite').read_bytes()
        with self.assertRaisesRegex(Exception,'fill_history_changed'):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_original_risk_gates_and_pending_signal_survive_rebuild(self):
        config=PaperRunConfig(initial_balance=D('1000'),max_daily_loss_pct=D('.1'),max_drawdown_pct=D('.1'))
        await self.interrupted(config=config)
        r=await self.resume()
        self.assertEqual(r['replay'],await PaperRunner(config).run(fixture()))

    async def test_input_replaced_during_preflight_never_places_order(self):
        await self.interrupted()
        original=PaperRunner.run
        async def replace(runner,*a,**kw):
            result=await original(runner,*a,**kw)
            f=self.path/'input.json'; f.write_bytes(f.read_bytes()+b' ')
            return result
        raw=(self.path/'account.sqlite').read_bytes()
        with patch.object(PaperRunner,'run',replace):
            with self.assertRaises(Exception):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_foreign_marks_are_refused_before_any_new_fill(self):
        await self.interrupted()
        async with PaperTradingSession(self.path/'account.sqlite') as session:
            session.exchange.set_price('ETH/USDT','200',timestamp=1700000000000)
        raw=(self.path/'account.sqlite').read_bytes()
        with self.assertRaises(Exception):await self.resume()
        self.assertEqual((self.path/'account.sqlite').read_bytes(),raw)

    async def test_directory_replacement_during_preflight_is_refused(self):
        await self.interrupted()
        original=PaperRunner.run
        async def replace(runner,*a,**kw):
            result=await original(runner,*a,**kw)
            import shutil
            old=self.root/'old';self.path.rename(old);shutil.copytree(old,self.path)
            return result
        with patch.object(PaperRunner,'run',replace):
            with self.assertRaises(Exception):await self.resume()
        self.assertFalse((self.path/'result.json').exists())

    async def test_public_directory_is_refused_before_account_use(self):
        await self.interrupted();self.path.chmod(0o755)
        with self.assertRaisesRegex(Exception,'private_directory'):await self.resume()
        self.path.chmod(0o700)

    async def test_unchanged_completed_input_no_fills_can_resume(self):
        self.module()
        from trading_bot import durable_paper_run as module
        original=module._publish
        def fail(raw,path):
            if Path(path).name=='result.json':raise OSError('publication failed')
            return original(raw,path)
        with patch.object(module,'_publish',fail):
            with self.assertRaises(OSError):await run_durable_replay(fixture(4),config=self.config,directory=self.path,resumable=True)
        r=await self.resume();self.assertEqual(r['replay']['trades'],[])



class ResumeCLITests(unittest.TestCase):
    def test_cli_missing_run_is_fixed_json_and_does_not_create_db(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'missing'
            p=subprocess.run([sys.executable,str(ROOT/'tools/paper_resume.py'),str(target)],capture_output=True,text=True)
            self.assertEqual(p.returncode,2,p.stderr)
            self.assertEqual(json.loads(p.stdout),{'error':'durable_paper_resume_failed_check_directory'})
            self.assertFalse(target.exists())

class ResumeProcessTests(unittest.TestCase):
    def child(self, target, stage):
        code=r"""
import asyncio,os,sys
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1])
for p in ('packages/core','packages/data','packages/strategy','apps/server','tests'):
    sys.path.insert(0,str(root/p))
from test_order_durable_resume import fixture
from trading_bot.durable_paper_run import run_durable_replay
from trading_bot.paper_runner import PaperRunConfig,PaperRunner
from trading_core.paper_session import _PaperAdapter
stage=sys.argv[3]
if stage=='before-strategy':
    def exit_now(*a,**kw):os._exit(42)
    PaperRunner._strategy=exit_now
elif stage in ('before-effect','after-effect'):
    original=_PaperAdapter.create_order
    async def exit_now(*a,**kw):
        if stage=='before-effect':os._exit(42)
        await original(*a,**kw)
        os._exit(42)
    _PaperAdapter.create_order=exit_now
else:
    original=PaperRunner._execute_pending
    async def exit_now(self,exchange,*a,**kw):
        await original(self,exchange,*a,**kw)
        if exchange.snapshot()['trades']:os._exit(42)
    PaperRunner._execute_pending=exit_now
asyncio.run(run_durable_replay(fixture(),config=PaperRunConfig(initial_balance='1000',max_daily_loss_pct=100,max_drawdown_pct=100),directory=sys.argv[2],resumable=True))
"""
        return subprocess.run([sys.executable,'-c',code,str(ROOT),str(target),stage],capture_output=True,text=True,timeout=30)

    def check_stage(self,stage,allow=False):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'run'; child=self.child(path,stage)
            self.assertEqual(child.returncode,42,child.stderr)
            observed=PaperSessionObserver(path/'account.sqlite').read()
            self.assertEqual(observed['control']['mode'],'active')
            self.assertFalse((path/'result.json').exists())
            cmd=[sys.executable,str(ROOT/'tools/paper_resume.py'),str(path)]
            if allow:cmd.append('--reconcile-saved-fills')
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
            if stage=='before-effect':
                self.assertEqual(result.returncode,4,result.stderr)
                self.assertEqual(json.loads(result.stdout)['error'],'durable_resume_missing_fill')
                self.assertEqual(PaperSessionObserver(path/'account.sqlite').read()['orders']['account_fills'],0)
                return
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            output=json.loads(result.stdout)
            expected=asyncio.run(PaperRunner(PaperRunConfig(initial_balance='1000',max_daily_loss_pct=100,max_drawdown_pct=100)).run(fixture()))
            self.assertEqual(output['replay'],expected)
            self.assertEqual(output['recovery']['saved_fills_reconciled'],1 if stage=='after-effect' else 0)
            self.assertEqual(PaperSessionObserver(path/'account.sqlite').read()['control']['mode'],'halted')
            raw=(path/'account.sqlite').read_bytes()
            repeated=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
            self.assertEqual(repeated.returncode,2)
            self.assertEqual((path/'account.sqlite').read_bytes(),raw)

    def test_process_death_before_strategy_can_resume(self):self.check_stage('before-strategy')
    def test_process_death_before_effect_is_not_resubmitted(self):self.check_stage('before-effect',True)
    def test_process_death_after_effect_requires_explicit_saved_fill_recovery(self):self.check_stage('after-effect',True)
    def test_process_death_after_confirmed_order_skips_original_fill(self):self.check_stage('confirmed')


if __name__=='__main__':unittest.main()
