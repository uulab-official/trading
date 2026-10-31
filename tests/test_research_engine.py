"""Original-engine integration. Network is forbidden throughout evaluation."""
import asyncio
from contextlib import ExitStack
from decimal import Decimal as D
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'packages/core', ROOT / 'packages/data', ROOT / 'packages/strategy', ROOT / 'apps/server', ROOT / 'tools'):
    sys.path.insert(0, str(path))
from trading_bot.walk_forward_research import run_research, evaluate_with_runner
from trading_bot.paper_runner import PaperRunner, PaperRunConfig
from quant_research import export_research
CANDIDATES = [dict(id='ma-fast', strategy='ma_cross', fast_period=2, slow_period=5), dict(id='ma-slow', strategy='ma_cross', fast_period=3, slow_period=9)]
SETTINGS = dict(train_size=72, test_size=20, gap=2, warmup=60, min_training_fills=2, max_daily_loss_pct='100', max_drawdown_pct='100')

def history(n=120, quote='USDT'):
    pattern = [90, 91, 94, 99, 105, 111, 113, 109, 103, 97, 92, 89]
    return dict(mode='paper', symbol='BTC/' + quote, timeframe='1h', candles=[[1700000000000 + i * 3600000, str(pattern[i % 12]), str(pattern[i % 12] + 2), str(pattern[i % 12] - 2), str(pattern[i % 12]), '100'] for i in range(n)])

class EngineResearchTests(unittest.TestCase):

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            stack.enter_context(patch(target, side_effect=AssertionError('research must stay offline')))

    def test_existing_engine_generates_fills_fees_and_repeatable_selection(self):
        a = run_research(history(), 'a' * 64, candidates=CANDIDATES, settings=SETTINGS)
        b = run_research(history(), 'a' * 64, candidates=CANDIDATES, settings=SETTINGS)
        self.assertEqual(a['folds'], b['folds'])
        self.assertEqual(a['run_id'], b['run_id'])
        self.assertGreater(a['summary']['evaluated_folds'], 0)
        self.assertGreater(sum((f['test']['fills'] for f in a['folds'] if f['test'])), 0)
        self.assertTrue(any((D(s['metrics']['fees']) > 0 for f in a['folds'] for s in f['train_scores'])))

    def test_no_warmup_fills_or_signal_executes_at_first_test_bar(self):
        seen = []
        original = PaperRunner.run

        async def spy(self, *a, **kw):
            result = await original(self, *a, **kw)
            seen.append(result)
            return result
        data = history()
        bars = data['candles']
        options = dict(symbol=data['symbol'], timeframe='1h', max_daily_loss_pct='100', max_drawdown_pct='100')
        with patch.object(PaperRunner, 'run', spy):
            evaluate_with_runner(CANDIDATES[0], bars[:60], bars[60:100], options)
        self.assertTrue(seen[0]['trades'])
        self.assertTrue(all((t['timestamp'] >= bars[61][0] for t in seen[0]['trades'])))
        self.assertEqual(seen[0]['bars_processed'], 40)

    def test_segment_agrees_with_unmodified_runner_when_no_warmup(self):
        data = history()
        bars = data['candles'][:72]
        c = CANDIDATES[0]
        direct = asyncio.run(PaperRunner(PaperRunConfig(fast_period=2, slow_period=5)).run(bars))
        m = evaluate_with_runner(c, [], bars, dict(symbol='BTC/USDT', timeframe='1h'))
        self.assertGreater(len(direct['trades']), 0)
        self.assertEqual(D(m['equity']), D(direct['equity']))
        self.assertEqual(D(m['fees']), D(direct['total_fees']))
        self.assertEqual(m['fills'], len(direct['trades']))

    def test_future_change_never_reselects_first_training_winner(self):
        data = history()
        a = run_research(data, 'a' * 64, candidates=CANDIDATES, settings=SETTINGS)
        for bar in data['candles'][74:]:
            bar[1:5] = ['200', '201', '199', '200']
        b = run_research(data, 'b' * 64, candidates=CANDIDATES, settings=SETTINGS)
        self.assertEqual(a['folds'][0]['train_scores'], b['folds'][0]['train_scores'])
        self.assertEqual(a['folds'][0]['selected'], b['folds'][0]['selected'])

    def test_cli_export_krw_idempotence_and_input_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.json'
            out = Path(temp) / 'result.json'
            path.write_text(json.dumps(history(quote='KRW')))
            before = path.read_bytes()
            first = export_research(path, out, candidates=CANDIDATES, settings=SETTINGS)
            saved = out.read_bytes()
            second = export_research(path, out, candidates=CANDIDATES, settings=SETTINGS)
            self.assertEqual(first, second)
            self.assertEqual(out.read_bytes(), saved)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(first['experiment']['symbol'], 'BTC/KRW')
            with self.assertRaises(ValueError):
                export_research(path, out, candidates=CANDIDATES, settings={**SETTINGS, 'gap': 3})
            self.assertEqual(out.read_bytes(), saved)

    def test_existing_fifty_bar_gate_is_not_bypassed(self):
        # The unchanged Strategy.on_candle gate needs 50 observed bars.
        # 36 scored bars + at most 12 warm-up bars must not create trades.
        report = run_research(history(), 'a' * 64, candidates=CANDIDATES,
                              settings={**SETTINGS, 'train_size': 36, 'warmup': 12})
        self.assertEqual(report['summary']['evaluated_folds'], 0)
        self.assertTrue(all(f['selected'] is None and f['test'] is None
                            for f in report['folds']))

    def test_source_metadata_fails_before_engine(self):
        data = history()
        data['data_provenance'] = {'provider': 'upbit', 'checksum': 'invalid'}
        with patch.object(PaperRunner, 'run', side_effect=AssertionError('must not execute')):
            with self.assertRaises(ValueError):
                run_research(data, 'a' * 64, candidates=CANDIDATES, settings=SETTINGS)
if __name__ == '__main__':
    unittest.main()
