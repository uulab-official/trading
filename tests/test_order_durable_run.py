"""Original strategy execution against real durable local paper accounts."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D, localcontext
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
for sub in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / sub))
from trading_bot.paper_runner import PaperRunner, PaperRunConfig
from trading_core.paper_session import PaperTradingSession, _PaperAdapter
from trading_core.paper_session_observer import PaperSessionObserver
from trading_core.paper_evidence import verify_evidence
from trading_core.order_manager import OrderOutcomeUnknown


def fixture(n=100):
    values = [D(100 + (i % 20 if (i // 20) % 2 == 0 else 20 - i % 20)) for i in range(n)]
    return [[1700000000000 + i * 3600000, str(x), str(x + 2), str(x - 2), str(x + D('.5')), '1'] for i, x in enumerate(values)]


def engine():
    try:
        return importlib.import_module('trading_bot.durable_paper_run')
    except ModuleNotFoundError:
        return None


class DurableRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = PaperRunConfig(initial_balance=D('1000'), max_daily_loss_pct=100,
                                     max_drawdown_pct=100)
        self.network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.tmp.cleanup()

    async def run_case(self, bars=None, config=None, name='run'):
        mod = engine()
        self.assertIsNotNone(mod, 'durable paper runner is missing')
        return await mod.run_durable_replay(fixture() if bars is None else bars,
            config=config or self.config, directory=self.root / name)

    async def test_original_ma_economic_results_match_persisted_session(self):
        expected = await PaperRunner(self.config).run(fixture())
        report = await self.run_case()
        self.assertEqual(report['replay'], expected)
        self.assertGreater(len(expected['trades']), 0)
        observed = PaperSessionObserver(self.root/'run/account.sqlite').read()
        self.assertEqual(observed['account']['cash'], expected['cash'])
        self.assertEqual(observed['account']['total_fees'], expected['total_fees'])
        self.assertEqual(observed['orders']['account_fills'], len(expected['trades']))
        self.assertEqual(observed['control']['mode'], 'halted')
        self.assertEqual(observed['orders']['unresolved'], 0)

    async def test_rsi_and_krw_use_original_engine(self):
        for strategy, currency in [('rsi','USDT'),('ma_cross','KRW'),('rsi','KRW')]:
            config = PaperRunConfig(strategy=strategy,symbol='BTC/'+currency,
                initial_balance=D('1000'),max_daily_loss_pct=100,max_drawdown_pct=100)
            actual = await self.run_case(config=config, name=strategy+currency)
            self.assertEqual(actual['replay'], await PaperRunner(config).run(fixture()))

    async def test_before_after_evidence_and_report_reconcile(self):
        report = await self.run_case()
        folder=self.root/'run'
        before=verify_evidence(folder/'before.paper-evidence.json')
        after=verify_evidence(folder/'after.paper-evidence.json')
        self.assertEqual(before['orders']['account_fills'],0)
        self.assertEqual(before['control']['mode'],'halted')
        self.assertEqual(after['control']['mode'],'halted')
        self.assertEqual(report['comparison']['new_fills']['count'],len(report['replay']['trades']))
        self.assertEqual(json.loads((folder/'result.json').read_text()), report)
        self.assertFalse(report['live_enabled'])

    async def test_no_trade_run_is_valid_without_fabricated_fills(self):
        report=await self.run_case(fixture(4))
        self.assertEqual(report['replay']['trades'],[])
        self.assertEqual(report['comparison']['new_fills']['count'],0)

    async def test_invalid_late_candle_creates_nothing(self):
        bars=fixture(); bars[-1][4]='-1'
        with self.assertRaises(ValueError): await self.run_case(bars)
        self.assertFalse((self.root/'run').exists())

    async def test_empty_overlimit_and_overlapping_inputs_create_nothing(self):
        for bars in ([], fixture(5001), [fixture(2)[0]]*2):
            with self.subTest(size=len(bars)):
                with self.assertRaises(ValueError): await self.run_case(bars)
                self.assertFalse((self.root/'run').exists())

    async def test_existing_directory_and_account_are_never_reused(self):
        await self.run_case(); db=self.root/'run/account.sqlite'; old=db.read_bytes()
        with self.assertRaises((ValueError,FileExistsError)): await self.run_case()
        self.assertEqual(db.read_bytes(),old)

    async def test_private_files_and_frozen_input(self):
        bars=fixture(); report=await self.run_case(bars)
        folder=self.root/'run'; self.assertEqual(folder.stat().st_mode & 0o077,0)
        frozen=json.loads((folder/'input.json').read_text())
        self.assertEqual(frozen['candles'],bars)
        self.assertEqual(frozen['config'],self.config.identity())
        for p in folder.iterdir(): self.assertEqual(p.stat().st_mode & 0o077,0)
        self.assertEqual(report['input_sha256'],__import__('hashlib').sha256((folder/'input.json').read_bytes()).hexdigest())

    async def test_reopen_retains_account_and_does_not_replay(self):
        report=await self.run_case()
        async with PaperTradingSession(self.root/'run/account.sqlite') as session:
            self.assertEqual(session.control_status['mode'],'halted')
            self.assertEqual(session.exchange.snapshot()['trades'],report['replay']['trades'])

    async def test_lost_ack_halts_before_another_order_without_resubmit(self):
        original=_PaperAdapter.create_order; calls=[]
        async def lose(adapter,*a,**kw):
            result=await original(adapter,*a,**kw); calls.append(result)
            raise TimeoutError('PRIVATE_ACCOUNT_DETAIL')
        with patch.object(_PaperAdapter,'create_order',lose):
            with self.assertRaises(Exception): await self.run_case()
        self.assertEqual(len(calls),1)
        report=PaperSessionObserver(self.root/'run/account.sqlite').read()
        self.assertEqual(report['orders']['account_fills'],1)
        self.assertEqual(report['orders']['unresolved'],1)
        self.assertEqual(report['control']['mode'],'halted')
        self.assertFalse((self.root/'run/result.json').exists())

    async def test_catchable_strategy_error_halts_and_releases_owner(self):
        with patch.object(PaperRunner,'_strategy',side_effect=RuntimeError('private')):
            with self.assertRaises(RuntimeError): await self.run_case()
        async with PaperTradingSession(self.root/'run/account.sqlite') as session:
            self.assertEqual(session.control_status['mode'],'halted')
        self.assertFalse((self.root/'run/result.json').exists())

    async def test_cancellation_propagates_after_halt(self):
        async def cancel(*a,**kw): raise asyncio.CancelledError()
        with patch.object(PaperRunner,'_run',cancel):
            with self.assertRaises(asyncio.CancelledError): await self.run_case()
        report=PaperSessionObserver(self.root/'run/account.sqlite').read()
        self.assertEqual(report['control']['mode'],'halted')
        self.assertFalse((self.root/'run/result.json').exists())

    async def test_failed_halt_never_publishes_completion(self):
        original=PaperTradingSession.set_operating_mode
        async def broken(session,mode,**kw):
            if mode=='halted': raise OSError('private halt error')
            return await original(session,mode,**kw)
        with patch.object(PaperTradingSession,'set_operating_mode',broken):
            with self.assertRaisesRegex(Exception,'durable_replay_stop_unconfirmed'):
                await self.run_case()
        self.assertFalse((self.root/'run/result.json').exists())
        async with PaperTradingSession(self.root/'run/account.sqlite') as session:
            self.assertEqual(session.control_status['mode'],'active')

    async def test_future_change_cannot_change_earlier_fills(self):
        first=fixture(75); second=fixture(100)
        second[80:]=[[r[0],'200','202','198','201','1'] for r in second[80:]]
        a=await self.run_case(first,name='a');b=await self.run_case(second,name='b')
        cutoff=first[-1][0]
        self.assertEqual(a['replay']['trades'],[t for t in b['replay']['trades'] if t['timestamp']<=cutoff])

    async def test_risk_stop_behavior_matches_original(self):
        config=PaperRunConfig(initial_balance=D('1000'),position_size_pct=100,
            max_drawdown_pct=D('.01'),max_daily_loss_pct=D('.01'))
        report=await self.run_case(config=config)
        self.assertEqual(report['replay'],await PaperRunner(config).run(fixture()))
        self.assertTrue(report['replay']['drawdown_halted'])

    async def test_final_signal_is_pending_not_forced_fill(self):
        bars=fixture(50); report=await self.run_case(bars)
        self.assertEqual(report['replay'],await PaperRunner(self.config).run(bars))
        self.assertEqual(report['replay']['trades'],[])

    async def test_legacy_checkpoint_resume_is_unchanged(self):
        path=self.root/'legacy.json'
        first=await PaperRunner(self.config).run(fixture(),checkpoint_path=path,stop_after=55)
        self.assertEqual(first['status'],'paused')
        resumed=await PaperRunner(self.config).run(fixture(),checkpoint_path=path)
        self.assertEqual(resumed,await PaperRunner(self.config).run(fixture()))

    async def test_corrupt_result_is_rejected_after_halt(self):
        original=PaperRunner._run
        async def wrong(runner,*args,**kw):
            result=await original(runner,*args,**kw); result['cash']='1'; return result
        with patch.object(PaperRunner,'_run',wrong):
            with self.assertRaisesRegex(ValueError,'durable_replay_result_mismatch'):
                await self.run_case(fixture(3))
        self.assertEqual(PaperSessionObserver(self.root/'run/account.sqlite').read()['control']['mode'],'halted')
        self.assertFalse((self.root/'run/result.json').exists())

    async def test_observer_can_read_account_while_engine_owns_it(self):
        original=PaperRunner._execute_pending; reports=[]
        async def observe(runner,*args,**kw):
            reports.append(PaperSessionObserver(self.root/'run/account.sqlite').read())
            return await original(runner,*args,**kw)
        with patch.object(PaperRunner,'_execute_pending',observe):
            await self.run_case(fixture(65))
        self.assertTrue(reports)
        self.assertEqual(reports[0]['control']['mode'],'active')
        self.assertTrue(all(r['read_only'] for r in reports))

    async def test_no_network_or_credentials_are_required(self):
        with patch.dict(os.environ, {'KIS_APPKEY':'SECRET_KEY_NOT_TO_COPY','OPENAI_API_KEY':'SECRET_KEY_NOT_TO_COPY'}):
            await self.run_case(fixture(3))
        for path in (self.root/'run').glob('*.json'):
            self.assertNotIn('SECRET_KEY_NOT_TO_COPY',path.read_text())

    async def test_original_context_ignores_ambient_precision(self):
        with localcontext() as ctx:
            ctx.prec=4
            result=await self.run_case(fixture(60))
        self.assertEqual(result['replay'],await PaperRunner(self.config).run(fixture(60)))


class DurableCLITests(unittest.TestCase):
    def test_cli_success_and_existing_output_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.json';out=root/'run'
            source.write_text(json.dumps({'symbol':'BTC/USDT','timeframe':'1h','candles':fixture(60)}))
            command=[sys.executable,str(ROOT/'tools/paper_operate.py'),str(source),'--directory',str(out)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads(result.stdout);self.assertEqual(report['status'],'completed_halted')
            self.assertEqual(result.stderr,'')
            old=(out/'account.sqlite').read_bytes()
            repeated=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(repeated.returncode,2);self.assertEqual(repeated.stderr,'')
            self.assertEqual(json.loads(repeated.stdout),{'error':'durable_paper_run_failed_check_directory'})
            self.assertEqual((out/'account.sqlite').read_bytes(),old)

    def test_cli_bad_input_does_not_leak_path_or_make_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'secret.json';p.write_text('{"token":"DO_NOT_LEAK"}')
            result=subprocess.run([sys.executable,str(ROOT/'tools/paper_operate.py'),str(p),'--directory',str(root/'out')],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout),{'error':'durable_paper_run_failed_check_directory'})
            self.assertFalse((root/'out').exists())

    def test_demo_requires_no_input_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'demo'
            p=subprocess.run([sys.executable,str(ROOT/'tools/paper_operate.py'),'--demo','--directory',str(out)],capture_output=True,text=True,timeout=30)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertGreater(len(json.loads(p.stdout)['replay']['trades']),0)

    def test_hard_exit_keeps_account_but_never_creates_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'run'
            script="""
