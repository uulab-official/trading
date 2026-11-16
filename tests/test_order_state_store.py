"""Actual local SQLite/lock persistence, no broker credentials or networking."""
import asyncio
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import importlib.util
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
sys.path.insert(0, str(ROOT/'packages/core'))
from trading_core import order_manager as om


def make_order(status=om.OrderStatus.SUBMITTING, filled='0', venue=None):
    return om.Order('one','BTC/USDT',om.OrderSide.BUY,om.OrderType.LIMIT,D('2'),D('100'),
                    status=status, filled=D(filled), remaining=D('2')-D(filled),
                    exchange_order_id=venue)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_core.order_state_store'),
                             'SQLite state store must exist')
        from trading_core.order_state_store import SQLiteOrderStateStore
        self.Store = SQLiteOrderStateStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'orders.sqlite'

    def create(self):
        s = self.Store(self.path, scope='paper-test', create=True)
        self.addCleanup(s.close)
        return s

    def reopen(self):
        s = self.Store(self.path, scope='paper-test')
        self.addCleanup(s.close)
        return s

    def test_roundtrip_preserves_exact_decimal_and_no_auto_initialization(self):
        with self.assertRaises(om.OrderPersistenceError): self.reopen()
        self.assertFalse(self.path.exists())
        s = self.create(); self.assertEqual(s.claim(), {})
        o = make_order(); o.price=D('100.0000000000000000001')
        s.save({'one':o}); s.close()
        with self.reopen() as s:
            got=s.claim(); self.assertEqual(got['one'].to_dict(),o.to_dict())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_no_clobber_existing_file_even_empty(self):
        for body in [b'',b'not sqlite']:
            self.path.write_bytes(body)
            with self.assertRaises(om.OrderPersistenceError): self.create()
            self.assertEqual(self.path.read_bytes(),body)

    def test_wrong_scope_rejected_without_rewrite(self):
        s=self.create();s.close(); before=self.path.read_bytes()
        with self.assertRaises(om.OrderPersistenceError): self.Store(self.path,scope='different')
        self.assertEqual(self.path.read_bytes(),before)

    def test_duplicate_owner_rejected_and_lock_released_on_close(self):
        s=self.create()
        with self.assertRaises(om.OrderPersistenceError): self.reopen()
        s.close(); self.reopen()

    def test_two_managers_cannot_share_store(self):
        s=self.create(); om.OrderManager(None,state_store=s)
        with self.assertRaises(om.OrderPersistenceError): om.OrderManager(None,state_store=s)

    def test_corrupt_digest_rejected(self):
        s=self.create(); s.close()
        with sqlite3.connect(self.path) as db: db.execute("UPDATE order_state SET digest=?",('0'*64,))
        with self.assertRaises(om.OrderPersistenceError): self.reopen()

    def test_unexpected_table_and_trigger_rejected(self):
        s=self.create();s.close()
        with sqlite3.connect(self.path) as db: db.execute('CREATE TABLE foreign_table(x)')
        with self.assertRaises(om.OrderPersistenceError): self.reopen()

    def test_invalid_stored_order_rejected_even_with_matching_checksum(self):
        s=self.create();s.claim();s.save({'one':make_order()});s.close()
        with sqlite3.connect(self.path) as db:
            payload=json.loads(db.execute('SELECT payload FROM order_state').fetchone()[0])
            payload['one']['filled']='3'
            raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True)
            db.execute('UPDATE order_state SET payload=?,digest=?',(raw,hashlib.sha256(raw.encode()).hexdigest()))
        with self.assertRaises(om.OrderPersistenceError): self.reopen()

    def test_db_replacement_rejected(self):
        s=self.create();s.claim(); old=self.path.with_suffix('.old');self.path.rename(old)
        self.path.write_bytes(old.read_bytes())
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':make_order()})

    def test_lock_replacement_rejected(self):
        s=self.create();s.claim()
        lock=self.path.with_name('.'+self.path.name+'.orders.lock')
        lock.rename(lock.with_suffix('.old'));lock.touch(mode=0o600)
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':make_order()})

    def test_foreign_revision_rejected(self):
        s=self.create();s.claim()
        with sqlite3.connect(self.path) as db: db.execute('UPDATE order_state SET revision=revision+1')
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':make_order()})

    def test_dropping_orders_or_changing_intent_rejected(self):
        s=self.create();s.claim();o=make_order();s.save({'one':o})
        with self.assertRaises(om.OrderPersistenceError): s.save({})
        s.close();s=self.reopen();s.claim();o.price=D('101')
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':o})

    def test_decreasing_fills_or_changing_terminal_rejected(self):
        s=self.create();s.claim();original=make_order(om.OrderStatus.PARTIALLY_FILLED,'1','v1')
        s.save({'one':original})
        o=deepcopy(original);o.filled=D('0');o.remaining=D('2')
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':o})
        s.close();s=self.reopen();s.claim()
        o=deepcopy(original);o.status=om.OrderStatus.FILLED;o.filled=D('2');o.remaining=D('0')
        s.save({'one':o})
        changed=deepcopy(o);changed.status=om.OrderStatus.CANCELED;changed.filled=D('1');changed.remaining=D('1')
        with self.assertRaises(om.OrderPersistenceError): s.save({'one':changed})

    def test_symlink_and_hardlink_rejected(self):
        s=self.create();s.close();alias=self.path.with_name('alias.sqlite')
        alias.symlink_to(self.path)
        with self.assertRaises(om.OrderPersistenceError): self.Store(alias,scope='paper-test')
        alias.unlink();os.link(self.path,alias)
        with self.assertRaises(om.OrderPersistenceError): self.reopen()

    def test_commit_failure_rolls_back(self):
        s=self.create();s.claim();o=make_order();s.save({'one':o})
        o.status=om.OrderStatus.OPEN;o.exchange_order_id='v1'
        with patch.object(s,'_commit',side_effect=OSError('private')):
            with self.assertRaises(om.OrderPersistenceError): s.save({'one':o})
        s.close()
        self.assertEqual(self.reopen().claim()['one'].status,om.OrderStatus.SUBMITTING)

    def test_write_lock_held_by_other_connection_fails_closed(self):
        s=self.create();s.claim()
        with sqlite3.connect(self.path) as other:
            other.execute('BEGIN IMMEDIATE')
            with self.assertRaises(om.OrderPersistenceError): s.save({'one':make_order()})
            other.rollback()

    def test_existing_db_is_not_reinitialized_when_row_missing(self):
        s=self.create();s.close()
        with sqlite3.connect(self.path) as db: db.execute('DELETE FROM order_state')
        before=self.path.read_bytes()
        with self.assertRaises(om.OrderPersistenceError): self.reopen()
        self.assertEqual(self.path.read_bytes(),before)

    def test_close_is_idempotent_but_closed_store_is_not_usable(self):
        s=self.create();s.close();s.close()
        with self.assertRaises(om.OrderPersistenceError): s.claim()

    def test_forked_handle_is_rejected_without_releasing_parent_lock(self):
        s=self.create();s.claim()
        pid=os.fork()
        if pid==0:
            try:
                s.save({'one':make_order()})
            except om.OrderPersistenceError:
                s.close(); os._exit(0)
            os._exit(9)
        _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)
        with self.assertRaises(om.OrderPersistenceError): self.reopen()
        s.save({'one':make_order()})

    def test_initialization_failure_closes_connection_and_leaves_no_database(self):
        import trading_core.order_state_store as module
        opened=[];connect=sqlite3.connect
        def capture(*args,**kwargs):
            db=connect(*args,**kwargs);opened.append(db);return db
        with patch.object(module.sqlite3,'connect',side_effect=capture), patch.object(module,'SCHEMA','INVALID DDL'):
            with self.assertRaises(om.OrderPersistenceError): self.create()
        self.assertFalse(self.path.exists())
        self.assertEqual(len(opened),1)
        with self.assertRaises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')
        self.assertFalse(list(self.path.parent.glob('.order-state-*')))
        self.create()

    def test_revision_limit_rejects_before_invalid_state_commit(self):
        s=self.create();s.close()
        with sqlite3.connect(self.path) as db:db.execute('UPDATE order_state SET revision=?',(2**62-1,))
        s=self.reopen();s.claim()
        with self.assertRaises(om.OrderPersistenceError):s.save({})
        s.close()
        with self.reopen() as s:self.assertEqual(s.claim(),{})

    def test_unexpected_trigger_is_rejected(self):
        s=self.create();s.close()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TRIGGER unexpected BEFORE UPDATE ON order_state BEGIN SELECT 1; END')
        with self.assertRaises(om.OrderPersistenceError):self.reopen()

    def test_real_second_process_cannot_open_owned_store(self):
        s=self.create();s.claim()
        code="""import sys
sys.path.insert(0,sys.argv[1])
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.order_manager import OrderPersistenceError
try:SQLiteOrderStateStore(sys.argv[2],scope='paper-test')
except OrderPersistenceError:sys.exit(0)
sys.exit(9)
"""
        result=subprocess.run([sys.executable,'-I','-B','-c',code,
            str(ROOT/'packages/core'),str(self.path)],capture_output=True,text=True,
            env={'PATH':os.defpath},timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        s.save({'one':make_order()})


if __name__=='__main__':unittest.main()
