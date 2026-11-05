"""Offline provider-contract checks; no connection to an exchange is made."""
import copy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'packages/data/trading_data/upbit_history.py'
UTC = timezone.utc
NOW = datetime(2026, 10, 1, 3, 10, tzinfo=UTC)
BASE = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()) * 1000
HOUR = 3600000


def bar(index, **changes):
    value = dict(market='KRW-BTC', unit=60,
                 candle_date_time_utc=datetime.fromtimestamp((BASE+index*HOUR)/1000, UTC).strftime('%Y-%m-%dT%H:%M:%S'),
                 opening_price='100.0100', high_price='102', low_price='99',
                 trade_price='101', candle_acc_trade_volume='0.123456789012345678',
                 timestamp=BASE+index*HOUR+12345)
    value.update(changes)
    return value


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.is_file(), 'closed-history importer is not implemented')
        spec = importlib.util.spec_from_file_location('upbit_history_contract', PATH)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.calls, self.sleeps = [], []

    def client(self, pages, now=NOW):
        pending = iter(pages)
        def get(url):
            self.calls.append(url)
            item = next(pending)
            if isinstance(item, Exception): raise item
            if isinstance(item, tuple): return item
            return json.dumps(item, allow_nan=False).encode(), {}
        return self.m.UpbitHistoryClient(get=get, sleep=self.sleeps.append, clock=lambda: now)

    def fixture(self):
        return self.client([[bar(2), bar(1), bar(0)]]).download('KRW-BTC', unit=60, count=3)

    def test_pagination_utc_opening_time_and_decimal_precision(self):
        fixture = self.client([[bar(2), bar(1)], [bar(0)]]).download('KRW-BTC', unit=60, count=3)
        self.assertEqual(fixture['symbol'], 'BTC/KRW')
        self.assertEqual(fixture['timeframe'], '1h')
        self.assertEqual([r[0] for r in fixture['candles']], [BASE, BASE+HOUR, BASE+2*HOUR])
        self.assertEqual(fixture['candles'][0][1], '100.01')
        self.assertEqual(fixture['candles'][0][5], '0.123456789012345678')
        query = parse_qs(urlsplit(self.calls[1]).query)
        self.assertEqual(query['to'], ['2026-10-01T01:00:00Z'])
        self.assertEqual(query['count'], ['1'])
        self.assertFalse(fixture['data_provenance']['authenticated'])
        self.assertEqual(self.m.validate_market_fixture(fixture), fixture['data_provenance'])

    def test_five_second_close_margin_excludes_just_closed_bar(self):
        now = datetime(2026,10,1,3,0,2,tzinfo=UTC)
        fixture = self.client([[bar(1), bar(0)]], now).download('KRW-BTC', unit=60, count=2)
        self.assertEqual(fixture['data_provenance']['end_exclusive_ms'], BASE+2*HOUR)

    def test_unfinished_bar_is_rejected_even_if_provider_returns_it(self):
        with self.assertRaises(ValueError):
            self.client([[bar(3),bar(2)]]).download('KRW-BTC', unit=60, count=2)

    def test_gaps_and_missing_tail_fail_without_synthetic_candles(self):
        for rows in ([bar(2),bar(0)], [bar(1),bar(0)]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, 'gap'):
                self.client([rows]).download('KRW-BTC', unit=60, count=2)

    def test_duplicate_identical_rows_are_deduplicated(self):
        f = self.client([[bar(2),bar(2),bar(1)], [bar(0)]]).download('KRW-BTC',unit=60,count=3)
        self.assertEqual(len(f['candles']),3)

    def test_conflicting_duplicates_and_cursor_repetition_fail(self):
        for pages in ([[bar(2),bar(2,trade_price='100'),bar(1)]],
                      [[bar(2)], [bar(2)]]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.client(pages).download('KRW-BTC',unit=60,count=3)

    def test_incomplete_history_never_publishes_partial_success(self):
        with self.assertRaisesRegex(ValueError, 'insufficient'):
            self.client([[bar(2)], []]).download('KRW-BTC',unit=60,count=3)

    def test_invalid_request_makes_no_network_call(self):
        options = [dict(market='USDT-BTC'),dict(market='KRW-KRW'),dict(unit=True),
                   dict(unit=2),dict(count=0),dict(count=10001),dict(count=True),
                   dict(end=datetime(2027,1,1,tzinfo=UTC)),dict(end=datetime(2026,1,1))]
        for override in options:
            args=dict(market='KRW-BTC',unit=60,count=3);args.update(override)
            with self.subTest(args=args),self.assertRaises(ValueError): self.client([]).download(**args)
        self.assertEqual(self.calls,[])

    def test_explicit_timezone_end_rounded_down(self):
        end = datetime.fromisoformat('2026-10-01T12:02:00+09:00')
        f = self.client([[bar(2)]]).download('KRW-BTC',unit=60,count=1,end=end)
        self.assertEqual(f['data_provenance']['end_exclusive_ms'],BASE+3*HOUR)

    def test_bad_provider_rows_fail_closed(self):
        cases=[dict(market='KRW-ETH'),dict(unit=1),dict(unit=True),dict(opening_price='NaN'),
               dict(high_price='98'),dict(low_price='103'),dict(trade_price='0'),
               dict(candle_acc_trade_volume='-1'),dict(opening_price='1e9999'),
               dict(candle_date_time_utc='2026-10-01T02:01:00'),
               dict(candle_date_time_utc='2026-10-01T02:00:00+09:00')]
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.client([[bar(2,**changes)]]).download('KRW-BTC',unit=60,count=1)

    def test_bad_json_and_oversized_response(self):
        bodies=[b'[{"unit":60,"unit":1}]',b'[NaN]',b'{}',b'\xff',b'['*2000,
                b' '*(self.m.MAX_PAGE_BYTES+1)]
        for body in bodies:
            with self.subTest(size=len(body)),self.assertRaises(ValueError):
                self.client([(body,{})]).download('KRW-BTC',unit=60,count=1)

    def test_retries_and_rate_exhaustion_are_bounded(self):
        error=HTTPError('https://api.upbit.com',429,'rate limited',{'Retry-After':'2'},None)
        f=self.client([error,(json.dumps([bar(2)]).encode(),{'Remaining-Req':'group=candle; min=0; sec=0'})]).download('KRW-BTC',unit=60,count=1)
        self.assertEqual(len(f['candles']),1)
        self.assertIn(2.0,self.sleeps)
        self.assertIn(1.0,self.sleeps)
        self.calls.clear()
        with self.assertRaises(self.m.MarketDataUnavailable):
            self.client([URLError('offline') for _ in range(3)]).download('KRW-BTC',unit=60,count=1)
        self.assertEqual(len(self.calls),3)

    def test_418_and_long_retry_after_stop_immediately(self):
        for code,headers in [(418,{}),(429,{'Retry-After':'600'}),(400,{})]:
            self.calls.clear()
            with self.subTest(code=code),self.assertRaises(self.m.MarketDataUnavailable):
                self.client([HTTPError('https://api.upbit.com',code,'blocked',headers,None)]).download('KRW-BTC',unit=60,count=1)
            self.assertEqual(len(self.calls),1)

    def test_unparseable_retry_after_is_not_silently_ignored(self):
        error=HTTPError('https://api.upbit.com',429,'cooldown',{'Retry-After':'Thu, 01 Oct 2026 04:00:00 GMT'},None)
        with self.assertRaises(self.m.MarketDataUnavailable):
            self.client([error,[bar(2)]]).download('KRW-BTC',unit=60,count=1)
        self.assertEqual(len(self.calls),1)

    def test_clock_must_remain_timezone_aware_at_publication(self):
        clock=iter([NOW,NOW.replace(tzinfo=None)])
        client=self.client([[bar(2)]])
        client._clock=lambda:next(clock)
        with self.assertRaises(ValueError):client.download('KRW-BTC',unit=60,count=1)

    def test_safe_transport_only_accepts_fixed_https_endpoint(self):
        for url in ['http://api.upbit.com/v1/candles/minutes/60',
                    'https://evil.example/v1/candles/minutes/60',
                    'https://api.upbit.com/v1/orders',
                    'https://user:password@api.upbit.com/v1/candles/minutes/60']:
            with self.subTest(url=url), self.assertRaises(ValueError): self.m.public_get(url)
        redirect=self.m.NoRedirect()
        with self.assertRaises(HTTPError):
            redirect.redirect_request(None,None,302,'redirect',{},'https://evil.example/')

    def test_precision_independent_of_decimal_context(self):
        with localcontext() as c:
            c.prec=3
            f=self.fixture()
        self.assertEqual(f['candles'][0][5],'0.123456789012345678')

    def test_fixture_tamper_or_mismatched_provenance_is_rejected(self):
        original=self.fixture()
        for change in ['candle','hash','count','symbol','unit','gap','authenticated','unknown_field']:
            f=copy.deepcopy(original); p=f['data_provenance']
            if change=='candle': f['candles'][0][4]='100'
            if change=='hash': p['candles_sha256']='0'*64
            if change=='count': p['bar_count']=4
            if change=='symbol': f['symbol']='ETH/KRW'
            if change=='unit': p['unit_minutes']=5
            if change=='gap': p['gap_policy']='forward_fill'
            if change=='authenticated': p['authenticated']=True
            if change=='unknown_field': p['api_key']='not-allowed'
            with self.subTest(change=change),self.assertRaises(ValueError):self.m.validate_market_fixture(f)

    def test_atomic_publication_no_overwrite_and_failed_link_cleanup(self):
        f=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'history.json'
            self.m.write_fixture(path,f)
            self.assertEqual(self.m.decode_json(path.read_bytes()),f)
            before=path.read_bytes()
            with self.assertRaises(FileExistsError):self.m.write_fixture(path,f)
            self.assertEqual(path.read_bytes(),before)
            other=Path(tmp)/'failed.json'
            with patch.object(self.m.os,'link',side_effect=OSError('disk')):
                with self.assertRaises(OSError):self.m.write_fixture(other,f)
            self.assertFalse(other.exists())
            self.assertEqual(list(Path(tmp).glob('.market-*.tmp')),[])

    def test_symlink_or_racing_file_is_not_clobbered(self):
        f=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'output.json';target=Path(tmp)/'target'
            target.write_text('keep');path.symlink_to(target)
            with self.assertRaises(ValueError):self.m.write_fixture(path,f)
            path.unlink();real_link=self.m.os.link
            def race(src,dest):
                Path(dest).write_text('racing writer');return real_link(src,dest)
            with patch.object(self.m.os,'link',side_effect=race):
                with self.assertRaises(FileExistsError):self.m.write_fixture(path,f)
            self.assertEqual(path.read_text(),'racing writer')
            self.assertEqual(target.read_text(),'keep')


if __name__=='__main__':unittest.main()
