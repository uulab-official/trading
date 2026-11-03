"""Read-only order pages on real, synthetic local paper-account SQLite files."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import http.client
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import Order, OrderSide, OrderType, OrderStatus, OrderOutcomeUnknown
from trading_core.order_state_store import _canonical, _digest
from trading_core.paper_session import PaperTradingSession
from trading_core import paper_session_observer as observer_module
from trading_core.paper_session_observer import PaperSessionObserver, PaperSessionUnavailable


class OrderPagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'account.sqlite'
        self.s = PaperTradingSession(self.path, create=True, initial_balance='10000')
        await self.s.__aenter__()
        self.reader = PaperSessionObserver(self.path)
        self.addCleanup(self.tmp.cleanup)
        guard = patch('socket.socket.connect', side_effect=AssertionError('external network forbidden'))
        guard.start(); self.addCleanup(guard.stop)

    async def asyncTearDown(self):
        await self.s.close()

    def page(self, **kwargs):
        self.assertTrue(hasattr(self.reader, 'read_orders'), 'revision-pinned order pages are missing')
        return self.reader.read_orders(**kwargs)

    def seed(self, count):
        # Synthetic unresolved inventory exercises bounds, not actual strategy submissions.
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        orders = {f'order-{i:05d}': Order(f'order-{i:05d}', 'BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D('1.000000000000000000001'), remaining=D('1.000000000000000000001'),
            status=OrderStatus.UNKNOWN if i % 2 else OrderStatus.SUBMITTING,
            created_at=when, updated_at=when) for i in range(count)}
        self.s._store.save(orders)
        return orders

    def anchor(self, page):
        return {'expected_revision': page['revision'], 'expected_digest': page['digest']}

    async def buy(self, key='buy'):
        self.s.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        return await self.s.manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET,
                                               D('1'), client_order_id=key)

    async def test_empty_account_is_a_valid_empty_page(self):
        p = self.page()
        self.assertEqual(p['orders'], dict(total=0, matched=0, status='all', offset=0, limit=50,
            has_previous=False, has_next=False, items=[]))
        self.assertTrue(p['read_only']); self.assertFalse(p['live_enabled'])

    async def test_all_rows_beyond_summary_limit_are_reachable_once(self):
        expected = self.seed(137)
        before = self.path.read_bytes()
        first = self.page(limit=50)
        second = self.page(offset=50, limit=50, **self.anchor(first))
        third = self.page(offset=100, limit=50, **self.anchor(first))
        ids = [i['client_order_id'] for p in (first, second, third) for i in p['orders']['items']]
        self.assertEqual(len(ids), 137); self.assertEqual(len(set(ids)), 137)
        self.assertEqual(ids, sorted(expected, reverse=True))
        self.assertFalse(third['orders']['has_next']); self.assertTrue(second['orders']['has_previous'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.reader.read()['orders']['total'], 137)
        self.assertEqual(len(self.reader.read()['orders']['items']), 100)

    async def test_filter_counts_whole_inventory_not_first_summary_page(self):
        self.seed(137)
        p = self.page(status='unknown', limit=7)
        self.assertEqual(p['orders']['total'], 137); self.assertEqual(p['orders']['matched'], 68)
        self.assertTrue(all(i['status'] == 'unknown' for i in p['orders']['items']))
        self.assertEqual(self.page(status='filled')['orders']['matched'], 0)
        self.assertEqual(self.page(status='unresolved')['orders']['matched'], 137)

    async def test_exact_quantities_and_stored_submitting_are_not_normalized(self):
        self.seed(2); p = self.page()
        self.assertEqual(p['orders']['items'][0]['amount'], '1.000000000000000000001')
        self.assertEqual(p['orders']['items'][1]['status'], 'submitting')
        p['orders']['items'][0]['amount'] = '999'
        self.assertEqual(self.page()['orders']['items'][0]['amount'], '1.000000000000000000001')

    async def test_continuation_requires_both_preconditions(self):
        self.seed(3)
        for kw in ({'offset': 1}, {'offset': 1, 'expected_revision': '2'},
                   {'expected_digest': '0'*64}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): self.page(**kw)

    async def test_invalid_options_rejected_without_reading_database(self):
        self.assertTrue(hasattr(self.reader, 'read_orders'), 'order pages missing')
        bad = [{'limit':v} for v in (0,101,True,1.5,'10')]
        bad += [{'offset':v} for v in (-1,10001,True,1.5,'0')]
        bad += [{'status':v} for v in ('', 'canceled', [], None)]
        bad += [dict(expected_revision=r, expected_digest='0'*64) for r in ('01','-1',1, str(2**62))]
        bad += [dict(expected_revision='1', expected_digest=d) for d in ('x'*64,'A'*64,'0',None)]
        with patch.object(observer_module, '_read_row', side_effect=AssertionError('read before validation')):
            for kw in bad:
                with self.subTest(kw=kw), self.assertRaises(ValueError): self.page(**kw)

    async def test_commit_between_pages_requires_explicit_restart(self):
        self.seed(3); p = self.page(limit=1)
        self.s.exchange.set_price('BTC/USDT', D('100'), timestamp=2)
        with self.assertRaises(PaperSessionUnavailable) as error:
            self.page(offset=1, limit=1, **self.anchor(p))
        self.assertEqual(str(error.exception), 'paper_session_changed')
        latest = self.page(limit=1)
        self.assertNotEqual(latest['revision'], p['revision'])
        self.page(offset=1, limit=1, **self.anchor(latest))

    async def test_matching_revision_but_wrong_digest_conflicts(self):
        self.seed(2); p = self.page()
        with self.assertRaises(PaperSessionUnavailable) as error:
            self.page(expected_revision=p['revision'], expected_digest='0'*64)
        self.assertEqual(str(error.exception), 'paper_session_changed')

    async def test_pinned_first_page_also_refuses_new_revision(self):
        p = self.page(); self.s.exchange.set_price('BTC/USDT', D('100'), timestamp=2)
        with self.assertRaises(PaperSessionUnavailable): self.page(**self.anchor(p))

    async def test_corrupt_source_is_unavailable_not_an_ordinary_change(self):
        p = self.page()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paper_session SET digest='corrupt'"); db.commit()
        with self.assertRaises(PaperSessionUnavailable) as error: self.page(**self.anchor(p))
        self.assertEqual(str(error.exception), 'paper_session_unavailable')

    async def test_lost_ack_is_not_confirmed_by_browsing(self):
        self.s.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        original = self.s.exchange.create_order
        async def lost(**kwargs):
            await original(**kwargs); raise TimeoutError('simulated lost acknowledgement')
        with patch.object(self.s.exchange, 'create_order', side_effect=lost):
            with self.assertRaises(OrderOutcomeUnknown):
                await self.s.manager.place_order('BTC/USDT',OrderSide.BUY,OrderType.MARKET,D('1'),client_order_id='lost')
        before = self.path.read_bytes()
        item = self.page(status='unresolved')['orders']['items'][0]
        self.assertEqual(item['status'], 'unknown'); self.assertTrue(item['account_fill_present'])
        self.assertEqual(item['filled'], '0'); self.assertEqual(self.path.read_bytes(), before)

    async def test_actual_filled_order_fields_and_empty_filter(self):
        await self.buy()
        p = self.page(status='filled'); self.assertEqual(p['orders']['matched'], 1)
        item = p['orders']['items'][0]
        self.assertEqual(item['filled'], '1'); self.assertEqual(item['remaining'], '0')
        self.assertTrue(item['account_fill_present'])
        self.assertEqual(self.page(status='unresolved')['orders']['items'], [])

    async def test_out_of_range_offset_is_not_silently_empty(self):
        self.seed(2); p = self.page()
        with self.assertRaises(ValueError): self.page(offset=3, **self.anchor(p))

    async def test_reader_never_creates_missing_file_or_lock(self):
        self.reader = PaperSessionObserver(Path(self.tmp.name)/'missing.sqlite')
        before = set(Path(self.tmp.name).iterdir())
        with self.assertRaises(PaperSessionUnavailable): self.page()
        self.assertEqual(set(Path(self.tmp.name).iterdir()), before)

    async def test_same_reader_gate_is_shared_with_summary(self):
        self.reader._gate.acquire()
        try:
            with self.assertRaises(PaperSessionUnavailable): self.page()
        finally: self.reader._gate.release()
        self.page()

    async def test_revision_remains_string_above_javascript_safe_integer(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET revision=?', (2**53+7,)); db.commit()
        p=self.page(); self.assertEqual(p['revision'],str(2**53+7))
        self.page(**self.anchor(p))

    async def test_identity_change_is_never_adopted_by_restart_first_page(self):
        self.page(); await self.s.close()
        old=self.path.with_suffix('.old'); self.path.rename(old)
        self.s=PaperTradingSession(self.path,create=True)
        await self.s.__aenter__()
        with self.assertRaises(PaperSessionUnavailable): self.page()

    async def test_summary_contract_stays_unchanged(self):
        self.seed(3); summary=self.reader.read(); p=self.page()
        self.assertEqual(summary['orders']['items'],p['orders']['items'])
        self.assertEqual(summary['revision'],p['revision']); self.assertEqual(summary['digest'],p['digest'])
        self.assertNotIn('offset',summary['orders'])


class OrderPagesHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'account.sqlite'
        self.s=PaperTradingSession(self.path,create=True,initial_balance='1000'); await self.s.__aenter__()
        spec=importlib.util.spec_from_file_location('paper_pages_server',ROOT/'apps/dashboard/server.py')
        self.module=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.server=self.module.create_server(port=0,paper_session_path=self.path)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01),daemon=True)
        self.thread.start()

    async def asyncTearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        await self.s.close(); self.tmp.cleanup()

    def request(self, path='/api/paper-session/orders', method='GET', headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            c.request(method,path,headers=headers or {}); r=c.getresponse(); body=r.read()
            return r.status, dict(r.getheaders()), body
        finally: c.close()

    async def test_page_route_and_security_headers(self):
        before=self.path.read_bytes(); status,h,b=self.request()
        self.assertEqual(status,200); self.assertEqual(json.loads(b)['orders']['matched'],0)
        self.assertEqual(h['Cache-Control'],'no-store'); self.assertIn("default-src 'none'",h['Content-Security-Policy'])
        self.assertEqual(self.path.read_bytes(),before)

    async def test_changed_precondition_is_409_not_silent_new_page(self):
        status,_,body=self.request(); self.assertEqual(status,200); p=json.loads(body)
        self.s.exchange.set_price('BTC/USDT',D('100'),timestamp=1)
        status,_,body=self.request('/api/paper-session/orders?'+urlencode({'revision':p['revision'],'digest':p['digest']}))
        self.assertEqual(status,409); self.assertEqual(json.loads(body)['error'],'paper_session_changed')

    async def test_strict_query_parameters_and_no_path_selection(self):
        for q in ('path=/tmp/secret','limit=0','limit=101','limit=01','limit=%2B1','limit=1&limit=2',
                  'offset=1','offset=-1','status=madeup','digest=x','revision=1','offset=1.0'):
            with self.subTest(q=q): self.assertEqual(self.request('/api/paper-session/orders?'+q)[0],400)

    async def test_source_unavailable_is_503_and_no_path_leak(self):
        self.server.RequestHandlerClass.paper_session_observer=PaperSessionObserver(Path(self.tmp.name)/'secret-missing')
        status,_,body=self.request(); self.assertEqual(status,503)
        self.assertEqual(json.loads(body)['error'],'paper_session_unavailable'); self.assertNotIn(b'secret',body)

    async def test_origin_and_writes_are_rejected(self):
        self.assertEqual(self.request(headers={'Origin':'https://evil.invalid'})[0],403)
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE'):
            self.assertEqual(self.request(method=method)[0],405)
        status,_,body=self.request(method='HEAD'); self.assertEqual(status,200); self.assertEqual(body,b'')

    async def test_old_summary_route_still_rejects_parameters(self):
        self.assertEqual(self.request('/api/paper-session')[0],200)
        self.assertEqual(self.request('/api/paper-session?offset=1')[0],400)

    async def test_no_source_route_does_not_require_optional_reader_import(self):
        self.server.RequestHandlerClass.paper_session_observer = None
        # Other routes are usable even when optional account modules cannot load.
        import builtins
        original = builtins.__import__
        def deny_account(name, *args, **kwargs):
            if name == 'trading_core.paper_session_observer':
                raise ImportError('optional observer not configured')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=deny_account):
            self.assertEqual(self.request()[0], 503)

    async def test_page_assets_are_allowlisted(self):
        for path in ('/paper-orders.html','/paper-orders.mjs','/paper-orders.css'):
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/paper-orders.html?path=secret')[0],400)
        self.assertEqual(self.request('/../account.sqlite')[0],404)


if __name__ == '__main__': unittest.main()
