"""Shared producer input rules, preserved values and execution-free delegation."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('apps/server', 'packages/data', 'tests'):
    sys.path.insert(0, str(ROOT / part))
from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
from trading_bot import walk_forward_research as producer


class ResearchInputTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('trading_bot.research_input')
            self.loader = importlib.import_module('trading_bot.fixture_input').load_fixture
        except ModuleNotFoundError:
            self.fail('shared execution-free research input validation is missing')
        self.prepare = self.module.prepare_research_input

    def prepare_data(self, data=None, **kwargs):
        options = dict(candidates=CANDIDATES, settings=SETTINGS)
        options.update(kwargs)
        return self.prepare(dataset() if data is None else data, 'a'*64, **options)

    def test_preparation_preserves_exact_numeric_strings_and_input(self):
        data = dataset()
        data['candles'][0][1:] = ['1.000000000000000001', '2.000', '0.1', '1.00', '0E-9']
        before = copy.deepcopy(data)
        spec, clean = self.prepare_data(data)
        self.assertEqual(clean[0], (0, '1.000000000000000001', '2.000', '0.1', '1.00', '0E-9'))
        self.assertIsInstance(clean, tuple)
        self.assertTrue(all(isinstance(row, tuple) for row in clean))
        self.assertEqual(spec['fixture_sha256'], 'a'*64)
        self.assertEqual(spec['bars'], 60)
        self.assertEqual(data, before)

    def test_missing_synthetic_metadata_retains_producer_defaults(self):
        data = dataset()
        for field in ('mode', 'symbol', 'timeframe'):
            data.pop(field)
        spec, clean = self.prepare_data(data)
        self.assertEqual((spec['symbol'], spec['timeframe']), ('BTC/USDT', '1h'))
        self.assertEqual(clean[0][0], 0)

    def test_loader_retains_legacy_float_decoding_and_exporter_interface(self):
        data = dataset()
        data['candles'][0][1] = 100.1
        decoded = self.loader(json.dumps(data).encode())
        self.assertIs(type(decoded['candles'][0][1]), float)
        self.assertEqual(self.prepare_data(decoded)[1][0][1], '100.1')
        module_spec = importlib.util.spec_from_file_location('shared_loader_export', ROOT/'tools/paper_replay_dashboard.py')
        exporter = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(exporter)
        self.assertIs(exporter.load_fixture, self.loader)

    def test_loader_rejects_duplicate_keys_nonfinite_constants_and_bad_shapes(self):
        samples = (b'{"candles":[],"candles":[]}', b'{"candles":NaN}',
                   b'{"candles":[[0,1,2,1,1,1]],"extra":{"x":1,"x":1}}',
                   b'{"candles":[[0,1,2,1,1]]}', b'{"mode":"live","candles":[[0,1,2,1,1,1]]}')
        for raw in samples:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.loader(raw)

    def test_nonfinite_boolean_negative_or_out_of_range_prices_are_rejected(self):
        for value in (True, None, 'NaN', float('inf'), '-1', '0', '1e41'):
            data = dataset()
            data['candles'][5][4] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare_data(data)
        for column, value in ((2, '99'), (3, '101'), (5, '-1')):
            data = dataset()
            data['candles'][5][column] = value
            with self.subTest(column=column), self.assertRaises(ValueError):
                self.prepare_data(data)

    def test_gaps_duplicates_out_of_order_and_noninteger_timestamps_are_rejected(self):
        for value in (True, 5.0*3600000, 4*3600000, 6*3600000, -1, 253402300800000):
            data = dataset()
            data['candles'][5][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare_data(data)

    def test_imported_provenance_uses_existing_strict_validation(self):
        from test_market_history_integration import synthetic_market_fixture
        data = synthetic_market_fixture()
        spec, clean = self.prepare_data(data)
        self.assertEqual(spec['symbol'], 'BTC/KRW')
        self.assertEqual(spec['bars'], 90)
        self.assertEqual(clean[-1][0], data['candles'][-1][0])
        for change in ('hash', 'authenticated', 'numeric'):
            broken = copy.deepcopy(data)
            if change == 'hash':
                broken['data_provenance']['candles_sha256'] = 'b'*64
            elif change == 'authenticated':
                broken['data_provenance']['authenticated'] = True
            else:
                broken['candles'][0][1] = float(broken['candles'][0][1])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare_data(broken)

    def test_preflight_failure_precedence_is_preserved(self):
        bad_mode = dict(dataset(), mode='live')
        bad_shape = dataset()
        bad_shape['symbol'] = False
        bad_shape['candles'][-1] = [0]
        bad_ohlc = dataset()
        bad_ohlc['candles'][0][4] = '-1'
        invalid_candidate = [dict(CANDIDATES[0], fast_period=4, slow_period=3)]
        cases = ((bad_mode, {'robustness': 1}, 'robustness must be boolean'),
                 (bad_mode, {'settings': {'unknown': 1}}, 'paper history required'),
                 (bad_shape, {}, 'six-column candles required'),
                 (bad_ohlc, {'candidates': invalid_candidate}, 'fast must precede slow'),
                 (dict(bad_ohlc, candles=bad_ohlc['candles'][:10]), {}, 'history has no complete test window'))
        for data, options, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.prepare_data(data, **options)

    def test_robustness_constraints_are_not_downgraded_to_legacy(self):
        settings = dict(SETTINGS, fee_pct='34')
        self.prepare_data(settings=settings, robustness=False)
        with self.assertRaises(ValueError):
            self.prepare_data(settings=settings, robustness=True)

    def test_producer_delegates_once_before_any_evaluation(self):
        data = dataset()
        evaluate = Mock(side_effect=lambda *args: metrics())
        with patch.object(producer, 'prepare_research_input', wraps=self.prepare) as prepare:
            report = producer.run_research(data, 'a'*64, candidates=CANDIDATES,
                                          settings=SETTINGS, evaluator=evaluate)
        prepare.assert_called_once_with(data, 'a'*64, candidates=CANDIDATES,
                                        settings=SETTINGS, robustness=False)
        self.assertEqual(report['summary']['total_folds'], 3)
        self.assertTrue(evaluate.called)
        evaluate.reset_mock()
        with patch.object(producer, 'prepare_research_input', side_effect=ValueError('invalid input')):
            with self.assertRaisesRegex(ValueError, 'invalid input'):
                producer.run_research(data, 'a'*64, evaluator=evaluate)
        evaluate.assert_not_called()

    def test_producer_evaluator_consumes_shared_cleaned_rows(self):
        spec, clean = self.prepare_data()
        clean = tuple((row[0], '200', '210', '190', '200', '10') for row in clean)
        observed = []
        def evaluate(candidate, warmup, bars, options):
            observed.extend(bars)
            observed.extend(warmup)
            return metrics()
        with patch.object(producer, 'prepare_research_input', return_value=(spec, clean)):
            producer.run_research(dataset(), 'a'*64, candidates=CANDIDATES,
                                  settings=SETTINGS, evaluator=evaluate)
        self.assertTrue(observed)
        self.assertTrue(all(row[1] == '200' and row[4] == '200' for row in observed))


if __name__ == '__main__':
    unittest.main()
