"""Real process exits/SQLite recovery, explicit fake venue; no live requests."""
import asyncio
from decimal import Decimal as D
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packages/core'))
from trading_core.order_manager import (OrderManager,OrderSide,OrderType,OrderStatus,
                                       OrderOutcomeUnknown,OrderPersistenceError,ReconciliationRequired)
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange

CHILD=r'''
import asyncio,json,os,sys
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from trading_core.order_manager import *
from trading_core.order_state_store import SQLiteOrderStateStore
root=Path(sys.argv[2]);phase=sys.argv[3];mode=sys.argv[4]
effects=root/'effects.json'
class Venue:
 async def create_order(self,**kw):
  if phase=='before_effect':os._exit(71)
  calls=json.loads(effects.read_text()) if effects.exists() else []
  calls.append(kw['params']['clientOrderId']);effects.write_text(json.dumps(calls))
  if phase=='after_effect':os._exit(72)
  return dict(id='venue-one',status='open' if phase=='cancel_effect' else 'filled',filled='0' if phase=='cancel_effect' else '2')
 async def cancel_order(self,*a):
  (root/'cancel-effect').write_text('accepted')
  os._exit(73)
 async def fetch_order(self,*a):
  return dict(id='venue-one',status='filled',filled='2')
async def run():
 with SQLiteOrderStateStore(root/'orders.sqlite',scope='paper-test',create=(mode=='run')) as store:
  manager=OrderManager(Venue(),state_store=store)
  if mode=='run':
   commit=store._commit
   def hook():
    if store._last[0]==2 and phase=='ack_before_commit':os._exit(74)
    commit()
    if store._last[0]==2 and phase=='ack_after_commit':os._exit(75)
   store._commit=hook
  result=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.LIMIT,D('2'),D('100'),client_order_id='one')
  if mode=='run' and phase=='cancel_effect':await manager.cancel_order('one')
  if mode=='recover' and phase=='cancel_effect':await manager._sync_orders()
  state=await manager.get_order('one')
  print(json.dumps({'status':state.status.value,'filled':str(state.filled),'cancel_requested':state.cancel_requested}))
asyncio.run(run())
'''


class ProcessRestartTests(unittest.TestCase):
    def exercise(self,phase,code,expected):
        with tempfile.TemporaryDirectory() as td:
            command=[sys.executable,'-I','-B','-c',CHILD,str(ROOT/'packages/core'),td,phase]
            env={'PATH':os.defpath,'LANG':'C.UTF-8'}
            first=subprocess.run(command+['run'],capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(first.returncode,code,first.stderr)
            recovered=subprocess.run(command+['recover'],capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(recovered.returncode,0,recovered.stderr)
            self.assertEqual(json.loads(recovered.stdout)['status'],expected)
            again=subprocess.run(command+['recover'],capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(again.returncode,0,again.stderr)
            path=Path(td)/'effects.json'
            self.assertEqual(json.loads(path.read_text()) if path.exists() else [],
                             [] if phase=='before_effect' else ['one'])

    def test_kill_after_intent_before_external_effect(self): self.exercise('before_effect',71,'unknown')
    def test_kill_after_external_effect_before_response(self): self.exercise('after_effect',72,'unknown')
    def test_kill_before_ack_commit_rolls_back(self): self.exercise('ack_before_commit',74,'unknown')
    def test_kill_after_ack_commit_preserves_terminal_result(self): self.exercise('ack_after_commit',75,'filled')
    def test_kill_after_cancel_effect_readback_no_recancel(self): self.exercise('cancel_effect',73,'filled')


class SQLiteManagerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'orders.sqlite'

    async def test_real_paper_exchange_snapshot_restore_and_duplicate_are_consistent(self):
        exchange=PaperExchange(initial_balance=D('1000'),fee_pct=D('0.1'))
        await exchange.connect();exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        with SQLiteOrderStateStore(self.path,scope='paper',create=True) as s:
            manager=OrderManager(exchange,state_store=s)
            original=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id='one')
            self.assertEqual(original.status,OrderStatus.FILLED)
            broker_snapshot=exchange.snapshot()
        restored_exchange=PaperExchange.from_snapshot(broker_snapshot)
        await restored_exchange.connect()
        with SQLiteOrderStateStore(self.path,scope='paper') as s:
            manager=OrderManager(restored_exchange,state_store=s)
            duplicate=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id='one')
            self.assertEqual(original.to_dict(),duplicate.to_dict())
            self.assertEqual(restored_exchange.snapshot(),broker_snapshot)
        await exchange.disconnect();await restored_exchange.disconnect()

    async def test_cancel_pending_restore_remains_blocking_until_readback(self):
        class Venue:
            async def create_order(self,**kw):return dict(id='v',status='open',filled='0')
            async def cancel_order(self,*a):return dict(id='v',status='open',filled='0')
            async def fetch_order(self,*a):return dict(id='v',status='canceled',filled='0')
        with SQLiteOrderStateStore(self.path,scope='paper',create=True) as s:
            manager=OrderManager(Venue(),state_store=s)
            await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.LIMIT,D('2'),D('100'),client_order_id='one')
            await manager.cancel_order('one')
        with SQLiteOrderStateStore(self.path,scope='paper') as s:
            manager=OrderManager(Venue(),state_store=s)
            state=await manager.get_order('one')
            self.assertTrue(state.cancel_requested);self.assertEqual(state.status,OrderStatus.UNKNOWN)
            with self.assertRaises(ReconciliationRequired):await manager.cancel_order('one')
            await manager._sync_orders()
            self.assertEqual((await manager.get_order('one')).status,OrderStatus.CANCELED)

    async def test_response_save_failure_restores_unknown_without_venue_guess(self):
        class Venue:
            calls=0
            async def create_order(self,**kw):
                self.calls+=1
                return dict(id='v',status='filled',filled='2')
        venue=Venue()
        with SQLiteOrderStateStore(self.path,scope='paper',create=True) as s:
            manager=OrderManager(venue,state_store=s)
            commit=s._commit
            def fail():
                if s._last[0]==2:raise OSError('private disk error')
                commit()
            with patch.object(s,'_commit',side_effect=fail):
                with self.assertRaises(OrderPersistenceError):
                    await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.LIMIT,D('2'),D('100'),client_order_id='one')
        with SQLiteOrderStateStore(self.path,scope='paper') as s:
            manager=OrderManager(venue,state_store=s)
            state=await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.LIMIT,D('2'),D('100'),client_order_id='one')
            self.assertEqual(state.status,OrderStatus.UNKNOWN);self.assertIsNone(state.exchange_order_id)
            with self.assertRaises(ReconciliationRequired):
                await manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('1'),client_order_id='two')
        self.assertEqual(venue.calls,1)


if __name__=='__main__':unittest.main()
