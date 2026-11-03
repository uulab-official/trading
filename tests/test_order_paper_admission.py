"""Local paper admission: no network, keys or external account access."""
import asyncio
from copy import deepcopy
from contextlib import closing
from decimal import Decimal as D
import importlib
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
from trading_core.order_manager import (OrderSide, OrderType, OrderStatus,
    OrderOutcomeUnknown, OrderPersistenceError, ReconciliationRequired)
from trading_core.order_reconciliation import reconcile_order
from trading_core.paper_exchange import PaperExchange


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'paper.sqlite'
        self.sessions = []
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()

    async def asyncTearDown(self):
        for session in reversed(self.sessions):
            await session.close()
        self.guard.stop()
        self.tmp.cleanup()

    async def open(self, path=None, **kwargs):
        session = PaperTradingSession(path or self.path, **kwargs)
        self.sessions.append(session)
        await session.__aenter__()
        return session

    async def buy(self, session, key='buy', amount='1', **kwargs):
        return await session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D(amount), client_order_id=key, **kwargs)

    async def rejected_unchanged(self, session, call):
        before = session.exchange.snapshot()
        revision = session._store._last
        with self.assertRaises(ValueError):
            await call
        self.assertEqual(session._store._last, revision)
        self.assertEqual(session.exchange.snapshot(), before)
        self.assertEqual(session.manager.get_all_orders(), {})
        self.assertFalse(session.manager.storage_failed)

    async def test_insufficient_cash_does_not_strand_an_unknown_intent(self):
        session = await self.open(create=True, initial_balance=D('100'))
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.rejected_unchanged(session, self.buy(session, amount='2'))
        self.assertEqual((await self.buy(session, amount='1')).status, OrderStatus.FILLED)

    async def test_fees_are_included_in_preflight_and_charged_only_once(self):
        session = await self.open(create=True, initial_balance=D('100'), fee_pct=D('1'))
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.rejected_unchanged(session, self.buy(session))
        await self.buy(session, amount='.5')
        self.assertEqual(session.exchange.snapshot()['balances']['USDT'], '49.5')
        self.assertEqual(session.exchange.snapshot()['total_fees'], '0.5')

    async def test_missing_price_is_local_rejection_not_unknown(self):
        session = await self.open(create=True)
        await self.rejected_unchanged(session, self.buy(session))
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        self.assertEqual((await self.buy(session)).status, OrderStatus.FILLED)

    async def test_unsupported_limit_order_leaves_no_durable_intent(self):
        session = await self.open(create=True)
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.rejected_unchanged(session, session.manager.place_order(
            'BTC/USDT', OrderSide.BUY, OrderType.LIMIT, D('1'),
            price=D('100'), client_order_id='limit'))

    async def test_oversell_and_wrong_currency_leave_no_intent(self):
        session = await self.open(create=True)
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.rejected_unchanged(session, session.manager.place_order(
            'BTC/USDT', OrderSide.SELL, OrderType.MARKET,D('1'),client_order_id='sell'))
        await self.rejected_unchanged(session, session.manager.place_order(
            'BTC/KRW', OrderSide.BUY, OrderType.MARKET,D('1'),client_order_id='wrong'))

    async def test_submitted_adapter_failure_still_requires_reconciliation(self):
        session = await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        with patch.object(session.exchange,'create_order',side_effect=TimeoutError('unproven')):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(session)
        self.assertEqual((await session.manager.get_order('buy')).status,OrderStatus.UNKNOWN)
        with self.assertRaises(ReconciliationRequired): await self.buy(session,'other')

    async def test_two_concurrent_orders_cannot_overspend_or_leave_phantom_intent(self):
        session = await self.open(create=True,initial_balance=D('100'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        results = await asyncio.gather(self.buy(session,'a'), self.buy(session,'b'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
        self.assertEqual(len(session.manager.get_all_orders()),1)
        self.assertEqual(len(session.exchange.snapshot()['trades']),1)
        self.assertFalse(any(o.status==OrderStatus.UNKNOWN for o in session.manager.get_all_orders().values()))
    async def test_regressing_fill_time_is_rejected_before_intent_or_storage_damage(self):
        session=await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=200)
        await self.buy(session)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=100)
        before=session._store._last
        with self.assertRaises(ValueError): await self.buy(session,'earlier')
        self.assertEqual(session._store._last,before)
        self.assertFalse(session.manager.storage_failed)
        self.assertEqual(len(session.manager.get_all_orders()),1)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=201)
        self.assertEqual((await self.buy(session,'later')).status,OrderStatus.FILLED)

    async def test_preflight_cancellation_leaves_no_order_and_allows_next_request(self):
        session=await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        entered=asyncio.Event()
        async def delay(order):
            entered.set();await asyncio.Event().wait()
        with patch.object(session.exchange,'preflight',side_effect=delay):
            task=asyncio.create_task(self.buy(session))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(session.manager.get_all_orders(),{})
        self.assertEqual((await self.buy(session)).status,OrderStatus.FILLED)

    async def test_preflight_storage_error_latches_manager_without_submit(self):
        session=await self.open(create=True)
        with patch.object(session._store,'view',side_effect=OrderPersistenceError('failed')):
            with self.assertRaises(OrderPersistenceError): await self.buy(session)
        self.assertTrue(session.manager.storage_failed)
        self.assertEqual(session.manager.get_all_orders(),{})

    async def test_oversized_venue_id_is_rejected_before_an_unreconcilable_fill(self):
        session=await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        await self.rejected_unchanged(session,self.buy(session,'x'*128))


    async def test_synthetic_cli_and_existing_file_protection(self):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_admission_demo.py'),
            '--database',str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertTrue(data['success']);self.assertFalse(data['live_enabled'])
        self.assertEqual(data['fill_count'],2);self.assertEqual(D(data['final_cash']),D('999.8'))
        original=self.path.read_bytes()
        repeat=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_admission_demo.py'),
            '--database',str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertNotEqual(repeat.returncode,0)
        self.assertEqual(self.path.read_bytes(),original)

    async def test_legacy_account_stays_version_one_without_implicit_policy(self):
        session=await self.open(create=True)
        self.assertEqual(session.admission_policy,{'max_quote_age_ms':None,'max_order_notional':None})
        with closing(sqlite3.connect(self.path)) as db:
            version,raw=db.execute('SELECT version,payload FROM paper_session').fetchone()
        self.assertEqual(version,1);self.assertNotIn('policy',json.loads(raw))



# Helpers shared without collecting the base's tests twice.
class GuardedTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = AdmissionTests.asyncSetUp
    asyncTearDown = AdmissionTests.asyncTearDown
    open = AdmissionTests.open
    buy = AdmissionTests.buy
    rejected_unchanged = AdmissionTests.rejected_unchanged

    def clock(self):
        self.assertIn('max_quote_age_ms',inspect.signature(PaperTradingSession).parameters,
                      'missing persisted quote-age policy')
        module = importlib.import_module('trading_core.paper_admission')
        self.wall, self.mono = 2000000000000, 10000
        for name, clock in [('_wall_ms', lambda:self.wall), ('_mono_ms', lambda:self.mono)]:
            patcher=patch.object(module,name,side_effect=clock)
            patcher.start();self.addCleanup(patcher.stop)

    async def guarded(self, **kw):
        self.clock()
        return await self.open(create=True,initial_balance=D('1000'),max_quote_age_ms=1000,**kw)

    async def test_policy_is_persisted_cannot_be_overridden_and_view_is_detached(self):
        session=await self.guarded(max_order_notional=D('200'))
        self.assertEqual(session.admission_policy,{'max_quote_age_ms':1000,'max_order_notional':'200'})
        view=session.admission_policy;view['max_quote_age_ms']=99999
        self.assertEqual(session.admission_policy['max_quote_age_ms'],1000)
        await session.close()
        with self.assertRaises(ValueError): await self.open(max_quote_age_ms=2000)
        restored=await self.open()
        self.assertEqual(restored.admission_policy['max_quote_age_ms'],1000)

    async def test_invalid_policy_creates_no_db(self):
        self.clock()
        for kw in [dict(max_quote_age_ms=True),dict(max_quote_age_ms=0),dict(max_quote_age_ms=1.5),
                   dict(max_quote_age_ms=86400001),dict(max_order_notional='0'),dict(max_order_notional='NaN')]:
            with self.subTest(kw=kw),self.assertRaises(ValueError): await self.open(create=True,**kw)
            self.assertFalse(self.path.exists())

    async def test_restored_mark_cannot_authorize_new_order_even_when_recent(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        await session.close(); restored=await self.open()
        await self.rejected_unchanged(restored,self.buy(restored))
        # A replay of the same saved observation is not a new observation.
        restored.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        await self.rejected_unchanged(restored,self.buy(restored))
        self.wall+=1;self.mono+=1
        restored.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        self.assertEqual((await self.buy(restored)).status,OrderStatus.FILLED)

    async def test_quote_age_includes_time_before_receipt_and_boundary_is_inclusive(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall-900)
        self.wall+=101;self.mono+=101
        await self.rejected_unchanged(session,self.buy(session))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall-1000)
        self.assertEqual((await self.buy(session)).status,OrderStatus.FILLED)

    async def test_exact_quote_replays_do_not_extend_expiry(self):
        session=await self.guarded();stamp=self.wall
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=stamp)
        self.mono+=1001  # wall clock stalled; elapsed monotonic must still expire.
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=stamp)
        await self.rejected_unchanged(session,self.buy(session))

    async def test_future_stale_conflicting_and_reversed_quote_updates_are_atomic(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        before=session.exchange.snapshot();revision=session._store._last
        for price,stamp in [(D('100'),self.wall+1),(D('101'),self.wall),
                            (D('100'),self.wall-1),(D('100'),self.wall-1001)]:
            with self.subTest(stamp=stamp),self.assertRaises(ValueError):
                session.exchange.set_price('BTC/USDT',price,timestamp=stamp)
            self.assertEqual(session.exchange.snapshot(),before)
            self.assertEqual(session._store._last,revision)

    async def test_wall_or_monotonic_rollback_cannot_make_quote_fresh(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        self.wall-=1
        await self.rejected_unchanged(session,self.buy(session))
        self.wall+=1;self.mono-=1
        await self.rejected_unchanged(session,self.buy(session))

    async def test_notional_uses_modeled_slippage_and_accepts_equal_limit(self):
        session=await self.guarded(max_order_notional=D('100'),slippage_pct=D('1'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        await self.rejected_unchanged(session,self.buy(session))
        self.wall+=1;self.mono+=1
        session.exchange.set_price('BTC/USDT',D('99.00990099009900990099009901'),timestamp=self.wall)
        await self.buy(session)
        self.assertEqual(len(session.exchange.snapshot()['trades']),1)

    async def test_policy_can_be_notional_only_for_historical_inputs(self):
        self.clock()
        session=await self.open(create=True,max_order_notional=D('100'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        await self.buy(session)
        with self.assertRaises(ValueError): await self.buy(session,'large',amount='2')
        self.assertEqual(len(session.manager.get_all_orders()),1)

    async def test_duplicate_completed_id_does_not_need_fresh_quote_or_create_fill(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        first=await self.buy(session);before=session.exchange.snapshot()
        await session.close();restored=await self.open()
        self.wall+=100000;self.mono+=100000
        duplicate=await self.buy(restored)
        self.assertEqual(duplicate.exchange_order_id,first.exchange_order_id)
        self.assertEqual(restored.exchange.snapshot(),before)
        with self.assertRaises(ValueError): await self.buy(restored,amount='2')

    async def test_new_quote_is_required_per_symbol(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        with self.assertRaises(ValueError):
            await session.manager.place_order('ETH/USDT',OrderSide.BUY,OrderType.MARKET,D('1'),client_order_id='eth')
        self.assertEqual(session.manager.get_all_orders(),{})

    async def test_lost_ack_reconciliation_does_not_need_new_quote(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        original=session.exchange.create_order
        async def lose(**kw):
            await original(**kw);raise TimeoutError('lost')
        with patch.object(session.exchange,'create_order',side_effect=lose):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(session)
        before=session.exchange.snapshot();await session.close()
        restored=await self.open();self.wall+=100000;self.mono+=100000
        result=await reconcile_order(restored.manager,'buy',allow_client_lookup=True)
        self.assertEqual(result.status,OrderStatus.FILLED)
        self.assertEqual(restored.exchange.snapshot(),before)

    async def test_expiry_during_durable_submission_keeps_unknown_without_fill(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        commit=session._store._commit
        def expire_after_intent():
            commit();self.mono+=1001
        with patch.object(session._store,'_commit',side_effect=expire_after_intent):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(session)
        self.assertEqual(session.exchange.snapshot()['trades'],[])
        self.assertEqual((await session.manager.get_order('buy')).status,OrderStatus.UNKNOWN)
        with self.assertRaises(ReconciliationRequired): await self.buy(session,'new')

    async def test_policy_change_is_not_saved_and_latches_store(self):
        session=await self.guarded()
        data=session._store.view()[0];data['policy']['max_quote_age_ms']=10000
        with self.assertRaises(OrderPersistenceError):session._store._write(data)
        await session.close();restored=await self.open()
        self.assertEqual(restored.admission_policy['max_quote_age_ms'],1000)

    async def test_policy_version_mismatch_is_rejected_on_reopen(self):
        session=await self.guarded();await session.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET version=1'); db.commit()
        with self.assertRaises(OrderPersistenceError):await self.open()

    async def test_failed_quote_write_cannot_create_receipt(self):
        session=await self.guarded()
        with patch.object(session._store,'_commit',side_effect=OSError('disk failure')):
            with self.assertRaises(OrderPersistenceError):
                session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall)
        self.assertEqual(session.exchange._admission._receipts,{})
        await session.close();restored=await self.open()
        await self.rejected_unchanged(restored,self.buy(restored))

    async def test_exact_notional_above_ceiling_is_not_rounded_to_pass(self):
        self.clock()
        session=await self.open(create=True,initial_balance=D('1000'),max_order_notional=D('100'))
        session.exchange.set_price('BTC/USDT',D('100.0000000000000000000000001'),timestamp=1)
        from decimal import localcontext
        with localcontext() as ctx:
            ctx.prec=4
            await self.rejected_unchanged(session,self.buy(session))

    async def test_expired_quote_rejects_sell_without_losing_existing_position(self):
        session=await self.guarded()
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=self.wall);await self.buy(session)
        before=session.exchange.snapshot();self.wall+=1001;self.mono+=1001
        with self.assertRaises(ValueError):
            await session.manager.place_order('BTC/USDT',OrderSide.SELL,OrderType.MARKET,D('1'),client_order_id='sell')
        self.assertEqual(session.exchange.snapshot(),before)
        self.assertEqual(len(session.manager.get_all_orders()),1)

    async def test_fresh_guarded_fills_match_original_engine_for_both_currencies(self):
        self.clock()
        for currency in ('USDT','KRW'):
            path=Path(self.tmp.name)/(currency+'.sqlite')
            settings=dict(initial_balance=D('1000'),fee_pct=D('.1'),slippage_pct=D('.05'),quote_currency=currency)
            session=await self.open(path,create=True,max_quote_age_ms=1000,max_order_notional=D('500'),**settings)
            plain=PaperExchange(**settings);await plain.connect();symbol='BTC/'+currency
            for i,(side,amount,price) in enumerate([('buy','2','100'),('sell','.5','101'),('sell','1.5','99')]):
                self.wall+=1;self.mono+=1
                for book in (session.exchange,plain):book.set_price(symbol,D(price),timestamp=self.wall)
                await plain.create_order(symbol,'market',side,D(amount),params={'clientOrderId':str(i)})
                await session.manager.place_order(symbol,OrderSide(side),OrderType.MARKET,D(amount),client_order_id=str(i))
                self.assertEqual(session.exchange.snapshot(),plain.snapshot())



if __name__ == '__main__': unittest.main()
