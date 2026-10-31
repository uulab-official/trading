"""Actual existing engine integration; no replacement runner and no network."""
from contextlib import ExitStack, contextmanager
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@contextmanager
def offline():
    with ExitStack() as stack:
        for name in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            stack.enter_context(patch(name, side_effect=AssertionError('replay must remain offline')))
        yield


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.export = load('paper_replay_export', 'tools/paper_replay_dashboard.py')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root/'fixture.json'
        self.fixture.write_bytes((ROOT/'config/examples/offline_fixture.json').read_bytes())
        self.checkpoint = self.root/'checkpoint.json'; self.report = self.root/'report.json'

    def run_export(self, **options):
        with offline():
            return self.export.export_replay(self.fixture, self.checkpoint, self.report, **options)

    def test_original_runner_resume_and_http_account_projection(self):
        first = self.run_export(); before = self.report.read_bytes()
        self.assertGreater(first['fill_count'], 0)
        second = self.run_export()
        self.assertEqual(first, second); self.assertEqual(before, self.report.read_bytes())
        checkpoint = json.loads(self.checkpoint.read_text())
        self.assertEqual(checkpoint['next_index'], first['bars_processed'])
        self.assertEqual(len(checkpoint['exchange']['trades']), first['fill_count'])
        server_module = load('real_replay_server', 'apps/dashboard/server.py')
        server = server_module.create_server(None, 0, replay_path=self.report)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/replay', timeout=5) as response:
                projected = json.load(response)
            self.assertEqual(projected['account'], first['account'])
            self.assertEqual(projected['run_id'], first['run_id'])
            self.assertFalse(projected['live_enabled'])
        finally:
            server.shutdown(); server.server_close(); thread.join(5)

    def test_tampered_checkpoint_cannot_replace_a_good_report(self):
        self.run_export(); before = self.report.read_bytes()
        state = json.loads(self.checkpoint.read_text()); state['peak_equity'] = '999999999'
        self.checkpoint.write_text(json.dumps(state))
        with self.assertRaises(ValueError): self.run_export()
        self.assertEqual(before, self.report.read_bytes())

    def test_new_experiment_requires_new_report_path(self):
        self.run_export(); before = self.checkpoint.read_bytes()
        with self.assertRaises(ValueError): self.run_export(fee_pct=Decimal('2'))
        self.assertEqual(before, self.checkpoint.read_bytes())

    def test_failed_publication_can_regenerate_without_duplicate_fills(self):
        with patch.object(self.export, 'write_report', side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError): self.run_export()
        self.assertFalse(self.report.exists()); self.assertTrue(self.checkpoint.exists())
        before = json.loads(self.checkpoint.read_text())['exchange']['trades']
        report = self.run_export()
        self.assertEqual(len(before), report['fill_count'])
        self.assertEqual(before, json.loads(self.checkpoint.read_text())['exchange']['trades'])

    def test_krw_book_stays_separate(self):
        fixture = json.loads(self.fixture.read_text()); fixture['symbol'] = 'BTC/KRW'
        self.fixture.write_text(json.dumps(fixture))
        report = self.run_export()
        self.assertEqual(report['account']['currency'], 'KRW')
        self.assertTrue(all(f['fee_currency']=='KRW' for f in report['fills']))

    def test_no_signal_run_is_valid_not_an_unavailable_report(self):
        self.fixture.write_text(json.dumps(dict(mode='paper', symbol='BTC/USDT', timeframe='1h',
            candles=[[i*3600000,100,101,99,100,100] for i in range(6)])))
        report = self.run_export()
        self.assertEqual(report['fill_count'], 0)
        self.assertEqual(Decimal(report['account']['equity']), Decimal('10000'))
        self.assertEqual(Decimal(report['account']['total_fees']), 0)

    def test_busy_report_lock_cannot_start_checkpoint(self):
        from trading_bot.paper_checkpoint import locked_checkpoint, CheckpointInUseError
        with locked_checkpoint(self.report):
            with self.assertRaises(CheckpointInUseError): self.run_export()
        self.assertFalse(self.checkpoint.exists()); self.assertFalse(self.report.exists())


if __name__ == '__main__': unittest.main()
