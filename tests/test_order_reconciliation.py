"""Explicit read-only reconciliation, including real durable restart boundaries."""
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
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import (OrderManager, OrderSide, OrderType,
    OrderStatus, OrderOutcomeUnknown, OrderPersistenceError, ReconciliationRequired)
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange


def response(status='closed', filled='10', **changes):
    value = dict(id='venue-1', clientOrderId='one', symbol='BTC/USDT',
        side='buy', type='limit', amount='10', filled=filled,
        remaining=str(D('10') - D(filled)), price='100', status=status)
    value.update(changes)
    return value


class Adapter:
    def __init__(self):
        self.creates = self.cancels = self.lookups = self.fetches = 0
        self.value = response()
        self.error = None
        self.entered = asyncio.Event()
        self.release = None

    async def create_order(self, **kwargs):
        self.creates += 1
        raise TimeoutError('private-provider-error')

    async def cancel_order(self, *args):
        self.cancels += 1
        raise AssertionError('reconciliation must not cancel')

    async def fetch_order_by_client_id(self, key, symbol):
        self.lookups += 1
        self.args = key, symbol
        return await self._read()

    async def fetch_order(self, venue, symbol):
        self.fetches += 1
        self.args = venue, symbol
        return await self._read()

    async def _read(self):
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error:
            raise self.error
        return deepcopy(self.value)


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            self.api = importlib.import_module('trading_core.order_reconciliation')
        except ModuleNotFoundError:
            self.fail('explicit order reconciliation service is not implemented')
        self.adapter = Adapter()
        self.manager = OrderManager(self.adapter)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'orders.sqlite'
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    async def submit(self, manager=None, **extra):
        return await (manager or self.manager).place_order(
            'BTC/USDT', OrderSide.BUY, OrderType.LIMIT, D('10'), D('100'),
            client_order_id=extra.get('key', 'one'))

    async def unknown(self, manager=None):
        with self.assertRaises(OrderOutcomeUnknown):
            await self.submit(manager)

    async def reconcile(self, manager=None, **extra):
        return await self.api.reconcile_order(manager or self.manager, 'one',
            allow_client_lookup=True, **extra)

    async def test_missing_id_reconciles_explicitly_without_resubmission(self):
        await self.unknown()
        result = await self.reconcile()
        self.assertEqual((result.status, result.filled), (OrderStatus.FILLED, D('10')))
        self.assertEqual(self.adapter.args, ('one', 'BTC/USDT'))
        self.assertEqual((self.adapter.creates, self.adapter.cancels, self.adapter.lookups), (1,0,1))

    async def test_missing_id_requires_opt_in(self):
        await self.unknown()
        with self.assertRaises(ReconciliationRequired):
            await self.api.reconcile_order(self.manager, 'one')
        self.assertEqual(self.adapter.lookups, 0)

    async def test_unsupported_capability_stays_unknown(self):
        await self.unknown(); self.adapter.fetch_order_by_client_id = None
        with self.assertRaises(ReconciliationRequired): await self.reconcile()
        self.assertEqual((await self.manager.get_order('one')).status, OrderStatus.UNKNOWN)

    async def test_not_found_is_not_rejection_and_keeps_admission_blocked(self):
        await self.unknown(); self.adapter.value = None
        with self.assertRaises(OrderOutcomeUnknown): await self.reconcile()
        self.assertEqual((await self.manager.get_order('one')).status, OrderStatus.UNKNOWN)
        with self.assertRaises(ReconciliationRequired): await self.submit(key='two')
        self.assertEqual(self.adapter.creates, 1)

    async def test_malformed_and_ambiguous_results_do_not_bind(self):
        await self.unknown()
        for value in ({}, [], [response()], [response(), response()], {'found': False}):
            with self.subTest(value=value):
                self.adapter.value = value
                with self.assertRaises(OrderOutcomeUnknown): await self.reconcile()
                self.assertIsNone((await self.manager.get_order('one')).exchange_order_id)

    async def test_full_identity_and_quantity_are_required(self):
        await self.unknown()
        for field in ('clientOrderId','symbol','side','type','amount','remaining','price'):
            with self.subTest(field=field):
                self.adapter.value = response(); self.adapter.value.pop(field)
                with self.assertRaises(OrderOutcomeUnknown): await self.reconcile()
                self.assertIsNone((await self.manager.get_order('one')).exchange_order_id)

    async def test_conflicting_identity_amount_price_rejected_atomically(self):
        await self.unknown()
        for field, bad in (('clientOrderId','wrong'),('symbol','ETH/USDT'),
            ('side','sell'),('type','market'),('amount','11'),('remaining','1'),('price','99')):
            with self.subTest(field=field):
                self.adapter.value=response(**{field:bad})
                with self.assertRaises(OrderOutcomeUnknown): await self.reconcile()
                current=await self.manager.get_order('one')
                self.assertEqual((current.filled,current.exchange_order_id),(D('0'),None))

    async def test_known_id_uses_existing_fetch_not_client_lookup(self):
        await self.unknown(); self.adapter.value=response('open','3')
        await self.reconcile()
        self.adapter.value=response('closed','10')
        result=await self.api.reconcile_order(self.manager,'one')
        self.assertEqual(result.status,OrderStatus.FILLED)
        self.assertEqual((self.adapter.lookups,self.adapter.fetches),(1,1))
        self.assertEqual(self.adapter.args,('venue-1','BTC/USDT'))

    async def test_terminal_reconcile_is_detached_and_does_not_read_again(self):
        await self.unknown(); await self.reconcile()
        result=await self.reconcile(); result.amount=D('999')
        self.assertEqual(self.adapter.lookups,1)
        self.assertEqual((await self.manager.get_order('one')).amount,D('10'))

    async def test_automatic_polling_does_not_opt_in_missing_ids(self):
        await self.unknown(); await self.manager._sync_orders()
        self.assertEqual((self.adapter.fetches,self.adapter.lookups),(0,0))

    async def test_duplicate_reconciliation_and_poll_do_not_overlap(self):
        await self.unknown(); self.adapter.release=asyncio.Event()
        task=asyncio.create_task(self.reconcile())
        await self.adapter.entered.wait()
        try:
            with self.assertRaises(ReconciliationRequired): await self.reconcile()
            await self.manager._sync_orders()
            self.assertEqual((self.adapter.lookups,self.adapter.fetches),(1,0))
            duplicate=await self.submit()
            self.assertEqual(duplicate.status,OrderStatus.UNKNOWN)
        finally:
            self.adapter.release.set(); await task
        self.assertEqual(self.adapter.creates,1)

    async def test_caller_cancel_preserves_unknown_and_releases_ownership(self):
        await self.unknown(); self.adapter.release=asyncio.Event()
        task=asyncio.create_task(self.reconcile()); await self.adapter.entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((await self.manager.get_order('one')).status,OrderStatus.UNKNOWN)
        self.adapter.release.set()
        for _ in range(3): await asyncio.sleep(0)
        self.assertEqual((await self.reconcile()).status,OrderStatus.FILLED)

    async def test_timeout_preserves_unknown(self):
        await self.unknown(); self.adapter.release=asyncio.Event()
        with self.assertRaises(OrderOutcomeUnknown): await self.reconcile(timeout_seconds=.01)
        self.assertEqual((await self.manager.get_order('one')).status,OrderStatus.UNKNOWN)
        for _ in range(3): await asyncio.sleep(0)
        self.assertFalse(self.manager._inflight)

    async def test_response_after_swallowed_timeout_is_not_accepted(self):
        await self.unknown()
        async def late(*args):
            try: await asyncio.sleep(1)
            except asyncio.CancelledError: return response()
        self.adapter.fetch_order_by_client_id=late
        with self.assertRaises(OrderOutcomeUnknown): await self.reconcile(timeout_seconds=.01)
        self.assertIsNone((await self.manager.get_order('one')).exchange_order_id)

    async def test_caller_cancellation_cannot_be_swallowed_by_adapter(self):
        await self.unknown()
        entered=asyncio.Event()
        async def swallowing(*args):
            entered.set()
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: return response()
        self.adapter.fetch_order_by_client_id=swallowing
        task=asyncio.create_task(self.reconcile())
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((await self.manager.get_order('one')).status,OrderStatus.UNKNOWN)

    async def test_timeout_holds_ownership_until_suppressed_cancellation_finishes(self):
        await self.unknown(); cleanup=asyncio.Event(); release=asyncio.Event()
        async def resistant(*args):
            try: await asyncio.sleep(10)
            except asyncio.CancelledError:
                cleanup.set(); await release.wait(); return response()
        self.adapter.fetch_order_by_client_id=resistant
        try:
            with self.assertRaises(OrderOutcomeUnknown): await self.reconcile(timeout_seconds=.01)
            await cleanup.wait()
            self.assertIn('one',self.manager._inflight)
            with self.assertRaises(ReconciliationRequired): await self.reconcile()
            await self.manager._sync_orders()
        finally:
            release.set()
            for _ in range(4): await asyncio.sleep(0)
        self.assertFalse(self.manager._inflight)
        self.assertEqual((await self.manager.get_order('one')).status,OrderStatus.UNKNOWN)

    async def test_adapter_replacement_during_read_is_rejected(self):
        await self.unknown();self.adapter.release=asyncio.Event()
        task=asyncio.create_task(self.reconcile());await self.adapter.entered.wait()
        self.manager.exchange=Adapter();self.adapter.release.set()
        with self.assertRaises(OrderOutcomeUnknown): await task
        self.assertIsNone((await self.manager.get_order('one')).exchange_order_id)

    async def test_duplicate_venue_id_cannot_bind_two_intents(self):
        await self.unknown();self.adapter.value=response('open','3');await self.reconcile()
        with self.assertRaises(OrderOutcomeUnknown): await self.submit(key='two')
        self.adapter.value=response(clientOrderId='two')
        with self.assertRaises(OrderOutcomeUnknown):
            await self.api.reconcile_order(self.manager,'two',allow_client_lookup=True)
        self.assertIsNone((await self.manager.get_order('two')).exchange_order_id)

    async def test_invalid_options_rejected_before_io(self):
        await self.unknown()
        for value in (True,0,-1,31,float('nan'),float('inf'),'10',None):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                await self.reconcile(timeout_seconds=value)
        with self.assertRaises(ValueError):
            await self.api.reconcile_order(self.manager,'one',allow_client_lookup=1)
        self.assertEqual(self.adapter.lookups,0)

    async def test_adapter_error_is_redacted(self):
        await self.unknown(); self.adapter.error=RuntimeError('secret-account-token')
        with self.assertRaises(OrderOutcomeUnknown) as caught: await self.reconcile()
        self.assertNotIn('secret',str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    async def test_cancel_intent_survives_readback(self):
        await self.unknown(); self.adapter.value=response('open','3'); await self.reconcile()
        # Existing cancel path persists intention before the deliberately failed response.
        with self.assertRaises(OrderOutcomeUnknown): await self.manager.cancel_order('one')
        self.adapter.value=response('open','4'); result=await self.reconcile()
        self.assertEqual(result.status,OrderStatus.CANCEL_PENDING)
        self.assertTrue(result.cancel_requested)
        self.adapter.value=response('closed','10')
        self.assertEqual((await self.reconcile()).status,OrderStatus.FILLED)
        self.assertEqual(self.adapter.cancels,1)

    async def test_fill_regression_and_wrong_venue_do_not_replace_known_values(self):
        await self.unknown(); self.adapter.value=response('open','4'); await self.reconcile()
        for value in (response('open','2'),response(id='wrong-venue')):
            self.adapter.value=value
            with self.assertRaises(OrderOutcomeUnknown): await self.reconcile()
            current=await self.manager.get_order('one')
            self.assertEqual((current.filled,current.exchange_order_id),(D('4'),'venue-1'))

    async def test_restart_uses_durable_lookup_result(self):
        with SQLiteOrderStateStore(self.db,scope='paper',create=True) as store:
            manager=OrderManager(self.adapter,state_store=store); await self.unknown(manager)
        with SQLiteOrderStateStore(self.db,scope='paper') as store:
            manager=OrderManager(self.adapter,state_store=store)
            self.assertEqual((await self.reconcile(manager)).status,OrderStatus.FILLED)
        with SQLiteOrderStateStore(self.db,scope='paper') as store:
            manager=OrderManager(self.adapter,state_store=store)
            self.assertEqual((await manager.get_order('one')).status,OrderStatus.FILLED)
            await self.reconcile(manager)
        self.assertEqual((self.adapter.creates,self.adapter.lookups),(1,1))

    async def test_failed_persistence_latches_and_leaves_old_durable_state(self):
        with SQLiteOrderStateStore(self.db,scope='paper',create=True) as store:
            manager=OrderManager(self.adapter,state_store=store); await self.unknown(manager)
            with patch.object(store,'_commit',side_effect=OSError('disk-error')):
                with self.assertRaises(OrderPersistenceError): await self.reconcile(manager)
            self.assertTrue(manager.storage_failed)
            with self.assertRaises(OrderPersistenceError): await self.reconcile(manager)
        with SQLiteOrderStateStore(self.db,scope='paper') as store:
            manager=OrderManager(self.adapter,state_store=store)
            self.assertEqual((await manager.get_order('one')).status,OrderStatus.UNKNOWN)
            self.assertEqual((await self.reconcile(manager)).status,OrderStatus.FILLED)
        self.assertEqual(self.adapter.creates,1)

    async def test_closed_store_prevents_lookup(self):
        store=SQLiteOrderStateStore(self.db,scope='paper',create=True)
        manager=OrderManager(self.adapter,state_store=store); await self.unknown(manager);store.close()
        with self.assertRaises(OrderPersistenceError): await self.reconcile(manager)
        self.assertEqual(self.adapter.lookups,0)

    async def test_unknown_order_key_does_not_lookup(self):
        with self.assertRaises(ValueError): await self.reconcile()
        self.assertEqual(self.adapter.lookups,0)


class PaperLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_closed_order_and_absent_do_not_mutate_account(self):
        exchange=PaperExchange(initial_balance=D('1000'),fee_pct=D('.1'))
        self.assertTrue(callable(getattr(exchange,'fetch_order_by_client_id',None)),
                        'paper client-ID lookup missing')
        await exchange.connect();exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        before=exchange.snapshot()
        self.assertIsNone(await exchange.fetch_order_by_client_id('one','BTC/USDT'))
        self.assertEqual(before,exchange.snapshot())
        order=await exchange.create_order('BTC/USDT','market','buy',D('2'),params={'clientOrderId':'one'})
        before=exchange.snapshot();found=await exchange.fetch_order_by_client_id('one','BTC/USDT')
        self.assertEqual(found,order);found['filled']='999'
        self.assertEqual(before,exchange.snapshot())
        self.assertIsNone(await exchange.fetch_order_by_client_id('one','ETH/USDT'))
        restored=PaperExchange.from_snapshot(before);await restored.connect()
        self.assertEqual(await restored.fetch_order_by_client_id('one','BTC/USDT'),order)
        await exchange.disconnect()
        with self.assertRaises(RuntimeError): await exchange.fetch_order_by_client_id('one','BTC/USDT')



class ReconciliationProcessTests(unittest.TestCase):
    """Use real child exit around SQLite reconciliation commit; no external API."""
    def test_process_exit_before_and_after_reconciliation_commit(self):
        script = r'''
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'packages/core'))
from trading_core.order_manager import OrderManager
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange
from trading_core.order_reconciliation import reconcile_order
async def go():
    venue=PaperExchange.from_snapshot(json.loads(Path(sys.argv[3]).read_text()))
    await venue.connect()
    with SQLiteOrderStateStore(sys.argv[2],scope='paper') as store:
        manager=OrderManager(venue,state_store=store)
        commit=store._commit
        def failpoint():
            if sys.argv[4]=='after': commit()
            os._exit(77)
        store._commit=failpoint
        await reconcile_order(manager,'one',allow_client_lookup=True)
asyncio.run(go())
'''
        async def prepare(db):
            class LostAck(PaperExchange):
                async def create_order(self,*args,**kwargs):
                    await super().create_order(*args,**kwargs)
                    raise TimeoutError()
            venue=LostAck(initial_balance=D('1000'),fee_pct=D('.1'))
            await venue.connect();venue.set_price('BTC/USDT',D('100'),timestamp=1)
            with SQLiteOrderStateStore(db,scope='paper',create=True) as store:
                manager=OrderManager(venue,state_store=store)
                with self.assertRaises(OrderOutcomeUnknown):
                    await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('1'),client_order_id='one')
            return venue.snapshot()
        async def recover(db,snapshot,when):
            api=importlib.import_module('trading_core.order_reconciliation')
            venue=PaperExchange.from_snapshot(snapshot);await venue.connect()
            with SQLiteOrderStateStore(db,scope='paper') as store:
                manager=OrderManager(venue,state_store=store)
                expected=OrderStatus.UNKNOWN if when=='before' else OrderStatus.FILLED
                self.assertEqual((await manager.get_order('one')).status,expected)
                result=await api.reconcile_order(manager,'one',allow_client_lookup=True)
                self.assertEqual(result.status,OrderStatus.FILLED)
                duplicate=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('1'),client_order_id='one')
                self.assertEqual(duplicate.filled,D('1'))
                self.assertEqual(venue.snapshot(),snapshot)
                self.assertEqual(len(await venue.fetch_my_trades()),1)
        for when in ('before','after'):
            with self.subTest(when=when), tempfile.TemporaryDirectory() as directory:
                db=Path(directory)/'orders.sqlite';snapshot=asyncio.run(prepare(db))
                venue_file=Path(directory)/'synthetic-venue.json'
                venue_file.write_text(json.dumps(snapshot));venue_file.chmod(0o600)
                result=subprocess.run([sys.executable,'-B','-c',script,str(ROOT),str(db),str(venue_file),when],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,
                    env={'PATH':os.defpath,'LANG':'C.UTF-8'})
                self.assertEqual(result.returncode,77,result.stderr.decode())
                asyncio.run(recover(db,snapshot,when))

    def test_demo_cli_runs_offline_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'demo.sqlite'
            command=[sys.executable,'-B',str(ROOT/'tools/order_reconcile_demo.py'),'--database',str(db)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=15,
                                  env={'PATH':os.defpath,'LANG':'C.UTF-8'})
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout)
            self.assertEqual((value['fill_count'],value['cash'],value['fees']),(1,'899.9','0.1'))
            self.assertFalse(value['live_enabled']);self.assertTrue(value['durable_restart_verified'])
            before=db.read_bytes()
            result=subprocess.run(command,capture_output=True,text=True,timeout=15,
                                  env={'PATH':os.defpath,'LANG':'C.UTF-8'})
            self.assertEqual(result.returncode,2)
            self.assertEqual(db.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
