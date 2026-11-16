"""Read-only session observations use actual private SQLite accounts, no network."""
import asyncio
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
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
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.paper_session import PaperTradingSession
from trading_core.order_manager import OrderSide, OrderType, OrderOutcomeUnknown
from trading_core.order_state_store import _canonical, _digest


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'paper.sqlite'
        self.session = None

    async def asyncTearDown(self):
        if self.session is not None:
            await self.session.close()
        self.temp.cleanup()

    async def account(self, **kwargs):
        self.session = PaperTradingSession(self.path, create=True, initial_balance='1000',
                                          fee_pct='.1', **kwargs)
        await self.session.__aenter__()
        return self.session

    def observer(self):
        spec = importlib.util.find_spec('trading_core.paper_session_observer')
        self.assertIsNotNone(spec, 'read-only paper session observer is not implemented')
        from trading_core.paper_session_observer import PaperSessionObserver
        return PaperSessionObserver(self.path)

    async def test_read_does_not_restore_orders_or_acquire_writer_lease(self):
        session = await self.account(enable_control=True)
        before = self.path.read_bytes()
        with patch.object(PaperTradingSession, '__init__', side_effect=AssertionError('writer forbidden')):
            report = self.observer().read()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(report['control']['mode'], 'halted')
        self.assertEqual(report['account']['cash'], '1000')
        self.assertEqual(report['orders']['total'], 0)
        self.assertEqual(report['observation']['process_state'], 'unobserved')
        self.assertEqual(report['observation']['order_authorization'], 'not_assessed')
        self.assertEqual(session.control_status['generation'], 0)

    async def buy(self, key='buy', amount='2'):
        self.session.exchange.set_price('BTC/USDT', Decimal('100'), timestamp=1000)
        return await self.session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, Decimal(amount), client_order_id=key)

    async def test_exact_account_values_fees_and_marks(self):
        await self.account()
        await self.buy()
        report = self.observer().read()
        self.assertEqual(Decimal(report['account']['cash']), Decimal('799.8'))
        self.assertEqual(Decimal(report['account']['marked_equity']), Decimal('999.8'))
        self.assertEqual(Decimal(report['account']['total_fees']), Decimal('.2'))
        holding = report['account']['holdings'][0]
        self.assertEqual(holding['asset'], 'BTC')
        self.assertEqual(holding['mark_timestamp_ms'], 1000)
        self.assertEqual(report['observation']['quote_receipts'], 'unavailable_to_observer')
        self.assertEqual(report['orders']['account_fills'], 1)
        self.assertEqual(report['orders']['unresolved'], 0)

    async def test_observation_does_not_confirm_lost_acknowledgement(self):
        session = await self.account()
        original = session.exchange.create_order
        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise TimeoutError('private-response')
        with patch.object(session.exchange, 'create_order', lost):
            with self.assertRaises(OrderOutcomeUnknown):
                await self.buy()
        before = self.path.read_bytes()
        report = self.observer().read()
        self.assertEqual(report['orders']['unresolved'], 1)
        self.assertEqual(report['orders']['unconfirmed_account_fills'], 1)
        self.assertEqual(report['orders']['items'][0]['status'], 'unknown')
        self.assertTrue(report['orders']['items'][0]['account_fill_present'])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_control_updates_are_seen_without_reopening_account(self):
        session = await self.account(enable_control=True)
        observer = self.observer()
        first = observer.read()
        await session.set_operating_mode('active', expected_generation=0, reason='operator')
        second = observer.read()
        self.assertEqual(second['control']['mode'], 'active')
        self.assertGreater(int(second['revision']), int(first['revision']))
        self.assertEqual(second['account_id'], first['account_id'])
        self.assertEqual(second['account'], first['account'])

    async def test_legacy_accounts_do_not_gain_controls(self):
        await self.account()
        report = self.observer().read()
        self.assertEqual(report['session_version'], 1)
        self.assertFalse(report['control']['enabled'])
        self.assertIsNone(report['control']['mode'])
        self.assertFalse(report['policy']['enabled'])

    async def test_policy_is_information_not_execution_permission(self):
        await self.account(max_quote_age_ms=30000, max_order_notional='200')
        report = self.observer().read()
        self.assertEqual(report['session_version'], 2)
        self.assertEqual(report['policy']['max_quote_age_ms'], 30000)
        self.assertEqual(report['policy']['max_order_notional'], '200')
        self.assertNotIn('can_trade', report)

    async def test_missing_file_does_not_create_db_or_lock(self):
        observer = self.observer()
        with self.assertRaises(ValueError): observer.read()
        self.assertEqual(list(self.path.parent.iterdir()), [])

    async def test_missing_and_invalid_files_return_fixed_error(self):
        observer = self.observer()
        for data in (b'', b'private-secret-not-a-db'):
            self.path.write_bytes(data); self.path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, '^paper_session_unavailable$'):
                observer.read()

    async def test_links_and_public_permissions_are_rejected(self):
        await self.account()
        observer = self.observer()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): observer.read()
        self.path.chmod(0o600)
        extra = self.path.with_name('alias.sqlite')
        os.link(self.path, extra)
        with self.assertRaises(ValueError): observer.read()
        extra.unlink()
        target = self.path.with_name('target.sqlite')
        self.path.rename(target); self.path.symlink_to(target)
        with self.assertRaises(ValueError): observer.read()
        self.path.unlink(); target.rename(self.path)

    def corrupt(self, change):
        with closing(sqlite3.connect(self.path)) as db:
            raw = db.execute('SELECT payload FROM paper_session').fetchone()[0]
            data = json.loads(raw); change(data)
            raw = _canonical(data)
            db.execute('UPDATE paper_session SET payload=?,digest=?', (raw,_digest(raw)))
            db.commit()

    async def test_checksumming_a_corrupt_balance_cannot_make_it_valid(self):
        await self.account(); await self.buy()
        self.corrupt(lambda d:d['book']['balances'].update(USDT='99999'))
        with self.assertRaises(ValueError): self.observer().read()

    async def test_extra_schema_is_rejected(self):
        await self.account()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE injected(x)'); db.commit()
        with self.assertRaises(ValueError): self.observer().read()

    async def test_observer_pins_file_identity(self):
        await self.account()
        observer = self.observer(); observer.read()
        copied = self.path.with_name('replacement');copied.write_bytes(self.path.read_bytes());copied.chmod(0o600)
        saved = self.path.with_name('saved');self.path.rename(saved);copied.rename(self.path)
        with self.assertRaises(ValueError): observer.read()
        self.path.unlink();saved.rename(self.path)

    async def test_regressing_revision_is_not_a_new_success(self):
        await self.account()
        observer = self.observer(); observer.read()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET revision=0');db.commit()
        with self.assertRaises(ValueError): observer.read()

    async def test_uncommitted_change_is_invisible_and_exclusive_lock_is_unavailable(self):
        await self.account()
        observer = self.observer(); before = observer.read()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute('BEGIN IMMEDIATE');db.execute('UPDATE paper_session SET digest=?', ('bad',))
            during = observer.read()
            self.assertEqual(before['digest'], during['digest'])
            db.execute('ROLLBACK'); db.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(ValueError):observer.read()
            db.execute('ROLLBACK')
        self.assertEqual(observer.read()['digest'], before['digest'])

    async def test_returned_object_is_detached(self):
        await self.account(enable_control=True)
        observer=self.observer();report=observer.read();report['control']['mode']='active'
        report['account']['cash']='999999'
        self.assertEqual(observer.read()['control']['mode'], 'halted')
        self.assertEqual(observer.read()['account']['cash'], '1000')

    async def test_query_failure_does_not_leak_path_or_payload(self):
        await self.account()
        with patch('sqlite3.connect', side_effect=RuntimeError('SECRET '+str(self.path))):
            with self.assertRaisesRegex(ValueError, '^paper_session_unavailable$'):
                self.observer().read()

    async def test_details_are_bounded_but_counts_use_every_order(self):
        from trading_core.order_manager import Order, OrderStatus
        await self.account()
        rows={}
        for i in range(137):
            item=Order(f'unresolved-{i:03}', 'BTC/USDT', OrderSide.BUY, OrderType.MARKET,
                       Decimal('1'), remaining=Decimal('1'), status=OrderStatus.UNKNOWN)
            rows[item.client_order_id]=item.to_dict()
        self.corrupt(lambda d:d.update(orders=rows))
        r=self.observer().read()
        self.assertEqual(r['orders']['total'],137)
        self.assertEqual(r['orders']['unresolved'],137)
        self.assertEqual(len(r['orders']['items']),100)
        self.assertTrue(r['orders']['truncated'])
        self.assertEqual(r['account']['cash'],'1000')

    async def test_stored_submitting_is_not_normalized_by_observation(self):
        from trading_core.order_manager import Order, OrderStatus
        await self.account()
        item=Order('pending','BTC/USDT',OrderSide.BUY,OrderType.MARKET,Decimal('1'),
                   remaining=Decimal('1'),status=OrderStatus.SUBMITTING)
        self.corrupt(lambda d:d['orders'].update(pending=item.to_dict()))
        before=self.path.read_bytes()
        r=self.observer().read()
        self.assertEqual(r['orders']['items'][0]['status'],'submitting')
        self.assertEqual(self.path.read_bytes(),before)

    async def test_version_mismatch_and_digest_corruption_are_rejected(self):
        await self.account(enable_control=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET version=1');db.commit()
        with self.assertRaises(ValueError):self.observer().read()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paper_session SET version=3,digest='bad'");db.commit()
        with self.assertRaises(ValueError):self.observer().read()

    async def test_missing_marks_are_not_assumed_zero(self):
        await self.account();await self.buy()
        self.corrupt(lambda d:d['book']['tickers'].clear())
        report=self.observer().read()
        self.assertIsNone(report['account']['marked_equity'])
        self.assertIsNone(report['account']['position_value'])
        self.assertEqual(report['observation']['missing_marks_count'],1)
        self.assertEqual(report['account']['holdings'][0]['quantity'],'2')

    async def test_changed_account_in_same_file_is_not_adopted(self):
        await self.account();observer=self.observer();observer.read()
        self.corrupt(lambda d:d.update(account_id='b'*32))
        with self.assertRaises(ValueError):observer.read()

    async def test_same_revision_changed_digest_is_rejected(self):
        await self.account(enable_control=True);observer=self.observer();observer.read()
        self.corrupt(lambda d:d.update(control={'mode':'active','generation':1,'reason':'operator'}))
        # The new payload is structurally valid; only the unchanged revision conflicts.
        from trading_core.paper_session import _bundle
        with closing(sqlite3.connect(self.path)) as db:
            _bundle(db.execute('SELECT payload FROM paper_session').fetchone()[0])
        with self.assertRaises(ValueError):observer.read()

    async def test_database_replacement_during_projection_is_rejected(self):
        await self.account()
        from trading_core import paper_session_observer as mod
        original=mod._project;saved=self.path.with_name('saved')
        def replace(*args):
            result=original(*args)
            self.path.rename(saved);self.path.write_bytes(saved.read_bytes());self.path.chmod(0o600)
            return result
        try:
            with patch.object(mod,'_project',replace):
                with self.assertRaises(ValueError):self.observer().read()
        finally:
            if saved.exists():self.path.unlink();saved.rename(self.path)

    async def test_reader_does_not_refresh_or_rewrite_prices(self):
        session=await self.account();await self.buy()
        observer=self.observer();a=observer.read();b=observer.read()
        self.assertEqual(a['digest'],b['digest'])
        self.assertEqual(a['revision'],b['revision'])
        self.assertEqual(a['account']['holdings'][0]['mark_timestamp_ms'],1000)
        self.assertEqual(b['account']['holdings'][0]['mark_timestamp_ms'],1000)

    async def test_other_order_store_is_not_a_paper_account(self):
        from trading_core.order_state_store import SQLiteOrderStateStore
        with SQLiteOrderStateStore(self.path,scope='demo',create=True):
            with self.assertRaises(ValueError):self.observer().read()

    async def test_concurrent_read_gate_is_released_after_failure(self):
        await self.account();observer=self.observer()
        observer._gate.acquire()
        try:
            with self.assertRaises(ValueError):observer.read()
        finally:observer._gate.release()
        self.assertEqual(observer.read()['account']['cash'],'1000')


    async def test_actual_control_commit_exposes_only_old_then_new_bundle(self):
        session=await self.account(enable_control=True)
        observer=self.observer();before=observer.read();seen=[]
        commit=session._store._commit
        def observe_before_commit():
            seen.append(observer.read())
            commit()
        with patch.object(session._store,'_commit',observe_before_commit):
            await session.set_operating_mode('active',expected_generation=0,reason='operator')
        after=observer.read()
        self.assertEqual(seen[0]['control']['mode'],'halted')
        self.assertEqual(seen[0]['revision'],before['revision'])
        self.assertEqual(after['control']['mode'],'active')
        self.assertEqual(after['control']['generation'],1)
        self.assertEqual(after['account'],before['account'])


if __name__ == '__main__':
    unittest.main()
