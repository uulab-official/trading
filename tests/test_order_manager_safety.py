"""Local protocol regressions; no credentials, network or real orders."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D, localcontext
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packages/core'))
from trading_core.order_manager import Order, OrderManager, OrderSide, OrderStatus, OrderType
from trading_core.paper_exchange import PaperExchange


def reply(status='open', filled='0', **extra):
    return dict(id='venue-1', clientOrderId='one', symbol='BTC/USDT',
                side='buy', type='limit', amount='10', filled=filled,
                remaining=str(D('10')-D(filled)), status=status, **extra)


class Adapter:
    def __init__(self):
        self.create_reply = reply()
        self.cancel_reply = reply('canceled')
        self.fetch_reply = reply()
        self.create_error = self.cancel_error = self.fetch_error = None
        self.creates = self.cancels = self.fetches = 0
        self.entered = asyncio.Event()
        self.release = None
        self.cancel_entered = asyncio.Event()
        self.cancel_release = None
        self.fetch_entered = asyncio.Event()
        self.fetch_release = None

    async def create_order(self, **kwargs):
        self.creates += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.create_error:
            raise self.create_error
        return deepcopy(self.create_reply)

    async def cancel_order(self, *args):
        self.cancels += 1
        self.cancel_entered.set()
        if self.cancel_release is not None:
            await self.cancel_release.wait()
        if self.cancel_error:
            raise self.cancel_error
        return deepcopy(self.cancel_reply)

    async def fetch_order(self, *args):
        self.fetches += 1
        self.fetch_entered.set()
        if self.fetch_release is not None:
            await self.fetch_release.wait()
        if self.fetch_error:
            raise self.fetch_error
        return deepcopy(self.fetch_reply)


class ManagerSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = Adapter()
        self.manager = OrderManager(self.adapter)

    async def place(self, **overrides):
        values = dict(symbol='BTC/USDT', side=OrderSide.BUY,
                      order_type=OrderType.LIMIT, amount=D('10'), price=D('100'),
                      client_order_id='one', strategy_id='test')
        values.update(overrides)
        return await self.manager.place_order(**values)

    async def state(self):
        return await self.manager.get_order('one')

    async def test_timeout_is_unknown_not_rejected(self):
        self.adapter.create_error = TimeoutError('sensitive-adapter-text')
        with self.assertRaises(Exception): await self.place()
        self.assertEqual((await self.state()).status.value, 'unknown')

    async def test_unknown_blocks_new_id_without_resubmission(self):
        self.adapter.create_error = TimeoutError()
        with self.assertRaises(Exception): await self.place()
        self.adapter.create_error = None
        with self.assertRaises(RuntimeError): await self.place(client_order_id='two')
        self.assertEqual(self.adapter.creates, 1)

    async def test_duplicate_after_unknown_only_returns_unknown(self):
        self.adapter.create_error = TimeoutError()
        with self.assertRaises(Exception): await self.place()
        self.adapter.create_error = None
        result = await self.place()
        self.assertEqual(result.status.value, 'unknown')
        self.assertEqual(self.adapter.creates, 1)

    async def test_task_cancel_after_adapter_entered_preserves_unknown(self):
        self.adapter.release = asyncio.Event()
        task = asyncio.create_task(self.place())
        await self.adapter.entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((await self.state()).status.value, 'unknown')

    async def test_changed_duplicate_intent_rejected(self):
        await self.place()
        with self.assertRaises(ValueError): await self.place(amount=D('11'))
        self.assertEqual(self.adapter.creates, 1)

    async def test_duplicate_pending_exposes_submitting_not_acceptance(self):
        self.adapter.release = asyncio.Event()
        task = asyncio.create_task(self.place())
        await self.adapter.entered.wait()
        try:
            self.assertEqual((await self.place()).status.value, 'submitting')
            with self.assertRaises(ValueError): await self.place(price=D('99'))
            self.assertEqual(self.adapter.creates, 1)
        finally:
            self.adapter.release.set(); await task

    async def test_cancel_reply_open_is_not_cancelled(self):
        await self.place()
        self.adapter.cancel_reply = reply('open','3')
        result = await self.manager.cancel_order('one')
        self.assertEqual(result.status.value, 'cancel_pending')
        self.assertEqual(result.filled, D('3'))
        with self.assertRaises(RuntimeError): await self.place(client_order_id='two')

    async def test_cancel_reply_filled_wins(self):
        await self.place()
        self.adapter.cancel_reply = reply('closed','10')
        result = await self.manager.cancel_order('one')
        self.assertEqual(result.status, OrderStatus.FILLED)
        self.assertEqual(result.remaining, 0)

    async def test_cancel_timeout_retains_known_fill_and_id(self):
        self.adapter.create_reply = reply('open','4')
        await self.place(); self.adapter.cancel_error = TimeoutError()
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        result = await self.state()
        self.assertEqual((result.status.value,result.filled,result.exchange_order_id),('unknown',D('4'),'venue-1'))

    async def test_empty_cancel_reply_is_unknown(self):
        await self.place(); self.adapter.cancel_reply = {}
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        self.assertEqual((await self.state()).status.value, 'unknown')

    async def test_duplicate_cancellation_sends_once(self):
        await self.place(); self.adapter.cancel_release = asyncio.Event()
        task = asyncio.create_task(self.manager.cancel_order('one'))
        await self.adapter.cancel_entered.wait()
        try:
            try:
                result = await asyncio.wait_for(self.manager.cancel_order('one'), .15)
            except asyncio.TimeoutError:
                self.fail('duplicate cancellation issued a second blocking adapter call')
            self.assertEqual(result.status.value, 'cancel_pending')
            self.assertEqual(self.adapter.cancels, 1)
        finally:
            self.adapter.cancel_release.set(); await task

    async def test_cancel_task_cancellation_is_unknown(self):
        await self.place(); self.adapter.cancel_release = asyncio.Event()
        task = asyncio.create_task(self.manager.cancel_order('one'))
        await self.adapter.cancel_entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((await self.state()).status.value, 'unknown')

    async def test_unknown_reconciles_by_read_only_known_id(self):
        await self.place(); self.adapter.cancel_error = TimeoutError()
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        self.adapter.fetch_reply = reply('canceled','4')
        await self.manager._sync_orders()
        value = await self.state()
        self.assertEqual((value.status,value.filled), (OrderStatus.CANCELED,D('4')))
        self.assertEqual((self.adapter.creates,self.adapter.cancels,self.adapter.fetches),(1,1,1))

    async def test_missing_id_never_guesses_or_retries(self):
        self.adapter.create_error = TimeoutError()
        with self.assertRaises(Exception): await self.place()
        await self.manager._sync_orders()
        self.assertEqual(self.adapter.fetches, 0)
        self.assertEqual((await self.state()).status.value,'unknown')

    async def test_pending_cancel_polls_remaining_fills(self):
        await self.place(); self.adapter.cancel_reply=reply('open','3')
        await self.manager.cancel_order('one')
        self.adapter.fetch_reply=reply('closed','10')
        await self.manager._sync_orders()
        self.assertEqual((await self.state()).status, OrderStatus.FILLED)

    async def test_regressing_fill_preserves_last_known_quantity(self):
        self.adapter.create_reply=reply('open','4'); await self.place()
        self.adapter.fetch_reply=reply('open','2'); await self.manager._sync_orders()
        current=await self.state()
        self.assertEqual((current.status.value,current.filled,current.remaining),('unknown',D('4'),D('6')))

    async def test_poll_does_not_overlap_cancel(self):
        await self.place(); self.adapter.cancel_release=asyncio.Event()
        task=asyncio.create_task(self.manager.cancel_order('one'))
        await self.adapter.cancel_entered.wait()
        try:
            await self.manager._sync_orders()
            self.assertEqual(self.adapter.fetches,0)
        finally:
            self.adapter.cancel_release.set(); await task

    async def test_returned_order_mutation_does_not_change_internal_state(self):
        created=await self.place(); created.status=OrderStatus.REJECTED; created.amount=D('999')
        self.assertEqual((await self.state()).amount,D('10'))
        self.assertEqual((await self.state()).status,OrderStatus.OPEN)

    async def test_getters_are_deep_copies(self):
        await self.place()
        (await self.state()).filled=D('9')
        self.manager.get_all_orders()['one'].status=OrderStatus.REJECTED
        self.assertEqual((await self.state()).status, OrderStatus.OPEN)
        (await self.manager.get_open_orders())[0].amount=D('999')
        (await self.manager.get_orders_by_strategy('test'))[0].price=D('1')
        item=await self.state()
        self.assertEqual((item.filled,item.amount,item.price),(D('0'),D('10'),D('100')))

    async def test_restore_active_order_requires_reconciliation(self):
        await self.place(); saved=self.manager.get_all_orders()
        restored=OrderManager(self.adapter); restored.load_orders(saved)
        self.assertEqual((await restored.get_order('one')).status.value,'unknown')
        await restored._sync_orders()
        self.assertEqual((await restored.get_order('one')).status,OrderStatus.OPEN)

    async def test_restore_copies_and_cannot_overwrite_active_manager(self):
        await self.place(); saved=self.manager.get_all_orders()
        restored=OrderManager(self.adapter); restored.load_orders(saved)
        saved['one'].filled=D('9')
        self.assertEqual((await restored.get_order('one')).filled,D('0'))
        with self.assertRaises(ValueError): restored.load_orders({})

    async def test_invalid_restore_is_atomic(self):
        await self.place(); saved=self.manager.get_all_orders()
        saved['two']=deepcopy(saved['one']); saved['two'].client_order_id='two'; saved['two'].filled=D('11')
        restored=OrderManager(self.adapter)
        with self.assertRaises(ValueError): restored.load_orders(saved)
        self.assertEqual(restored.get_all_orders(),{})

    async def test_unknown_status_never_defaults_to_open(self):
        self.adapter.create_reply=reply('provider-new-status')
        with self.assertRaises(Exception): await self.place()
        self.assertEqual((await self.state()).status.value,'unknown')

    async def test_rejected_response_is_explicit_zero_fill_only(self):
        self.adapter.create_reply=reply('rejected')
        self.assertEqual((await self.place()).status,OrderStatus.REJECTED)
        self.assertEqual(self.adapter.creates,1)

    async def test_closed_requires_full_quantity(self):
        self.adapter.create_reply=reply('closed','3')
        with self.assertRaises(Exception): await self.place()
        self.assertEqual((await self.state()).status.value,'unknown')

    async def test_overfill_is_rejected_without_partial_mutation(self):
        self.adapter.create_reply=reply('closed','11')
        with self.assertRaises(Exception): await self.place()
        value=await self.state()
        self.assertEqual((value.filled,value.remaining),(D('0'),D('10')))

    async def test_foreign_venue_id_is_not_adopted_on_poll(self):
        await self.place(); self.adapter.fetch_reply=reply(); self.adapter.fetch_reply['id']='foreign'
        await self.manager._sync_orders()
        value=await self.state()
        self.assertEqual((value.status.value,value.exchange_order_id),('unknown','venue-1'))

    async def test_get_open_orders_includes_unresolved_orders(self):
        self.adapter.create_error=TimeoutError()
        with self.assertRaises(Exception): await self.place()
        self.assertEqual(len(await self.manager.get_open_orders()),1)

    async def test_raw_adapter_exception_is_not_logged_or_returned(self):
        self.adapter.create_error=RuntimeError('SECRET-ACCOUNT-DATA')
        with self.assertLogs('trading_core.order_manager', level='WARNING') as logs:
            with self.assertRaises(Exception) as caught: await self.place()
        self.assertNotIn('SECRET-ACCOUNT-DATA',str(caught.exception))
        self.assertNotIn('SECRET-ACCOUNT-DATA',' '.join(logs.output))

    async def test_start_is_idempotent_and_stop_cleans_task(self):
        await self.manager.start(); first=self.manager._polling_task
        await self.manager.start(); second=self.manager._polling_task
        try: self.assertIs(first,second)
        finally:
            await self.manager.stop()
            if first is not second:
                first.cancel()
                try: await first
                except asyncio.CancelledError: pass
        self.assertIsNone(self.manager._polling_task)




class ManagerBoundaryTests(ManagerSafetyTests):
    # Only the additional methods are discovered from this subclass below;
    # inherited test names are removed after the class definition.
    async def test_pending_cancel_survives_open_poll_until_terminal(self):
        await self.place(); self.adapter.cancel_reply=reply('open','2')
        await self.manager.cancel_order('one')
        self.adapter.fetch_reply=reply('open','4')
        await self.manager._sync_orders()
        self.assertEqual((await self.state()).status.value,'cancel_pending')
        await self.manager.cancel_order('one')
        self.assertEqual(self.adapter.cancels,1)

    async def test_uncertain_cancel_survives_readback_and_restore(self):
        await self.place(); self.adapter.cancel_error=TimeoutError()
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        restored=OrderManager(self.adapter); restored.load_orders(self.manager.get_all_orders())
        self.adapter.fetch_reply=reply('open','2')
        await restored._sync_orders()
        self.assertEqual((await restored.get_order('one')).status.value,'cancel_pending')
        await restored.cancel_order('one')
        self.assertEqual(self.adapter.cancels,1)

    async def test_two_intents_cannot_adopt_same_venue_identifier(self):
        await self.place()
        self.adapter.create_reply['clientOrderId']='two'
        with self.assertRaises(Exception): await self.place(client_order_id='two')
        self.assertEqual((await self.manager.get_order('two')).status.value,'unknown')
        self.assertIsNone((await self.manager.get_order('two')).exchange_order_id)

    async def test_malformed_response_matrix(self):
        cases=[None,[],{},reply(),reply(),reply(),reply(),reply(),reply(),reply()]
        cases[3].pop('filled');cases[4]['filled']='NaN';cases[5]['filled']='-1'
        cases[6]['remaining']='9';cases[7]['clientOrderId']='other'
        cases[8]['symbol']='ETH/USDT';cases[9]['side']='sell'
        for value in cases:
            with self.subTest(value=value):
                self.manager=OrderManager(self.adapter);self.adapter.create_reply=value
                with self.assertRaises(Exception):await self.place()
                current=await self.state()
                self.assertEqual((current.status.value,current.filled,current.remaining),('unknown',D('0'),D('10')))

    async def test_invalid_intent_rejected_before_adapter(self):
        cases=[dict(amount=x) for x in (True,False,None,'NaN','Infinity','-1','0','1e-31','1e31')]
        cases += [dict(side='buy'),dict(order_type='limit'),dict(price=None),
                  dict(client_order_id=''),dict(client_order_id='one\n'),dict(strategy_id='bad\x00')]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError): await self.place(**kwargs)
        self.assertEqual(self.adapter.creates,0)
        self.assertEqual(self.manager.get_all_orders(),{})

    async def test_duplicate_identity_includes_all_request_fields(self):
        await self.place()
        for kwargs in (dict(symbol='ETH/USDT'),dict(side=OrderSide.SELL),
                       dict(order_type=OrderType.MARKET),dict(price=D('99')),dict(strategy_id='other')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):await self.place(**kwargs)
        self.assertEqual(self.adapter.creates,1)
        self.assertEqual((await self.place(amount=D('10.00'))).amount,D('10'))

    async def test_quantity_conservation_ignores_ambient_decimal_precision(self):
        self.adapter.create_reply=reply('open','0.123456789123456789')
        with localcontext() as ctx:
            ctx.prec=3
            current=await self.place()
        self.assertEqual(current.remaining,D('9.876543210876543211'))

    async def test_poll_exception_marks_unknown_without_secret_leak(self):
        await self.place();self.adapter.fetch_error=RuntimeError('SECRET-TOKEN')
        with self.assertLogs('trading_core.order_manager',level='WARNING') as logs:
            await self.manager._sync_orders()
        self.assertEqual((await self.state()).status.value,'unknown')
        self.assertNotIn('SECRET-TOKEN',' '.join(logs.output))

    async def test_readback_cancellation_is_propagated_and_releases_ownership(self):
        await self.place();self.adapter.fetch_release=asyncio.Event()
        task=asyncio.create_task(self.manager._sync_orders())
        await self.adapter.fetch_entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((await self.state()).status.value,'unknown')
        self.assertEqual(self.manager._inflight,set())
        self.adapter.fetch_release=None
        await self.manager._sync_orders()
        self.assertEqual((await self.state()).status,OrderStatus.OPEN)

    async def test_concurrent_polls_issue_only_one_read(self):
        await self.place();self.adapter.fetch_release=asyncio.Event()
        task=asyncio.create_task(self.manager._sync_orders())
        await self.adapter.fetch_entered.wait()
        try:
            await self.manager._sync_orders()
            self.assertEqual(self.adapter.fetches,1)
        finally:self.adapter.fetch_release.set();await task

    async def test_no_new_order_while_another_submission_is_inflight(self):
        self.adapter.release=asyncio.Event();task=asyncio.create_task(self.place())
        await self.adapter.entered.wait()
        try:
            with self.assertRaises(RuntimeError):await self.place(client_order_id='two')
            self.assertEqual(self.adapter.creates,1)
        finally:self.adapter.release.set();await task

    async def test_terminal_restore_is_idempotent_without_network(self):
        self.adapter.create_reply=reply('closed','10');await self.place()
        restored=OrderManager(self.adapter);restored.load_orders(self.manager.get_all_orders())
        self.manager=restored
        self.assertEqual((await self.place()).status,OrderStatus.FILLED)
        await self.manager.cancel_order('one');await self.manager._sync_orders()
        self.assertEqual((self.adapter.creates,self.adapter.cancels,self.adapter.fetches),(1,0,0))

    async def test_cancel_rejection_is_not_rejection_of_original_order(self):
        await self.place(); self.adapter.cancel_reply=reply('rejected')
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        self.assertEqual((await self.state()).status.value,'unknown')
        self.assertTrue((await self.state()).cancel_requested)

    async def test_poll_cannot_turn_an_acknowledged_order_into_rejected(self):
        await self.place(); self.adapter.fetch_reply=reply('rejected')
        await self.manager._sync_orders()
        self.assertEqual((await self.state()).status.value,'unknown')

    async def test_fully_filled_cancelled_response_is_inconsistent(self):
        await self.place(); self.adapter.cancel_reply=reply('canceled','10')
        with self.assertRaises(Exception): await self.manager.cancel_order('one')
        self.assertEqual((await self.state()).status.value,'unknown')

    async def test_cancel_during_readback_does_not_send_overlapping_write(self):
        await self.place(); self.adapter.fetch_release=asyncio.Event()
        task=asyncio.create_task(self.manager._sync_orders())
        await self.adapter.fetch_entered.wait()
        try:
            current=await self.manager.cancel_order('one')
            self.assertEqual(current.status,OrderStatus.OPEN)
            self.assertEqual(self.adapter.cancels,0)
        finally: self.adapter.fetch_release.set(); await task

    async def test_actual_paper_exchange_fills_remain_compatible(self):
        exchange=PaperExchange(initial_balance=D('2000'),fee_pct=D('.1'),slippage_pct=D('.05'))
        await exchange.connect();exchange.set_price('BTC/USDT',D('100'),timestamp=1234)
        manager=OrderManager(exchange)
        buy=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('10'),client_order_id='buy')
        self.assertEqual((buy.status,buy.remaining),(OrderStatus.FILLED,D('0')))
        again=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('10'),client_order_id='buy')
        self.assertEqual(again.exchange_order_id,buy.exchange_order_id)
        exchange.set_price('BTC/USDT',D('105'),timestamp=2234)
        sell=await manager.place_order('BTC/USDT',OrderSide.SELL,OrderType.MARKET,D('10'),client_order_id='sell')
        self.assertEqual(sell.status,OrderStatus.FILLED)
        self.assertEqual(len(await exchange.fetch_my_trades()),2)
        self.assertEqual((await exchange.fetch_balance())['BTC'].free,0)
        await exchange.disconnect()


# Avoid counting inherited methods twice, without marking any tests skipped.
for _name in list(ManagerSafetyTests.__dict__):
    if _name.startswith('test_'):
        setattr(ManagerBoundaryTests, _name, None)

if __name__ == "__main__": unittest.main()
