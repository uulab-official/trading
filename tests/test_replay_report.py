"""Contract tests: report is a derived view, never an execution ledger."""
import copy
from decimal import Decimal as D, localcontext
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'apps/server/trading_bot/replay_report.py'


def load_report_module():
    if not PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location('replay_report', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result_fixture():
    # One buy and one sell, with fees already removed from quote cash.
    def fill(key, side, price, fee, timestamp):
        return dict(id='paper-'+key, clientOrderId=key, symbol='BTC/USDT',
                    type='market', side=side, amount='1', filled='1', remaining='0',
                    average=price, price=price, status='closed', timestamp=timestamp,
                    fee={'cost': fee, 'currency': 'USDT'})
    return dict(mode='paper', status='fixture_complete', bars_processed=3,
                assumptions=dict(mode='paper', symbol='BTC/USDT', timeframe='1h',
                                 strategy='ma_cross', initial_balance='1000',
                                 fee_pct='1', slippage_pct='0'),
                cash='1007.90', equity='1007.90', holdings={}, total_fees='2.10',
                total_return_pct='0.7900', pending_signal=None,
                daily_loss_halted=False, drawdown_halted=False,
                trades=[fill('buy', 'buy', '100', '1', 3600000),
                        fill('sell', 'sell', '110', '1.10', 7200000)])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.m = load_report_module()
        self.assertIsNotNone(self.m, 'replay_report implementation is missing')
        self.result = result_fixture()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'report.json'

    def build(self, result=None):
        return self.m.build_report(result or self.result, fixture_sha256='a'*64,
                                   first_bar_ms=0, last_bar_ms=7200000, mark_price='110')

    def test_net_fees_and_replay_identity(self):
        report = self.build()
        self.assertEqual(D(report['account']['equity']), D('1007.90'))
        self.assertEqual(D(report['account']['total_pnl']), D('7.90'))
        self.assertEqual(D(report['account']['total_fees']), D('2.10'))
        self.assertEqual(report['fill_count'], 2)
        self.assertEqual(report['source'], 'local_fixture')
        self.assertFalse(report['live_enabled'])
        self.assertEqual(report['run_id'], self.build()['run_id'])

    def test_marked_open_position(self):
        self.result.update(cash='899', equity='1009', holdings={'BTC': '1'},
                           total_fees='1', total_return_pct='0.9', trades=self.result['trades'][:1])
        report = self.build()
        self.assertEqual(report['positions'][0]['quantity'], '1')
        self.assertEqual(D(report['positions'][0]['market_value']), D('110'))
        self.assertEqual(D(report['account']['cash']), D('899'))

    def test_reject_inconsistent_or_nonfinite_accounting(self):
        for field, bad in [('cash', '1008'), ('equity','NaN'), ('total_fees','0'),
                           ('total_return_pct','80'), ('holdings',{'BTC':'1'})]:
            result = copy.deepcopy(self.result); result[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build(result)

    def test_reject_invalid_fills(self):
        for field, bad in [('side','short'), ('remaining','1'), ('symbol','ETH/USDT'),
                           ('timestamp',8000000), ('filled','2')]:
            result = copy.deepcopy(self.result); result['trades'][0][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.build(result)
        result = copy.deepcopy(self.result)
        result['trades'].append(result['trades'][0])
        with self.assertRaises(ValueError): self.build(result)

    def test_currency_and_live_fail_closed(self):
        for update in [dict(mode='live'), dict(quote_currency='KRW'),
                       dict(status='paused'), dict(bars_processed=True)]:
            result = copy.deepcopy(self.result); result.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError): self.build(result)
        self.result['trades'][0]['fee']['currency'] = 'KRW'
        with self.assertRaises(ValueError): self.build()

    def test_context_independent_decimal_arithmetic(self):
        with localcontext() as context:
            context.prec = 3
            report = self.build()
        self.assertEqual(D(report['account']['total_pnl']), D('7.90'))

    def test_atomic_read_idempotence_and_no_secret_fields(self):
        self.result['api_key'] = 'must-not-leak'
        self.result['assumptions']['token'] = 'must-not-leak'
        report = self.build()
        self.m.write_report(self.path, report)
        before = self.path.read_bytes()
        self.m.write_report(self.path, self.build())
        self.assertEqual(self.path.read_bytes(), before)
        parsed = self.m.read_report(self.path)
        self.assertEqual(parsed['run_id'], report['run_id'])
        self.assertNotIn('must-not-leak', json.dumps(parsed))

    def test_no_overwrite_of_other_data_or_different_run(self):
        self.path.write_text('do not overwrite')
        with self.assertRaises(ValueError): self.m.write_report(self.path, self.build())
        self.assertEqual(self.path.read_text(), 'do not overwrite')
        self.path.unlink()
        self.m.write_report(self.path, self.build())
        changed = self.m.build_report(self.result, fixture_sha256='b'*64,
                                     first_bar_ms=0, last_bar_ms=7200000, mark_price='110')
        with self.assertRaises(ValueError): self.m.write_report(self.path, changed)

    def test_failed_replace_does_not_publish_partial_json(self):
        report = self.build()
        with patch.object(self.m.os, 'link', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.m.write_report(self.path, report)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob('.replay-*.tmp')), [])

    def test_racing_unrelated_file_is_never_replaced(self):
        real_link = self.m.os.link
        def racing_link(source, destination):
            Path(destination).write_text('concurrent unrelated file')
            return real_link(source, destination)
        with patch.object(self.m.os, 'link', side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                self.m.write_report(self.path, self.build())
        self.assertEqual(self.path.read_text(), 'concurrent unrelated file')

    def test_non_regular_report_is_rejected_without_blocking(self):
        if not hasattr(self.m.os, 'mkfifo'):
            self.skipTest('POSIX-only FIFO regression')
        self.m.os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            self.m.read_report(self.path)

    def test_reader_rejects_tampering_duplicates_and_large_files(self):
        report = self.build(); self.m.write_report(self.path, report)
        raw = json.loads(self.path.read_text()); raw['account']['cash'] = '999999'
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): self.m.read_report(self.path)
        self.path.write_text('{"mode":"live","mode":"paper"}')
        with self.assertRaises(ValueError): self.m.read_report(self.path)
        self.path.write_bytes(b' '*(self.m.MAX_REPORT_BYTES+1))
        with self.assertRaises(ValueError): self.m.read_report(self.path)


if __name__ == '__main__': unittest.main()
