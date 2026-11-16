"""Credential-free write-ahead and restart tests of the original OrderManager."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packages/core'))
from trading_core import order_manager as om


class MemoryStore:
    def __init__(self):
        self.orders = {}
        self.history = []
        self.claimed = False
        self.closed = False
        self.fail_at = None

    def ensure_open(self):
        if self.closed:
            raise OSError('sensitive filesystem detail')

    def claim(self):
        self.ensure_open()
        if self.claimed:
            raise ValueError('store already claimed')
        self.claimed = True
        return deepcopy(self.orders)

    def save(self, orders):
        self.ensure_open()
        if self.fail_at == len(self.history):
            raise OSError('sensitive filesystem detail')
        self.orders = deepcopy(orders)
        self.history.append(deepcopy(orders))


class Adapter:
    def __init__(self, store):
        self.store = store
        self.creates = self.cancels = self.fetches = 0
        self.error = None
        self.response = dict(id='venue-1', status='open', filled='0')
        self.cancel_response = dict(id='venue-1', status='canceled', filled='0')
        self.fetch_response = dict(id='venue-1', status='filled', filled='2')

    async def create_order(self, **kw):
        self.creates += 1
        assert self.store.orders['one'].status is om.OrderStatus.SUBMITTING
        if self.error:
            raise self.error
        return deepcopy(self.response)

    async def cancel_order(self, *args):
        self.cancels += 1
        assert self.store.orders['one'].status is om.OrderStatus.CANCEL_PENDING
        assert self.store.orders['one'].cancel_requested
        return deepcopy(self.cancel_response)

    async def fetch_order(self, *args):
        self.fetches += 1
        return deepcopy(self.fetch_response)


class DurableManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIn('state_store', inspect.signature(om.OrderManager).parameters,
                      'OrderManager needs write-ahead storage barriers')
        self.store = MemoryStore()
        self.adapter = Adapter(self.store)
        self.manager = om.OrderManager(self.adapter, state_store=self.store)

    async def place(self, key='one'):
        return await self.manager.place_order('BTC/USDT', om.OrderSide.BUY,
            om.OrderType.LIMIT, D('2'), D('100'), client_order_id=key)

    async def test_persists_intent_before_transport_and_ack_before_return(self):
        result = await self.place()
        self.assertEqual([x['one'].status.value for x in self.store.history if 'one' in x],
                         ['submitting','open'])
        self.assertEqual(result.status, om.OrderStatus.OPEN)

    async def test_pre_send_storage_failure_blocks_all_future_io(self):
        self.store.fail_at = len(self.store.history)
        with self.assertRaises(om.OrderPersistenceError): await self.place()
        self.assertEqual(self.adapter.creates, 0)
        self.assertTrue(self.manager.storage_failed)
        self.store.fail_at = None
        with self.assertRaises(om.OrderPersistenceError): await self.place('two')
        with self.assertRaises(om.OrderPersistenceError): await self.manager._sync_orders()
        self.assertEqual(self.adapter.creates, 0)

    async def test_ack_storage_failure_is_not_rejection_or_resubmission(self):
        self.adapter.response = dict(id='venue-1', status='filled', filled='2')
        self.store.fail_at = len(self.store.history) + 1
        with self.assertRaises(om.OrderPersistenceError): await self.place()
        self.assertEqual(self.adapter.creates, 1)
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.SUBMITTING)
        self.assertEqual((await self.manager.get_order('one')).status, om.OrderStatus.FILLED)
        with self.assertRaises(om.OrderPersistenceError): await self.place()

    async def test_timeout_unknown_is_saved(self):
        self.adapter.error = TimeoutError('secret')
        with self.assertRaises(om.OrderOutcomeUnknown): await self.place()
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.UNKNOWN)

    async def test_task_cancellation_is_saved_and_not_swallowed(self):
        self.adapter.error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await self.place()
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.UNKNOWN)

    async def test_cancellation_is_preserved_even_when_save_fails(self):
        self.adapter.error = asyncio.CancelledError()
        self.store.fail_at = len(self.store.history) + 1
        with self.assertRaises(asyncio.CancelledError): await self.place()
        self.assertTrue(self.manager.storage_failed)

    async def test_cancel_intent_saved_before_call(self):
        await self.place()
        result = await self.manager.cancel_order('one')
        self.assertEqual(result.status, om.OrderStatus.CANCELED)
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.CANCELED)

    async def test_cancel_storage_failure_never_calls_adapter(self):
        await self.place()
        self.store.fail_at = len(self.store.history)
        with self.assertRaises(om.OrderPersistenceError): await self.manager.cancel_order('one')
        self.assertEqual(self.adapter.cancels, 0)

    async def test_readback_persists_fill(self):
        await self.place()
        await self.manager._sync_orders()
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.FILLED)

    async def test_readback_save_failure_propagates_and_latches(self):
        await self.place()
        self.store.fail_at = len(self.store.history)
        with self.assertRaises(om.OrderPersistenceError): await self.manager._sync_orders()
        self.assertTrue(self.manager.storage_failed)

    async def test_restore_persists_unknown_before_becoming_usable(self):
        await self.place()
        saved = deepcopy(self.store.orders)
        self.store.claimed = False
        restored = om.OrderManager(self.adapter, state_store=self.store)
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.UNKNOWN)
        self.assertEqual(saved['one'].status, om.OrderStatus.OPEN)
        with self.assertRaises(om.ReconciliationRequired):
            await restored.place_order('BTC/USDT', om.OrderSide.BUY, om.OrderType.MARKET,
                                      D('1'), client_order_id='new')
        duplicate = await restored.place_order('BTC/USDT', om.OrderSide.BUY, om.OrderType.LIMIT,
                                               D('2'), D('100'), client_order_id='one')
        self.assertEqual(duplicate.status, om.OrderStatus.UNKNOWN)
        self.assertEqual(self.adapter.creates, 1)
        await restored._sync_orders()
        self.assertEqual(self.store.orders['one'].status, om.OrderStatus.FILLED)

    async def test_persistent_restore_cannot_be_manually_cleared(self):
        with self.assertRaises(ValueError): self.manager.load_orders({})

    async def test_store_claim_is_single_owner(self):
        with self.assertRaises(om.OrderPersistenceError):
            om.OrderManager(self.adapter, state_store=self.store)

    async def test_closed_store_blocks_start_and_new_orders(self):
        self.store.closed = True
        with self.assertRaises(om.OrderPersistenceError): await self.manager.start()
        with self.assertRaises(om.OrderPersistenceError): await self.place()
        self.assertEqual(self.adapter.creates, 0)

    async def test_error_never_contains_storage_message(self):
        self.store.fail_at = len(self.store.history)
        try:
            await self.place()
        except om.OrderPersistenceError as exc:
            self.assertNotIn('sensitive', str(exc))
            self.assertTrue(exc.__suppress_context__)
        else: self.fail('expected storage error')


if __name__ == '__main__': unittest.main()