import asyncio,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_order_durable_run import fixture
from trading_bot.durable_paper_run import run_durable_replay
from trading_core.paper_session import _PaperAdapter
original=_PaperAdapter.create_order
async def die(self,*a,**kw):
    await original(self,*a,**kw)
    os._exit(73)
_PaperAdapter.create_order=die
asyncio.run(run_durable_replay(fixture(60),directory=sys.argv[1]))
"""
            env=dict(os.environ,PYTHONPATH=os.pathsep.join(str(ROOT/p) for p in ('packages/core','packages/data','packages/strategy','apps/server')))
            result=subprocess.run([sys.executable,'-c',script,str(out)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,73,result.stderr)
            self.assertFalse((out/'result.json').exists())
            observed=PaperSessionObserver(out/'account.sqlite').read()
            self.assertEqual(observed['orders']['account_fills'],1)
            self.assertEqual(observed['orders']['unresolved'],1)
            self.assertEqual(observed['control']['mode'],'active')

    def test_cli_error_json_uses_stdout_even_with_library_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'input.json';source.write_text(json.dumps({'symbol':'BTC/USDT','timeframe':'1h','candles':fixture(3)}))
            script="""
import importlib.util,logging,sys
from pathlib import Path
root=Path.cwd()
for p in ('packages/core','packages/data','packages/strategy','apps/server'):sys.path.insert(0,str(root/p))
from trading_bot.paper_runner import PaperRunner
async def fail(*a,**kw):
    logging.getLogger('trading_core.order_manager').warning('order_submission_outcome_unknown')
    raise RuntimeError('SECRET_PRIVATE_RAW_ERROR')
PaperRunner._run=fail
spec=importlib.util.spec_from_file_location('cli',root/'tools/paper_operate.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
raise SystemExit(m.main(sys.argv[1:]))
"""
            result=subprocess.run([sys.executable,'-c',script,str(source),'--directory',str(Path(tmp)/'out')],cwd=ROOT,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout),{'error':'durable_paper_run_failed_check_directory'})
            self.assertNotIn('SECRET_PRIVATE_RAW_ERROR',result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
