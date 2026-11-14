"""Original-engine integration; acquisition uses synthetic provider-shaped rows.

No real exchange requests or financial-performance assertions. Replay is forbidden
from making socket/DNS calls and must preserve checkpoint/report identity on retry.
"""
from datetime import datetime, timedelta, timezone
import asyncio
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT/'packages/core',ROOT/'packages/data',ROOT/'packages/strategy',ROOT/'apps/server'):
    sys.path.insert(0,str(path))
from trading_data.upbit_history import UpbitHistoryClient, write_fixture

spec=importlib.util.spec_from_file_location('market_history_export',ROOT/'tools/paper_replay_dashboard.py')
export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)


def synthetic_market_fixture():
    start=datetime(2026,10,1,tzinfo=timezone.utc)
    prices=([100,99,98,97,96,95,96,97,98,99,100,101,102,101,100,99,98,97]*5)
    rows=[]
    for index,price in enumerate(prices):
        rows.append(dict(market='KRW-BTC',unit=60,
                         candle_date_time_utc=(start+timedelta(hours=index)).strftime('%Y-%m-%dT%H:%M:%S'),
                         opening_price=price,high_price=price+1,low_price=price-1,
                         trade_price=price,candle_acc_trade_volume='20'))
    client=UpbitHistoryClient(get=lambda _url:(json.dumps(rows[::-1]).encode(),{}),
                             sleep=lambda _:None,clock=lambda:start+timedelta(hours=91))
    return client.download('KRW-BTC',unit=60,count=90,end=start+timedelta(hours=90))


class MarketHistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.fixture=self.root/'market.json'
        self.checkpoint=self.root/'checkpoint.json';self.report=self.root/'report.json'
        write_fixture(self.fixture,synthetic_market_fixture())

    def replay(self):
        with patch('socket.socket.connect',side_effect=AssertionError('replay must stay offline')), \
             patch('socket.create_connection',side_effect=AssertionError('no HTTP in replay')), \
             patch('socket.getaddrinfo',side_effect=AssertionError('no DNS in replay')):
            return export.export_replay(self.fixture,self.checkpoint,self.report)

    def test_imported_fixture_runs_original_engine_and_resumes_without_duplicate_fills(self):
        first=self.replay();before=self.report.read_bytes();checkpoint=self.checkpoint.read_bytes()
        self.assertEqual(first['account']['currency'],'KRW')
        self.assertGreater(first['fill_count'],0)
        self.assertGreater(Decimal(first['account']['total_fees']),0)
        self.assertFalse(first['live_enabled'])
        self.assertEqual(first['data_provenance']['provider'],'upbit_public_rest')
        self.assertFalse(first['data_provenance']['authenticated'])
        again=self.replay()
        self.assertEqual(again,first)
        self.assertEqual(self.report.read_bytes(),before)
        self.assertEqual(self.checkpoint.read_bytes(),checkpoint)

    def test_changed_import_data_rejected_before_checkpoint_write(self):
        fixture=json.loads(self.fixture.read_text());fixture['candles'][0][4]='99'
        self.fixture.write_text(json.dumps(fixture))
        with self.assertRaises(ValueError):self.replay()
        self.assertFalse(self.checkpoint.exists());self.assertFalse(self.report.exists())

    def test_existing_report_and_checkpoint_survive_malformed_source(self):
        self.replay();before=self.report.read_bytes();saved=self.checkpoint.read_bytes()
        fixture=json.loads(self.fixture.read_text());fixture['data_provenance']['authenticated']=True
        self.fixture.write_text(json.dumps(fixture))
        with self.assertRaises(ValueError):self.replay()
        self.assertEqual(self.report.read_bytes(),before)
        self.assertEqual(self.checkpoint.read_bytes(),saved)

    def test_capture_export_runs_original_durable_engine_and_completion_audit(self):
        from trading_bot.market_capture import collect_capture, export_capture
        from trading_bot.market_paper_run import inspect_market_input, run_market_replay
        from trading_bot.durable_paper_audit import audit_durable_replay
        data=synthetic_market_fixture()
        now=datetime.fromisoformat(data['data_provenance']['retrieved_at'])
        class SyntheticClient:
            def download(self, market, *, unit, count, end):
                if (market,unit,count,int(end.timestamp()*1000)) != ('KRW-BTC',60,90,
                        data['data_provenance']['end_exclusive_ms']):
                    raise AssertionError('collector did not request the expected historical window')
                return data
        directory=self.root/'capture'; output=self.root/'captured.json'; bundle=self.root/'bundle'
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')), \
             patch('socket.create_connection',side_effect=AssertionError('network forbidden')), \
             patch('socket.getaddrinfo',side_effect=AssertionError('DNS forbidden')):
            result=collect_capture(directory, config=dict(market='KRW-BTC',unit_minutes=60,
                bootstrap_bars=90,retention_bars=100,poll_seconds=60), max_polls=1,
                client=SyntheticClient(),clock=lambda:now,sleep=lambda _:None)
            self.assertEqual(result['exit_code'],0)
            before={p.name:p.read_bytes() for p in directory.iterdir()}
            exported=export_capture(directory,output)
            inspected=inspect_market_input(output,clock=lambda:now)
            self.assertEqual(inspected['source_sha256'],exported['fixture_sha256'])
            run=asyncio.run(run_market_replay(output,directory=bundle,clock=lambda:now,
                config_options=dict(strategy='ma_cross',fast_period=3,slow_period=5)))
            audit=asyncio.run(audit_durable_replay(bundle/'run'))
        self.assertEqual(run['status'],'completed_halted')
        self.assertEqual(audit['status'],'verified_completion')
        self.assertFalse(run['live_enabled'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(output.read_bytes(),(bundle/'source.json').read_bytes())


if __name__=='__main__':unittest.main()
