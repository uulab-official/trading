"""Provider metadata is a checked annotation, not authenticated exchange data."""
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest

import test_upbit_history as history
BASE, HOUR = history.BASE, history.HOUR

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def dataset():
    helper=history.HistoryTests('test_invalid_request_makes_no_network_call');helper.setUp()
    return helper.fixture()


def empty_result():
    return dict(mode='paper',status='fixture_complete',bars_processed=3,
                assumptions=dict(mode='paper',symbol='BTC/KRW',timeframe='1h',strategy='ma_cross',
                                 initial_balance='1000',fee_pct='0.1',slippage_pct='0.05'),
                quote_currency='KRW',cash='1000',equity='1000',holdings={},total_fees='0',
                total_return_pct='0',trades=[],daily_loss_halted=False,drawdown_halted=False)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.report=load('market_report_test','apps/server/trading_bot/replay_report.py')
        self.export=load('market_export_test','tools/paper_replay_dashboard.py')
        self.assertIn('data_provenance',inspect.signature(self.report.build_report).parameters)
        self.fixture=dataset()

    def build(self, provenance=None):
        return self.report.build_report(empty_result(),fixture_sha256='a'*64,first_bar_ms=BASE,
                     last_bar_ms=BASE+2*HOUR,mark_price='101',data_provenance=provenance)

    def test_metadata_round_trip_and_legacy_report_compatibility(self):
        old=self.build();new=self.build(self.fixture['data_provenance'])
        self.assertNotIn('data_provenance',old)
        self.assertEqual(self.report.validate_report(old),old)
        self.assertEqual(self.report.validate_report(new)['data_provenance'],self.fixture['data_provenance'])
        # Dataset identity is already bound to raw fixture SHA; legacy IDs do not change.
        self.assertEqual(old['run_id'],new['run_id'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json';self.report.write_report(path,new)
            self.assertEqual(self.report.read_report(path),new)

    def test_bad_metadata_cannot_be_published_as_good_report(self):
        for field,value in [('provider','other'),('authenticated',True),('bar_count',2),
                            ('market','KRW-ETH'),('unit_minutes',5),('end_exclusive_ms',BASE+4*HOUR),
                            ('retrieved_at','2026-09-30T00:00:00Z'),('missing_intervals',True),
                            ('schema_version',True),('candles_sha256','bad'),('token','secret')]:
            p=copy.deepcopy(self.fixture['data_provenance']);p[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.build(p)

    def test_reader_rejects_explicit_null_or_forged_source(self):
        for p in (None,{},dict(self.fixture['data_provenance'],authenticated=True)):
            report=self.build();report['data_provenance']=p
            with self.assertRaises(ValueError):self.report.validate_report(report)

    def test_export_preflight_validates_imported_hash_before_execution(self):
        f=self.fixture
        self.assertEqual(self.export.load_fixture(json.dumps(f).encode()),f)
        f['candles'][0][4]='100'
        with self.assertRaises(ValueError):self.export.load_fixture(json.dumps(f).encode())

    def test_legacy_float_parsing_unchanged_but_invalid_json_is_rejected(self):
        raw=b'{"mode":"paper","candles":[[0,0.10000000000000001,2,0.01,1,5]]}'
        self.assertIsInstance(self.export.load_fixture(raw)['candles'][0][1],float)
        for raw in [b'{"mode":"live","mode":"paper"}',b'{"candles":[[0,NaN,1,1,1,1]]}']:
            with self.assertRaises(ValueError):self.export.load_fixture(raw)


if __name__=='__main__':unittest.main()
