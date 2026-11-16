"""Opt-in local paper-account recovery. Never credentials or venue network calls."""
import asyncio
from contextlib import closing
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import (OrderManager, OrderSide, OrderType, OrderStatus,
    OrderOutcomeUnknown, OrderPersistenceError, ReconciliationRequired)
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange
from trading_core.order_reconciliation import reconcile_order


def session_class():
    try:
        return importlib.import_module('trading_core.paper_session').PaperTradingSession
    except ModuleNotFoundError:
        return None


class PaperSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'account.sqlite'
        self.sessions = []
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()

    async def asyncTearDown(self):
        for session in reversed(self.sessions):
            await session.close()
        self.guard.stop()
        self.tmp.cleanup()

    async def open(self, path=None, **kw):
        cls = session_class()
        self.assertIsNotNone(cls, 'missing durable paper session')
        session = cls(path or self.path, **kw)
        self.sessions.append(session)
        await session.__aenter__()
        return session

    async def buy(self, session, key='buy', amount='2'):
        return await session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D(amount), client_order_id=key)

    async def test_reopen_retains_fills_cash_fees_and_client_deduplication(self):
        session = await self.open(create=True, initial_balance=D('1000'), fee_pct=D('0.1'))
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await self.buy(session)
        before = session.exchange.snapshot()
        identity = session.account_id
        await session.close()
        restored = await self.open()
        self.assertEqual(restored.account_id, identity)
        self.assertEqual(restored.exchange.snapshot(), before)
        self.assertEqual(before['balances']['USDT'], '799.8')
        self.assertEqual((await self.buy(restored)).status, OrderStatus.FILLED)
        self.assertEqual(restored.exchange.snapshot(), before)

    async def test_raw_engine_and_session_arithmetic_are_identical(self):
        for currency in ('USDT', 'KRW'):
            path = Path(self.tmp.name) / (currency + '.sqlite')
            args = dict(initial_balance=D('1000'), fee_pct=D('.1'), slippage_pct=D('.05'), quote_currency=currency)
            session = await self.open(path, create=True, **args)
            plain = PaperExchange(**args); await plain.connect()
            symbol = 'BTC/' + currency
            for i, (side, amount, price) in enumerate([('buy','2','100'),('sell','.5','110'),('sell','1.5','90')]):
                for book in (plain, session.exchange): book.set_price(symbol,D(price),timestamp=i+1)
                expected = await plain.create_order(symbol,'market',side,D(amount),params={'clientOrderId':str(i)})
                actual = await session.manager.place_order(symbol,OrderSide(side),OrderType.MARKET,D(amount),client_order_id=str(i))
                self.assertEqual(actual.exchange_order_id, expected['id'])
                self.assertEqual(session.exchange.snapshot(), plain.snapshot())
            await session.close()
            self.assertEqual((await self.open(path)).exchange.snapshot(), plain.snapshot())

    async def test_commit_failure_latches_without_exposing_success(self):
        session = await self.open(create=True, initial_balance=D('1000'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        with patch.object(session._store, '_commit', side_effect=OSError('private message')):
            with self.assertRaises(OrderPersistenceError): await self.buy(session)
        with self.assertRaises(OrderPersistenceError): session.exchange.snapshot()
        with self.assertRaises(OrderPersistenceError): await self.buy(session, key='other')
        await session.close()
        restored = await self.open()
        self.assertEqual(restored.exchange.snapshot()['balances']['USDT'], '1000')
        self.assertEqual(restored.manager.get_all_orders(), {})

    async def test_lost_ack_reopens_then_reconciles_without_new_cash(self):
        session = await self.open(create=True, initial_balance=D('1000'), fee_pct=D('.1'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        original = session.exchange.create_order
        async def lose(**kw):
            await original(**kw)
            raise TimeoutError('lost acknowledgement')
        with patch.object(session.exchange,'create_order',side_effect=lose):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(session)
        before = session.exchange.snapshot()
        await session.close()
        restored = await self.open()
        self.assertEqual((await restored.manager.get_order('buy')).status,OrderStatus.UNKNOWN)
        with self.assertRaises(ReconciliationRequired): await self.buy(restored,'new')
        order = await reconcile_order(restored.manager,'buy',allow_client_lookup=True)
        self.assertEqual(order.status,OrderStatus.FILLED)
        self.assertEqual(restored.exchange.snapshot(),before)

    async def test_separate_orders_db_is_not_treated_as_an_account(self):
        with SQLiteOrderStateStore(self.path,scope='paper',create=True): pass
        before = self.path.read_bytes()
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.assertEqual(self.path.read_bytes(),before)

    async def test_missing_empty_or_foreign_database_is_not_initialized(self):
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.assertFalse(self.path.exists())
        self.path.touch(); self.path.chmod(0o600)
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.assertEqual(self.path.stat().st_size,0)

    async def test_create_does_not_replace_existing_account(self):
        session = await self.open(create=True); await session.close()
        before = self.path.read_bytes()
        with self.assertRaises(OrderPersistenceError): await self.open(create=True)
        self.assertEqual(self.path.read_bytes(),before)

    async def test_reopen_cannot_change_initial_cash_or_costs(self):
        session = await self.open(create=True); await session.close()
        for kw in [dict(initial_balance=D('1')),dict(fee_pct=D('1')),dict(quote_currency='KRW')]:
            with self.subTest(kw=kw), self.assertRaises(ValueError): await self.open(**kw)

    async def test_foreign_book_state_fails_even_with_matching_checksum(self):
        session = await self.open(create=True,initial_balance=D('1000'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1); await self.buy(session)
        await session.close()
        with closing(sqlite3.connect(self.path)) as db:
            raw = db.execute('SELECT payload FROM paper_session').fetchone()[0]
            data = json.loads(raw);data['book']['balances']['USDT']='1000'
            raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True)
            db.execute('UPDATE paper_session SET payload=?,digest=?',(raw,hashlib.sha256(raw.encode()).hexdigest()))
            db.commit()
        with self.assertRaises(OrderPersistenceError): await self.open()

    async def test_impossible_partial_state_is_rejected(self):
        session=await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1);await self.buy(session)
        await session.close()
        with closing(sqlite3.connect(self.path)) as db:
            data=json.loads(db.execute('SELECT payload FROM paper_session').fetchone()[0])
            data['orders']['buy'].update(status='unknown',filled='0.5',remaining='1.5')
            raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True)
            db.execute('UPDATE paper_session SET payload=?,digest=?',(raw,hashlib.sha256(raw.encode()).hexdigest()));db.commit()
        with self.assertRaises(OrderPersistenceError): await self.open()

    async def test_untracked_fill_cannot_be_committed(self):
        session = await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        with self.assertRaises(ReconciliationRequired):
            await session.exchange.create_order('BTC/USDT','market','buy',D('1'),params={'clientOrderId':'orphan'})
        await session.close()
        self.assertEqual((await self.open()).exchange.snapshot()['trades'],[])

    async def test_second_owner_cannot_open_or_submit(self):
        session = await self.open(create=True)
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.assertEqual(session.exchange.snapshot()['trades'],[])

    async def test_views_are_detached_and_marks_survive_reopen(self):
        session = await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=123)
        snapshot=session.exchange.snapshot();snapshot['balances']['USDT']='1'
        self.assertNotEqual(session.exchange.snapshot(),snapshot)
        await session.close()
        restored=await self.open()
        self.assertEqual((await restored.exchange.fetch_ticker('BTC/USDT')).timestamp,123)

    async def test_order_id_conflict_cannot_change_account(self):
        session=await self.open(create=True);session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        await self.buy(session);before=session.exchange.snapshot()
        with self.assertRaises(ValueError): await self.buy(session,amount='3')
        self.assertEqual(session.exchange.snapshot(),before)

    async def test_reading_closed_handle_fails(self):
        session=await self.open(create=True);await session.close()
        with self.assertRaises(OrderPersistenceError): session.exchange.snapshot()

    async def test_quote_validation_precedes_mutation(self):
        session=await self.open(create=True);before=session.exchange.snapshot()
        for price in [D('0'),D('-1'),D('NaN'),D('Infinity')]:
            with self.subTest(price=str(price)),self.assertRaises(ValueError):
                session.exchange.set_price('BTC/USDT',price,timestamp=1)
        self.assertEqual(session.exchange.snapshot(),before)

    async def test_close_releases_store_after_polling_failure(self):
        session=await self.open(create=True)
        async def failed_poll(): raise OrderPersistenceError('poll-failed')
        session.manager._polling_task=asyncio.create_task(failed_poll())
        await asyncio.sleep(0)
        with self.assertRaises(OrderPersistenceError): await session.close()
        self.assertTrue(session._closed, 'poll failure must not strand file ownership')
        restored=await self.open()
        self.assertEqual(restored.exchange.snapshot()['trades'],[])

    async def test_enter_failure_releases_store(self):
        cls=session_class(); self.assertIsNotNone(cls)
        session=cls(self.path,create=True)
        self.sessions.append(session)
        with patch.object(session.exchange,'connect',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError): await session.__aenter__()
        self.assertTrue(session._closed, 'failed context entry must release store')
        await self.open()

    async def test_after_commit_exception_requires_reopen_and_preserves_fill(self):
        session=await self.open(create=True,initial_balance=D('1000'),fee_pct=D('.1'))
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        original=session._store._commit
        def fail_after_fill():
            data=json.loads(session._store._db.execute('SELECT payload FROM paper_session').fetchone()[0])
            original()
            if data['book']['trades']: raise OSError('uncertain acknowledgement')
        with patch.object(session._store,'_commit',side_effect=fail_after_fill):
            with self.assertRaises(OrderPersistenceError): await self.buy(session)
        with self.assertRaises(OrderPersistenceError): session.exchange.snapshot()
        await session.close()
        restored=await self.open()
        self.assertEqual(restored.exchange.snapshot()['balances']['USDT'],'799.8')
        self.assertEqual((await reconcile_order(restored.manager,'buy',allow_client_lookup=True)).status,OrderStatus.FILLED)
        self.assertEqual(len(restored.exchange.snapshot()['trades']),1)

    async def test_unknown_intent_cannot_bypass_manager_via_adapter(self):
        session=await self.open(create=True)
        session.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        # Post-admission transport failure is uncertain; missing local quotes
        # are now rejected BEFORE creating an intent by the session preflight.
        with patch.object(session.exchange, 'create_order', side_effect=TimeoutError('lost')):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy(session)
        with self.assertRaises(ReconciliationRequired):
            await session.exchange.create_order('BTC/USDT','market','buy',D('2'),params={'clientOrderId':'buy'})
        self.assertEqual(session.exchange.snapshot()['trades'],[])

    async def test_file_replacement_is_detected_before_read(self):
        session=await self.open(create=True)
        replacement=self.path.with_name('replacement')
        replacement.write_bytes(self.path.read_bytes());replacement.chmod(0o600)
        os.replace(replacement,self.path)
        with self.assertRaises(OrderPersistenceError): session.exchange.snapshot()

    async def test_changed_row_is_detected_during_open_handle(self):
        session=await self.open(create=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET revision=revision+1');db.commit()
        with self.assertRaises(OrderPersistenceError): session.exchange.snapshot()

    async def test_unexpected_schema_is_not_accepted(self):
        session=await self.open(create=True);await session.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE extra(value TEXT)');db.commit()
        with self.assertRaises(OrderPersistenceError): await self.open()

    async def test_linked_or_public_files_are_rejected(self):
        session=await self.open(create=True);await session.close()
        alias=self.path.with_name('alias.sqlite');alias.symlink_to(self.path)
        with self.assertRaises(OrderPersistenceError): await self.open(alias)
        alias.unlink();os.link(self.path,alias)
        with self.assertRaises(OrderPersistenceError): await self.open()
        alias.unlink();self.path.chmod(0o644)
        with self.assertRaises(OrderPersistenceError): await self.open()
        self.path.chmod(0o600)

    async def test_inflight_read_prevents_premature_close(self):
        session=await self.open(create=True)
        session.manager._inflight.add('read')
        with self.assertRaises(ReconciliationRequired): await session.close()
        self.assertFalse(session._closed)
        session.manager._inflight.clear()
        await session.close()

    async def test_no_account_arguments_are_read_from_environment(self):
        with patch.dict(os.environ, {'API_KEY':'do-not-use','KIS_APP_SECRET':'do-not-use'}):
            session=await self.open(create=True)
        self.assertNotIn('do-not-use',self.path.read_bytes().decode('latin1'))
        self.assertNotIn('api_key',session.exchange.snapshot())


# Separate processes: no shared object or manually restored account snapshot.
CRASH_CHILD = r'''
import asyncio,json,os,sys
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'packages/core'))
from trading_core.paper_session import PaperTradingSession
from trading_core.order_manager import OrderSide,OrderType
async def main():
    async with PaperTradingSession(sys.argv[2],create=True,initial_balance=D('1000'),fee_pct=D('.1')) as s:
        s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        original=s._store._commit
        def commit():
            row=json.loads(s._store._db.execute('SELECT payload FROM paper_session').fetchone()[0])
            count=len(row['book']['trades'])
            status=row['orders'].get('crash',{}).get('status')
            hit=count==1 and status==('filled' if 'ack' in sys.argv[3] else 'submitting')
            if hit and sys.argv[3].startswith('before'): os._exit(71)
            original()
            if hit and sys.argv[3].startswith('after'): os._exit(72)
        s._store._commit=commit
        await s.manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id='crash')
asyncio.run(main())
'''


class PaperSessionCrashTests(unittest.IsolatedAsyncioTestCase):
    async def check_crash(self, phase):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.sqlite'
            proc=subprocess.run([sys.executable,'-B','-c',CRASH_CHILD,str(ROOT),str(path),phase],
                                capture_output=True,text=True,timeout=15,
                                env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'})
            self.assertEqual(proc.returncode,71 if phase.startswith('before') else 72,proc.stderr)
            cls=session_class();self.assertIsNotNone(cls)
            async with cls(path) as restored:
                before=restored.exchange.snapshot()
                self.assertEqual(len(before['trades']),0 if phase=='before-fill' else 1)
                self.assertEqual(before['balances']['USDT'],'1000' if phase=='before-fill' else '799.8')
                order=await restored.manager.get_order('crash')
                if phase=='after-ack': self.assertEqual(order.status,OrderStatus.FILLED)
                else: self.assertEqual(order.status,OrderStatus.UNKNOWN)
                if phase=='before-fill':
                    with self.assertRaises(OrderOutcomeUnknown):
                        await reconcile_order(restored.manager,'crash',allow_client_lookup=True)
                    with self.assertRaises(ReconciliationRequired):
                        await restored.manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id='new')
                else:
                    result=await reconcile_order(restored.manager,'crash',allow_client_lookup=True)
                    self.assertEqual(result.status,OrderStatus.FILLED)
                # Identical ID never creates another simulated fill in any phase.
                await restored.manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id='crash')
                self.assertEqual(restored.exchange.snapshot(),before)

    async def test_crash_before_account_commit_is_unresolved_not_retried(self):
        await self.check_crash('before-fill')

    async def test_crash_after_account_commit_recovers_original_fill(self):
        await self.check_crash('after-fill')

    async def test_crash_before_ack_commit_recovers_original_fill(self):
        await self.check_crash('before-ack')

    async def test_crash_after_ack_commit_is_already_complete(self):
        await self.check_crash('after-ack')


class PaperSessionDemoTests(unittest.TestCase):
    def test_cli_uses_a_new_local_database_and_checks_exact_recovery(self):
        tool=ROOT/'tools/paper_session_demo.py'
        self.assertTrue(tool.is_file(), 'missing durable paper session demo')
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'demo.sqlite'
            cmd=[sys.executable,'-B',str(tool),'--database',str(target)]
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads(result.stdout)
            self.assertTrue(data['success'])
            self.assertEqual(data['trade_count'],1)
            self.assertEqual(data['cash'],'799.8')
            self.assertFalse(data['live_enabled'])
            before=target.read_bytes()
            again=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(again.returncode,0)
            self.assertEqual(target.read_bytes(),before)


if __name__ == "__main__": unittest.main()
