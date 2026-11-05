"""Dependency-free contracts; engine integration is exercised separately."""
import copy
import importlib.util
import json
from pathlib import Path
from decimal import Decimal as D
import tempfile
import unittest
from unittest.mock import patch
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))

def dataset(n=60):
    return {'mode': 'paper', 'symbol': 'BTC/USDT', 'timeframe': '1h', 'candles': [[i * 3600000, '100', '110', '90', '100', '10'] for i in range(n)]}
CANDIDATES = [{'id': 'a', 'strategy': 'ma_cross', 'fast_period': 2, 'slow_period': 3}, {'id': 'b', 'strategy': 'ma_cross', 'fast_period': 2, 'slow_period': 4}]
SETTINGS = {'train_size': 20, 'test_size': 10, 'gap': 2, 'warmup': 5, 'min_training_fills': 2}

def metrics(return_pct='1', fills=2):
    equity = D('10000') * (1 + D(return_pct) / 100)
    return {'cash': str(equity), 'equity': str(equity), 'position_value': '0', 'fees': '1' if fills else '0', 'fills': fills, 'return_pct': return_pct, 'daily_loss_halted': False, 'drawdown_halted': False}

class ResearchTests(unittest.TestCase):

    def setUp(self):
        path = ROOT / 'apps/server/trading_bot/walk_forward_research.py'
        self.assertTrue(path.exists(), 'research implementation missing')
        from trading_bot import walk_forward_research as m
        from trading_bot import research_report as report
        self.m, self.r = (m, report)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_study(self, data=None, evaluator=None, **settings):
        return self.m.run_research(data or dataset(), 'a' * 64, candidates=CANDIDATES, settings={**SETTINGS, **settings}, evaluator=evaluator or (lambda *args: metrics()))

    def test_selection_is_training_only_and_test_is_selected_once(self):
        calls = []

        def evaluate(candidate, warmup, bars, options):
            calls.append((candidate['id'], [b[0] for b in warmup], [b[0] for b in bars]))
            return metrics('2' if candidate['id'] == 'b' else '1')
        report = self.run_study(evaluator=evaluate)
        self.assertEqual(len(report['folds']), 3)
        self.assertEqual(len(calls), 9)
        for i, fold in enumerate(report['folds']):
            self.assertEqual(fold['selected'], 'b')
            self.assertEqual([x[0] for x in calls[3 * i:3 * i + 3]], ['a', 'b', 'b'])
            self.assertTrue(all((t < fold['test_start'] * 3600000 for t in calls[3 * i][2])))
            self.assertTrue(all((t < calls[3 * i + 2][2][0] for t in calls[3 * i + 2][1])))
        self.assertEqual(report['summary']['unused_tail_bars'], 8)

    def test_future_prices_cannot_change_first_selection(self):

        def evaluate(candidate, warmup, bars, options):
            return metrics(str(sum((D(b[4]) for b in bars)) / 1000) if candidate['id'] == 'a' else '1')
        before = self.run_study(evaluator=evaluate)
        altered = dataset()
        for b in altered['candles'][22:]:
            b[1:5] = ['900', '910', '890', '900']
        after = self.run_study(altered, evaluate)
        self.assertEqual(before['folds'][0]['train_scores'], after['folds'][0]['train_scores'])
        self.assertEqual(before['folds'][0]['selected'], after['folds'][0]['selected'])
        self.assertNotEqual(before['folds'][0]['test'], after['folds'][0]['test'])

    def test_tie_break_is_id_not_input_order(self):
        report = self.m.run_research(dataset(), 'a' * 64, candidates=CANDIDATES[::-1], settings=SETTINGS, evaluator=lambda *args: metrics())
        self.assertTrue(all((f['selected'] == 'a' for f in report['folds'])))

    def test_insufficient_fills_skip_without_test_evaluation(self):
        calls = []
        report = self.run_study(evaluator=lambda *args: calls.append(args) or metrics('0', 0))
        self.assertEqual(len(calls), 6)
        self.assertEqual(report['summary']['skipped_folds'], 3)
        self.assertEqual(report['summary']['mean_test_return_pct'], '0')
        self.assertTrue(all((f['selected'] is None and f['test'] is None for f in report['folds'])))

    def test_negative_test_returns_are_kept(self):
        calls = [0]

        def evaluate(*args):
            calls[0] += 1
            return metrics('-5' if calls[0] % 3 == 0 else '1')
        report = self.run_study(evaluator=evaluate)
        self.assertEqual(D(report['summary']['mean_test_return_pct']), D('-5'))
        self.assertEqual(report['summary']['positive_folds'], 0)

    def test_anchored_folds_and_gap(self):
        r = self.run_study(anchored=True)
        self.assertEqual([f['train_start'] for f in r['folds']], [0, 0, 0])
        self.assertEqual([f['train_end'] for f in r['folds']], [20, 30, 40])
        self.assertTrue(all((f['train_end'] + 2 == f['test_start'] for f in r['folds'])))

    def test_invalid_settings_and_candidates_fail_before_evaluation(self):
        for kwargs in ({'train_size': True}, {'test_size': 0}, {'gap': -1}, {'warmup': 2}, {'min_training_fills': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_study(**kwargs)
        for candidates in ([], CANDIDATES * 5, [CANDIDATES[0], {**CANDIDATES[0], 'id': 'other'}], [{**CANDIDATES[0], 'api_key': 'secret'}]):
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                self.m.run_research(dataset(), 'a' * 64, candidates=candidates, settings=SETTINGS)

    def test_bad_history_rejected_before_evaluation(self):
        for mutate in (lambda d: d['candles'].pop(3), lambda d: d['candles'][3].__setitem__(4, 'NaN'), lambda d: d.update(mode='live'), lambda d: d['candles'][3].__setitem__(0, True)):
            data = dataset()
            mutate(data)
            with self.assertRaises(ValueError):
                self.run_study(data)

    def test_invalid_evaluator_metrics_and_errors_do_not_disappear(self):
        for bad in ({**metrics(), 'fees': 'NaN'}, {**metrics(), 'return_pct': '500'}, {**metrics(), 'equity': '0'}, {**metrics(), 'fills': True}):
            with self.assertRaises(ValueError):
                self.run_study(evaluator=lambda *args: bad)
        with self.assertRaises(RuntimeError):
            self.run_study(evaluator=lambda *a: (_ for _ in ()).throw(RuntimeError('failure')))

    def test_report_validation_recomputes_selection_summary_and_identity(self):
        r = self.run_study()
        for mutate in (lambda d: d['folds'][0].update(selected='b'), lambda d: d['summary'].update(positive_folds=99), lambda d: d['folds'][0].update(test_start=0), lambda d: d.update(run_id='b' * 64), lambda d: d.update(live_enabled=True)):
            bad = copy.deepcopy(r)
            mutate(bad)
            with self.assertRaises(ValueError):
                self.r.validate_report(bad)
        self.assertEqual(self.r.validate_report(r), r)

    def test_report_publication_is_no_clobber_and_idempotent(self):
        r = self.run_study()
        path = Path(self.tmp.name) / 'research.json'
        self.r.write_report(path, r)
        before = path.read_bytes()
        self.r.write_report(path, self.run_study())
        self.assertEqual(path.read_bytes(), before)
        path.write_text('unrelated')
        with self.assertRaises(ValueError):
            self.r.write_report(path, r)
        self.assertEqual(path.read_text(), 'unrelated')

    def test_reader_rejects_unknown_secret_fields_duplicates_and_nonregular(self):
        r = self.run_study()
        path = Path(self.tmp.name) / 'research.json'
        r['token'] = 'secret'
        with self.assertRaises(ValueError):
            self.r.write_report(path, r)
        path.write_text('{"mode":"paper","mode":"live"}')
        with self.assertRaises(ValueError):
            self.r.read_report(path)
        path.unlink()
        path.symlink_to(Path(self.tmp.name) / 'missing')
        with self.assertRaises(ValueError):
            self.r.write_report(path, self.run_study())

    def test_failed_publish_preserves_other_file_and_removes_temp(self):
        r = self.run_study()
        path = Path(self.tmp.name) / 'research.json'

        def collision(src, dst):
            Path(dst).write_text('racer')
            raise FileExistsError()
        with patch.object(self.r.os, 'link', side_effect=collision):
            with self.assertRaises(ValueError):
                self.r.write_report(path, r)
        self.assertEqual(path.read_text(), 'racer')
        self.assertFalse(list(path.parent.glob('.research-*.tmp')))

    def test_work_budget_and_insufficient_data(self):
        with self.assertRaises(ValueError):
            self.run_study(dataset(10))
        with self.assertRaises(ValueError):
            self.run_study(dataset(5000), train_size=1000, test_size=1000, anchored=True)

    def test_malformed_first_bar_is_value_error(self):
        for row in ([], None, 42, 'bad'):
            d = dataset()
            d['candles'][0] = row
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.run_study(d)

    def test_selection_does_not_depend_on_ambient_decimal_context(self):
        from decimal import localcontext
        scores = [{'candidate_id': 'a', 'metrics': metrics('1.123451')}, {'candidate_id': 'b', 'metrics': metrics('1.123459')}]
        with localcontext() as ctx:
            ctx.prec = 3
            self.assertEqual(self.r.selection(scores, 2), 'b')

    def test_summary_boolean_counts_and_nonfinite_values_are_rejected(self):
        r = self.run_study()
        for field, value in [('skipped_folds', False), ('mean_test_return_pct', 'NaN')]:
            bad = copy.deepcopy(r)
            bad['summary'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.r.validate_report(bad)
if __name__ == '__main__':
    unittest.main()
