"""Pinned read-only order/fill evidence, with real SQLite and localhost HTTP."""
from contextlib import closing
from decimal import Decimal as D
import http.client
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import OrderSide, OrderType, OrderOutcomeUnknown
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver, PaperSessionUnavailable
import trading_core.paper_session_observer as observer


class DetailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'private-account.sqlite'
        self.session = PaperTradingSession(self.path, create=True, initial_balance='1000', fee_pct='0.1', slippage_pct='0.5')
        await self.session.__aenter__()
        self.session.exchange.set_price('BTC/USDT', D('100'), timestamp=1700000000000)
        self.reader = PaperSessionObserver(self.path)

    async def asyncTearDown(self):
        await self.session.close()
        self.tmp.cleanup()

    async def buy(self, key='buy-1', amount='2'):
        return await self.session.manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET, D(amount), client_order_id=key)

    def anchor(self):
        page = self.reader.read_orders()
        return dict(expected_revision=page['revision'], expected_digest=page['digest'])

    def detail(self, key='buy-1', **kwargs):
        self.assertTrue(callable(getattr(self.reader, 'read_order', None)), 'pinned order detail is missing')
        return self.reader.read_order(key, **(kwargs or self.anchor()))

    async def test_confirmed_fill_returns_exact_stored_evidence_without_mutation(self):
        await self.buy()
        before = self.path.read_bytes()
        result = self.detail()
        self.assertEqual(result['source'], 'durable_paper_order_detail')
        self.assertTrue(result['read_only']); self.assertFalse(result['live_enabled'])
        self.assertEqual(result['order']['filled'], '2')
        self.assertEqual(result['evidence']['classification'], 'confirmed_fill')
        self.assertEqual(result['fill']['average_price'], '100.500')
        self.assertEqual(D(result['fill']['notional']), D('201'))
        self.assertEqual(D(result['fill']['fee_amount']), D('0.201'))
        self.assertEqual(result['fill']['fee_currency'], 'USDT')
        self.assertEqual(result['fill']['timestamp_ms'], 1700000000000)
        self.assertEqual(result['evidence']['cause'], 'not_recorded')
        self.assertFalse(result['evidence']['may_resubmit'])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_sell_evidence_is_not_profit_or_an_extra_fee_debit(self):
        await self.buy()
        await self.session.manager.place_order('BTC/USDT', OrderSide.SELL, OrderType.MARKET, D('1'), client_order_id='sell-1')
        before = self.session.exchange.snapshot()
        r = self.detail('sell-1')
        self.assertEqual(r['order']['side'], 'sell')
        self.assertEqual(D(r['fill']['average_price']), D('99.5'))
        self.assertEqual(D(r['fill']['fee_amount']), D('0.0995'))
        self.assertNotIn('profit', r); self.assertNotIn('cash_delta', r['fill'])
        self.assertEqual(self.session.exchange.snapshot(), before)

    async def test_lost_ack_keeps_unconfirmed_order_and_committed_fill_separate(self):
        original = self.session.exchange.create_order
        async def lose(**kwargs):
            await original(**kwargs)
            raise TimeoutError('private transport detail')
        with patch.object(self.session.exchange, 'create_order', side_effect=lose):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy('lost')
        before = self.path.read_bytes()
        r = self.detail('lost')
        self.assertEqual(r['order']['status'], 'unknown')
        self.assertEqual(r['order']['filled'], '0')
        self.assertEqual(r['fill']['quantity'], '2')
        self.assertEqual(r['evidence']['classification'], 'unconfirmed_account_fill')
        self.assertTrue(r['evidence']['requires_reconciliation'])
        self.assertNotIn('private transport detail', json.dumps(r))
        self.assertEqual(self.path.read_bytes(), before)

    async def test_unknown_without_fill_is_null_not_rejected(self):
        with patch.object(self.session.exchange, 'create_order', side_effect=TimeoutError('not evidence')):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy('no-fill')
        r = self.detail('no-fill')
        self.assertIsNone(r['fill'])
        self.assertEqual(r['order']['status'], 'unknown')
        self.assertEqual(r['evidence']['classification'], 'unresolved_without_account_fill')
        self.assertFalse(r['evidence']['may_resubmit'])

    async def test_submitting_snapshot_is_not_restored_or_normalized(self):
        self.assertTrue(callable(getattr(self.reader, 'read_order', None)), 'pinned order detail is missing')
        seen = []
        original = self.session.exchange.create_order
        async def inspect(**kwargs):
            seen.append(self.detail('pending'))
            return await original(**kwargs)
        with patch.object(self.session.exchange, 'create_order', side_effect=inspect):
            await self.buy('pending')
        self.assertEqual(seen[0]['order']['status'], 'submitting')
        self.assertIsNone(seen[0]['fill'])

    async def test_existing_reconciliation_remains_explicit(self):
        from trading_core.order_reconciliation import reconcile_order
        original = self.session.exchange.create_order
        async def lose(**kwargs):
            await original(**kwargs); raise TimeoutError()
        with patch.object(self.session.exchange, 'create_order', side_effect=lose):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy('lost')
        old = self.anchor(); before = self.detail('lost')
        await reconcile_order(self.session.manager, 'lost', allow_client_lookup=True)
        with self.assertRaises(PaperSessionUnavailable): self.detail('lost', **old)
        after = self.detail('lost')
        self.assertEqual(before['fill'], after['fill'])
        self.assertEqual(after['evidence']['classification'], 'confirmed_fill')

    async def test_missing_order_is_not_found_only_in_matching_valid_snapshot(self):
        await self.buy()
        with self.assertRaises(LookupError) as e: self.detail('absent')
        self.assertEqual(str(e.exception), 'paper_order_not_found')

    async def test_invalid_inputs_fail_before_io(self):
        self.assertTrue(callable(getattr(self.reader, 'read_order', None)), 'pinned order detail is missing')
        anchor = self.anchor()
        with patch.object(observer, '_read_row', side_effect=AssertionError('unexpected IO')):
            for key in ('', ' spaced', 'a\n', 'a'*129, None, 1, True, []):
                with self.subTest(key=key), self.assertRaises(ValueError): self.reader.read_order(key, **anchor)
            for kw in ({}, {'expected_revision':'1'}, {'expected_digest':'0'*64},
                       {'expected_revision':'01','expected_digest':'0'*64},
                       {'expected_revision':str(2**62),'expected_digest':'0'*64},
                       {'expected_revision':'1','expected_digest':'A'*64}):
                with self.subTest(kw=kw), self.assertRaises(ValueError): self.reader.read_order('a', **kw)

    async def test_changed_revision_or_digest_rejects_even_missing_id(self):
        a = self.anchor()
        self.session.exchange.set_price('BTC/USDT', D('101'), timestamp=1700000000001)
        with self.assertRaises(PaperSessionUnavailable) as e: self.detail('absent', **a)
        self.assertEqual(str(e.exception), 'paper_session_changed')
        a = self.anchor(); a['expected_digest'] = '0'*64
        with self.assertRaises(PaperSessionUnavailable) as e: self.detail('absent', **a)
        self.assertEqual(str(e.exception), 'paper_session_changed')

    async def test_invalid_source_is_not_found_neither_empty_nor_zero(self):
        a = self.anchor()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE paper_session SET digest='corrupt'"); db.commit()
        with self.assertRaises(PaperSessionUnavailable) as e: self.detail('absent', **a)
        self.assertEqual(str(e.exception), 'paper_session_unavailable')

    async def test_returned_detail_is_detached(self):
        await self.buy(); r = self.detail()
        r['fill']['fee_amount'] = '999'; r['order']['amount'] = '999'
        self.assertEqual(self.detail()['order']['amount'], '2')
        self.assertEqual(D(self.detail()['fill']['fee_amount']), D('0.201'))

    async def test_special_identifier_roundtrips_exactly(self):
        key = '한글/+?&=%#"<tag>'
        await self.buy(key); self.assertEqual(self.detail(key)['order']['client_order_id'], key)

    async def test_shared_read_gate_and_missing_file_do_not_write(self):
        a = self.anchor(); self.reader._gate.acquire()
        try:
            with self.assertRaises(PaperSessionUnavailable): self.detail(**a)
        finally: self.reader._gate.release()
        self.reader = PaperSessionObserver(Path(self.tmp.name)/'missing-private')
        files = list(Path(self.tmp.name).iterdir())
        with self.assertRaises(PaperSessionUnavailable): self.detail(**a)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), files)

    async def test_missing_order_still_checks_file_identity_after_projection(self):
        a = self.anchor()
        replacement = Path(self.tmp.name) / 'replacement.sqlite'
        alternate = PaperTradingSession(replacement, create=True)
        await alternate.close()
        original = observer._order_detail
        def replace(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                replacement.replace(self.path)
        with patch.object(observer, '_order_detail', side_effect=replace):
            with self.assertRaises((LookupError, PaperSessionUnavailable)) as e:
                self.detail('absent', **a)
        self.assertIsInstance(e.exception, PaperSessionUnavailable)
        self.assertEqual(str(e.exception), 'paper_session_unavailable')

    async def test_original_decimal_context_ignores_ambient_precision(self):
        from decimal import localcontext
        await self.buy()
        with localcontext() as ctx:
            ctx.prec = 3
            r = self.detail()
        self.assertEqual(D(r['fill']['notional']), D('201'))
        self.assertEqual(D(r['fill']['fee_amount']), D('0.201'))

    async def test_revision_above_javascript_safe_integer_stays_exact(self):
        await self.buy()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET revision=?', (2**53+7,)); db.commit()
        self.assertEqual(self.detail()['revision'], str(2**53+7))


class DetailHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)/'account.sqlite'
        self.session = PaperTradingSession(self.path, create=True, initial_balance='1000')
        await self.session.__aenter__(); self.session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        self.key = '한글/+?&=%#"<tag>'
        await self.session.manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET, D('1'), client_order_id=self.key)
        spec = importlib.util.spec_from_file_location('detail_test_server', ROOT/'apps/dashboard/server.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.server = module.create_server(port=0, paper_session_path=self.path)
        self.thread = threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01), daemon=True); self.thread.start()
        p = json.loads(self.request('/api/paper-session/orders')[2])
        self.query = dict(client_id=self.key, revision=p['revision'], digest=p['digest'])

    async def asyncTearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        await self.session.close(); self.tmp.cleanup()

    def request(self, path, method='GET', headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            c.request(method, path, headers=headers or {}); r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally: c.close()

    def url(self, **changes):
        return '/api/paper-session/order?' + urlencode(dict(self.query, **changes))

    async def test_detail_response_headers_exact_id_and_no_source_write(self):
        before = self.path.read_bytes(); code, headers, body = self.request(self.url())
        self.assertEqual(code, 200); self.assertEqual(json.loads(body)['order']['client_order_id'], self.key)
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_missing_id_returns_fixed_404(self):
        code, _, body = self.request(self.url(client_id='absent'))
        self.assertEqual(code, 404); self.assertEqual(json.loads(body)['error'], 'paper_order_not_found')

    async def test_changed_snapshot_returns_409(self):
        self.session.exchange.set_price('BTC/USDT', D('101'), timestamp=2)
        code, _, body = self.request(self.url())
        self.assertEqual(code, 409); self.assertEqual(json.loads(body)['error'], 'paper_session_changed')

    async def test_strict_queries_are_400(self):
        for q in ('', 'client_id=a', 'client_id=a&revision=1&digest='+'0'*64+'&path=secret',
                  'client_id=a&client_id=b&revision=1&digest='+'0'*64, 'revision=01&digest='+'0'*64+'&client_id=a',
                  'client_id=%0A&revision=1&digest='+'0'*64):
            with self.subTest(q=q): self.assertEqual(self.request('/api/paper-session/order?'+q)[0], 400)

    async def test_missing_source_fails_fixed_and_no_file_created(self):
        self.server.RequestHandlerClass.paper_session_observer = PaperSessionObserver(Path(self.tmp.name)/'secret')
        files = list(Path(self.tmp.name).iterdir()); code, _, body = self.request(self.url())
        self.assertEqual(code, 503); self.assertNotIn(b'secret', body)
        self.assertEqual(files, list(Path(self.tmp.name).iterdir()))

    async def test_unconfigured_source_needs_no_optional_import(self):
        self.server.RequestHandlerClass.paper_session_observer = None
        import builtins
        original = builtins.__import__
        def deny(name, *args, **kwargs):
            if name == 'trading_core.paper_session_observer': raise ImportError('unconfigured')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=deny): self.assertEqual(self.request(self.url())[0], 503)

    async def test_origin_write_denial_and_head(self):
        self.assertEqual(self.request(self.url(), headers={'Origin':'https://evil.invalid'})[0], 403)
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE'):
            self.assertEqual(self.request(self.url(), method=method)[0], 405)
        code, _, body = self.request(self.url(), method='HEAD'); self.assertEqual(code, 200); self.assertEqual(body, b'')


if __name__ == '__main__': unittest.main()
