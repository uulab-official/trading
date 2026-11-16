"""Persistent local-only operating modes; tests never call a real provider."""
import asyncio
from contextlib import closing
from copy import deepcopy
from decimal import Decimal as D
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_admission import PaperOrderRejected
from trading_core.order_manager import (OrderSide, OrderType, OrderStatus,
    OrderOutcomeUnknown, OrderPersistenceError, ReconciliationRequired)
from trading_core.order_reconciliation import reconcile_order
from trading_core.order_state_store import _canonical, _digest


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'account.sqlite'
        self.sessions = []
        guard = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        guard.start(); self.addCleanup(guard.stop)

    async def asyncTearDown(self):
        for session in reversed(self.sessions):
            await session.close()
        self.tmp.cleanup()

    async def open(self, **kwargs):
        self.assertIn('enable_control', inspect.signature(PaperTradingSession).parameters,
                      'durable operating control is not implemented')
        s = PaperTradingSession(self.path, **kwargs)
        self.sessions.append(s)
        await s.__aenter__()
        return s

    async def controlled(self, **kwargs):
        return await self.open(create=True, initial_balance='1000', enable_control=True, **kwargs)

    async def mode(self, session, mode, **kwargs):
        kwargs.setdefault('expected_generation', session.control_status['generation'])
        kwargs.setdefault('reason', 'operator')
        return await session.set_operating_mode(mode, **kwargs)

    async def buy(self, session, key='buy', amount='1'):
        return await session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D(amount), client_order_id=key)

    async def sell(self, session, key='sell', amount='1'):
        return await session.manager.place_order('BTC/USDT', OrderSide.SELL,
            OrderType.MARKET, D(amount), client_order_id=key)

    async def test_new_controlled_account_starts_halted_without_order(self):
        s = await self.controlled()
        self.assertEqual(s.control_status, {'enabled': True, 'mode': 'halted',
            'generation': 0, 'reason': 'created'})
        s.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        before = s.exchange.snapshot(); last = s._store._last
        with self.assertRaises(PaperOrderRejected): await self.buy(s)
        self.assertEqual(s.exchange.snapshot(), before)
        self.assertEqual(s._store._last, last)
        self.assertEqual(s.manager.get_all_orders(), {})

    async def test_active_mode_preserves_original_account_arithmetic(self):
        s = await self.controlled(fee_pct='.1')
        await self.mode(s, 'active')
        s.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        self.assertEqual((await self.buy(s)).status, OrderStatus.FILLED)
        self.assertEqual(s.exchange.snapshot()['balances']['USDT'], '899.9')
        self.assertEqual(s.exchange.snapshot()['total_fees'], '0.1')

    async def test_reduce_only_allows_held_spot_sell_and_blocks_new_buy(self):
        s = await self.controlled()
        await self.mode(s, 'active'); s.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.buy(s, amount='2'); await self.mode(s, 'reduce_only')
        before = s.exchange.snapshot()
        with self.assertRaises(PaperOrderRejected): await self.buy(s, 'extra')
        self.assertEqual(s.exchange.snapshot(), before)
        self.assertEqual((await self.sell(s)).status, OrderStatus.FILLED)
        with self.assertRaises(PaperOrderRejected): await self.sell(s, 'oversell', '2')
        self.assertEqual(s.exchange.snapshot()['balances']['BTC'], '1')

    async def test_halted_blocks_both_sides_but_keeps_quotes_and_queries(self):
        s = await self.controlled(); await self.mode(s, 'active')
        s.exchange.set_price('BTC/USDT', D('100'), timestamp=1); await self.buy(s)
        await self.mode(s, 'halted')
        s.exchange.set_price('BTC/USDT', D('110'), timestamp=2)
        with self.assertRaises(PaperOrderRejected): await self.buy(s, 'extra')
        with self.assertRaises(PaperOrderRejected): await self.sell(s)
        self.assertEqual((await s.exchange.fetch_ticker('BTC/USDT')).last, D('110'))
        self.assertEqual(len(await s.exchange.fetch_my_trades()), 1)

    async def test_reopen_retains_mode_generation_and_same_account(self):
        s = await self.controlled(); await self.mode(s, 'active'); await self.mode(s, 'reduce_only')
        state, identity = s.control_status, s.account_id
        await s.close(); r = await self.open()
        self.assertEqual(r.control_status, state); self.assertEqual(r.account_id, identity)
        with self.assertRaises(ValueError): await self.open(enable_control=True)

    async def test_legacy_accounts_are_not_migrated_or_silently_paused(self):
        s = await self.open(create=True, initial_balance='1000')
        self.assertEqual(s.control_status, {'enabled': False, 'mode': None,
            'generation': None, 'reason': None})
        with self.assertRaises(ValueError): await self.mode(s, 'halted', expected_generation=0)
        s.exchange.set_price('BTC/USDT', D('100'), timestamp=1); await self.buy(s)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT version FROM paper_session').fetchone()[0], 1)

    async def test_same_mode_is_noop_but_stale_generation_is_not_accepted(self):
        s = await self.controlled(); before = s._store._last
        self.assertEqual(await self.mode(s, 'halted'), s.control_status)
        self.assertEqual(s._store._last, before)
        await self.mode(s, 'active')
        with self.assertRaises(ValueError): await self.mode(s, 'active', expected_generation=0)
        self.assertEqual(s.control_status['generation'], 1)

    async def test_invalid_controls_and_generations_do_not_write(self):
        s = await self.controlled(); before = s._store._last
        for mode, generation, reason in [('LIVE',0,'operator'), ('active',True,'operator'),
                ('active',-1,'operator'), ('active',0,'secret token'), ('active',0,'created'),
                ([],0,'operator'), ('active',0,None)]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                await s.set_operating_mode(mode, expected_generation=generation, reason=reason)
            self.assertEqual(s._store._last, before)

    async def test_invalid_enable_option_creates_no_file(self):
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.open(create=True, enable_control=value)
            self.assertFalse(self.path.exists())

    async def test_status_is_detached(self):
        s = await self.controlled(); status = s.control_status
        status['mode'] = 'active'; status['generation'] = 99
        self.assertEqual(s.control_status['mode'], 'halted')
        self.assertEqual(s.control_status['generation'], 0)

    async def test_concurrent_transitions_use_compare_and_swap(self):
        s = await self.controlled()
        results = await asyncio.gather(self.mode(s,'active',expected_generation=0),
            self.mode(s,'reduce_only',expected_generation=0), return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertEqual(sum(isinstance(x,ValueError) for x in results),1)
        self.assertEqual(s.control_status['generation'],1)

    async def test_completed_order_deduplicates_while_halted(self):
        s = await self.controlled(); await self.mode(s, 'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1); await self.buy(s)
        await self.mode(s,'halted'); before=s.exchange.snapshot()
        self.assertEqual((await self.buy(s)).status,OrderStatus.FILLED)
        self.assertEqual(s.exchange.snapshot(),before)

    async def test_unknown_does_not_prevent_stop_but_prevents_relaxation(self):
        s = await self.controlled(); await self.mode(s,'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        with patch.object(s.exchange,'create_order',side_effect=TimeoutError('lost')):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(s)
        await self.mode(s,'halted')
        for mode in ('active','reduce_only'):
            with self.assertRaises(ReconciliationRequired): await self.mode(s,mode)
        self.assertEqual(s.control_status['mode'],'halted')
        self.assertEqual((await s.manager.get_order('buy')).status,OrderStatus.UNKNOWN)

    async def test_reconciliation_works_while_halted_without_duplicate_fill(self):
        s=await self.controlled(); await self.mode(s,'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        original=s.exchange.create_order
        async def lost(**kw):
            await original(**kw); raise TimeoutError('lost')
        with patch.object(s.exchange,'create_order',side_effect=lost):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(s)
        await self.mode(s,'halted'); before=s.exchange.snapshot(); await s.close()
        r=await self.open()
        await reconcile_order(r.manager,'buy',allow_client_lookup=True)
        self.assertEqual(r.exchange.snapshot(),before)
        self.assertEqual(r.control_status['mode'],'halted')
        await self.mode(r,'active')
        self.assertEqual(r.control_status['mode'],'active')

    async def test_failed_control_commit_latches_without_success(self):
        s=await self.controlled(); await self.mode(s,'active')
        with patch.object(s._store,'_commit',side_effect=OSError('sensitive path')):
            with self.assertRaises(OrderPersistenceError) as error: await self.mode(s,'halted')
        self.assertNotIn('sensitive',str(error.exception))
        self.assertTrue(s.manager.storage_failed)
        with self.assertRaises(OrderPersistenceError): await self.mode(s,'active')
        await s.close(); r=await self.open()
        self.assertEqual(r.control_status['mode'],'active')  # failed stop was not a success

    async def test_committed_control_with_lost_reply_is_retained_on_reopen(self):
        s=await self.controlled(); await self.mode(s,'active'); commit=s._store._commit
        def lost():
            commit(); raise OSError('after commit')
        with patch.object(s._store,'_commit',side_effect=lost):
            with self.assertRaises(OrderPersistenceError): await self.mode(s,'halted')
        self.assertTrue(s.manager.storage_failed)
        await s.close(); r=await self.open()
        self.assertEqual(r.control_status['mode'],'halted')
        self.assertEqual(r.control_status['generation'],2)

    async def test_resume_requires_new_quote_when_age_guard_enabled(self):
        with patch('trading_core.paper_admission._wall_ms',return_value=10000), \
             patch('trading_core.paper_admission._mono_ms',return_value=5000):
            s=await self.controlled(max_quote_age_ms=1000)
            s.exchange.set_price('BTC/USDT',D('100'),timestamp=9900)
            await self.mode(s,'active')
            with self.assertRaises(PaperOrderRejected): await self.buy(s)
            s.exchange.set_price('BTC/USDT',D('100'),timestamp=9900)
            with self.assertRaises(PaperOrderRejected): await self.buy(s)
            s.exchange.set_price('BTC/USDT',D('100'),timestamp=9901)
            await self.buy(s)

    async def test_control_noop_does_not_clear_fresh_quote(self):
        with patch('trading_core.paper_admission._wall_ms',return_value=10000), \
             patch('trading_core.paper_admission._mono_ms',return_value=5000):
            s=await self.controlled(max_quote_age_ms=1000); await self.mode(s,'active')
            s.exchange.set_price('BTC/USDT',D('100'),timestamp=9900)
            await self.mode(s,'active'); await self.buy(s)

    async def test_control_state_cannot_be_changed_through_ordinary_write(self):
        s=await self.controlled(); data=s._store.view()[0]
        data['control']['mode']='active'; data['control']['generation']=1; data['control']['reason']='operator'
        with self.assertRaises(OrderPersistenceError): s._store._write(data)
        await s.close(); r=await self.open()
        self.assertEqual(r.control_status['mode'],'halted')

    async def test_malformed_control_with_valid_checksum_is_rejected(self):
        s=await self.controlled(); await s.close()
        with closing(sqlite3.connect(self.path)) as db:
            data=json.loads(db.execute('SELECT payload FROM paper_session').fetchone()[0])
            data['control']['mode']='anything'; raw=_canonical(data)
            db.execute('UPDATE paper_session SET payload=?,digest=?',(raw,_digest(raw))); db.commit()
        with self.assertRaises(OrderPersistenceError): await self.open()

    async def test_control_version_mismatch_is_rejected(self):
        s=await self.controlled(); await s.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET version=1'); db.commit()
        with self.assertRaises(OrderPersistenceError): await self.open()


    async def test_halt_during_account_calculation_prevents_new_fill(self):
        from trading_core.paper_exchange import PaperExchange
        s=await self.controlled(); await self.mode(s,'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        before=s.exchange.snapshot(); entered=asyncio.Event(); release=asyncio.Event()
        original=PaperExchange.create_order; calls=0
        async def slow(book,*args,**kwargs):
            nonlocal calls
            calls+=1
            result=await original(book,*args,**kwargs)
            if calls==2:  # first is detached preflight, second is proposed account effect
                entered.set(); await release.wait()
            return result
        with patch.object(PaperExchange,'create_order',new=slow):
            task=asyncio.create_task(self.buy(s))
            try:
                await asyncio.wait_for(entered.wait(),5)
                await self.mode(s,'halted')
            finally:
                release.set()
            with self.assertRaises(OrderOutcomeUnknown): await task
        self.assertEqual(s.exchange.snapshot(),before)
        self.assertEqual(s.control_status['mode'],'halted')
        self.assertEqual((await s.manager.get_order('buy')).status,OrderStatus.UNKNOWN)

    async def test_last_generation_is_reserved_for_halt(self):
        from trading_core.paper_control import MAX_GENERATION, next_control
        state=dict(mode='active',generation=MAX_GENERATION-1,reason='operator')
        with self.assertRaises(ValueError): next_control(state,'reduce_only',MAX_GENERATION-1,'operator')
        final=next_control(state,'halted',MAX_GENERATION-1,'operator')
        self.assertEqual(final['generation'],MAX_GENERATION)
        with self.assertRaises(ValueError): next_control(final,'active',MAX_GENERATION,'operator')
        self.assertEqual(next_control(final,'halted',MAX_GENERATION,'operator'),final)

    async def test_control_validator_rejects_impossible_states(self):
        from trading_core.paper_control import MAX_GENERATION, validate_control
        for state in [None,{},dict(mode='active',generation=0,reason='created'),
                dict(mode='halted',generation=True,reason='created'),
                dict(mode='halted',generation=1,reason='created'),
                dict(mode='active',generation=MAX_GENERATION,reason='operator'),
                dict(mode='halted',generation=1,reason='password')]:
            with self.subTest(state=state),self.assertRaises(ValueError): validate_control(state)

    async def test_age_and_notional_policy_coexist_with_v3_on_reopen(self):
        s=await self.controlled(max_quote_age_ms=1000,max_order_notional='200')
        policy=s.admission_policy; await s.close(); r=await self.open()
        self.assertEqual(r.admission_policy,policy)
        self.assertEqual(r.control_status['mode'],'halted')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT version FROM paper_session').fetchone()[0],3)

    async def test_reduce_only_does_not_bypass_existing_notional_limit(self):
        s=await self.controlled(max_order_notional='100'); await self.mode(s,'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        await self.buy(s,'one'); await self.buy(s,'two'); await self.mode(s,'reduce_only')
        with self.assertRaises(PaperOrderRejected): await self.sell(s,amount='2')
        self.assertEqual((await self.sell(s)).status,OrderStatus.FILLED)

    async def test_existing_v2_account_keeps_policy_and_format(self):
        s=await self.open(create=True,max_order_notional='100')
        before=s.admission_policy
        with self.assertRaises(ValueError): await self.mode(s,'halted',expected_generation=0)
        await s.close(); r=await self.open()
        self.assertEqual(r.admission_policy,before)
        self.assertFalse(r.control_status['enabled'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT version FROM paper_session').fetchone()[0],2)

    async def test_control_change_preserves_entire_economic_snapshot(self):
        s=await self.controlled(); await self.mode(s,'active')
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1); await self.buy(s)
        book=s.exchange.snapshot(); orders=s.manager.get_all_orders()
        for mode in ('reduce_only','halted','reduce_only','active'):
            await self.mode(s,mode,reason='risk_review')
            self.assertEqual(s.exchange.snapshot(),book)
            self.assertEqual(s.manager.get_all_orders(),orders)

    async def test_closed_session_cannot_report_or_change_mode(self):
        s=await self.controlled(); await s.close()
        with self.assertRaises(OrderPersistenceError): _=s.control_status
        with self.assertRaises(OrderPersistenceError):
            await s.set_operating_mode('active',expected_generation=0,reason='operator')

    async def test_second_owner_cannot_change_control(self):
        s=await self.controlled()
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.assertEqual(s.control_status['mode'],'halted')

    async def test_cancelled_waiter_never_changes_control(self):
        s=await self.controlled(); before=s._store._last
        async with s.manager._lock:
            task=asyncio.create_task(self.mode(s,'active'))
            await asyncio.sleep(0); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(s._store._last,before)
        self.assertEqual(s.control_status['generation'],0)

    async def test_relaxation_is_blocked_by_existing_io(self):
        s=await self.controlled(); s.manager._inflight.add('readback')
        try:
            with self.assertRaises(ReconciliationRequired): await self.mode(s,'active')
        finally:
            s.manager._inflight.clear()
        self.assertEqual(s.control_status['mode'],'halted')

    async def test_keyboard_interrupt_during_commit_latches_and_propagates(self):
        s=await self.controlled(); await self.mode(s,'active')
        with patch.object(s._store,'_commit',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): await self.mode(s,'halted')
        self.assertTrue(s.manager.storage_failed)
        await s.close(); r=await self.open()
        self.assertEqual(r.control_status['mode'],'active')

    async def test_control_only_commit_cannot_smuggle_economic_changes(self):
        s=await self.controlled(); data=s._store.view()[0]
        data['control']=dict(mode='active',generation=1,reason='operator')
        # A legitimate ticker change is still forbidden in a control-only save.
        from trading_core.paper_exchange import PaperExchange
        book=PaperExchange.from_snapshot(data['book']); book.set_price('BTC/USDT',D('100'),timestamp=1)
        data['book']=book.snapshot()
        with self.assertRaises(OrderPersistenceError): s._store._write(data,control_update=True)
        await s.close(); r=await self.open()
        self.assertEqual(r.control_status['mode'],'halted')
        self.assertEqual(r.exchange.snapshot()['tickers'],{})


class ControlProcessTests(unittest.IsolatedAsyncioTestCase):
    async def crash(self, after):
        self.assertIn('enable_control',inspect.signature(PaperTradingSession).parameters)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'control.sqlite'
            async with PaperTradingSession(path,create=True,enable_control=True) as s:
                identity=s.account_id
                await s.set_operating_mode('active',expected_generation=0,reason='operator')
                before=s.exchange.snapshot()
            code = r"""
import asyncio, os, sys
sys.path.insert(0,sys.argv[1]+'/packages/core')
from trading_core.paper_session import PaperTradingSession
async def main():
    async with PaperTradingSession(sys.argv[2]) as session:
        commit=session._store._commit
        def crash():
            if sys.argv[3]=='after': commit()
            os._exit(75)
        session._store._commit=crash
        await session.set_operating_mode('halted',expected_generation=1,reason='maintenance')
asyncio.run(main())
"""
            result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT),str(path),
                'after' if after else 'before'],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,75,result.stderr)
            async with PaperTradingSession(path) as restored:
                self.assertEqual(restored.account_id,identity)
                self.assertEqual(restored.exchange.snapshot(),before)
                self.assertEqual(restored.control_status['mode'],'halted' if after else 'active')
                self.assertEqual(restored.control_status['generation'],2 if after else 1)

    async def test_process_death_before_mode_commit_does_not_claim_success(self):
        await self.crash(False)

    async def test_process_death_after_mode_commit_retains_stop(self):
        await self.crash(True)


class ControlDemoTests(unittest.TestCase):
    def test_synthetic_demo_and_no_overwrite(self):
        tool=ROOT/'tools/paper_control_demo.py'
        self.assertTrue(tool.exists(), 'missing paper control demo')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'demo.sqlite'
            command=[sys.executable,'-B',str(tool),'--database',str(path)]
            first=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(first.returncode,0,first.stderr)
            data=json.loads(first.stdout)
            self.assertTrue(data['synthetic'])
            self.assertEqual(data['cash'],'999.6')
            self.assertEqual(data['fees'],'0.4')
            self.assertEqual(data['fills'],3)
            self.assertEqual(data['restored_mode'],'halted')
            self.assertEqual(data['final_control']['mode'],'halted')
            self.assertEqual(data['blocked'],['initial_halt','reduce_only_buy','halted_sell'])
            before=path.read_bytes()
            second=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
