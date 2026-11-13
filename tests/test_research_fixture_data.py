"""One observed byte snapshot, authoritative input validation and bound results."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('apps/server', 'packages/data', 'tests'):
    sys.path.insert(0, str(ROOT / part))
from test_quant_research import dataset, CANDIDATES, SETTINGS
from test_research_robustness import metrics, reference
from trading_bot.research_report import research_identity
from trading_bot.walk_forward_research import run_research


def make_report(data, raw, *, robust=False):
    return run_research(data, hashlib.sha256(raw).hexdigest(), candidates=CANDIDATES,
                        settings=SETTINGS, robustness=robust,
                        evaluator=lambda c,w,b,s: metrics(s, '1'), benchmark_evaluator=reference)


class FixtureDataTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('trading_bot.research_fixture_data')
        except ModuleNotFoundError:
            self.fail('optional original research-input inspection is missing')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'PRIVATE_SOURCE.json'
        self.data = dataset()
        self.raw = json.dumps(self.data).encode()
        self.path.write_bytes(self.raw)
        self.report = make_report(self.data, self.raw)

    def check(self, report=None):
        return self.module.check_fixture_data(self.path, self.report if report is None else report)

    def test_both_schemas_match_with_separate_exact_metadata(self):
        for robust in (False, True):
            report = make_report(self.data, self.raw, robust=robust)
            before = copy.deepcopy(report)
            with self.subTest(robust=robust):
                byte_check, data_check = self.check(report)
                metadata = dict(symbol='BTC/USDT', timeframe='1h', bars=60,
                                first_bar_ms=0, last_bar_ms=59*3600000)
                self.assertEqual(byte_check['status'], 'matched')
                self.assertEqual(data_check, dict(status='matched', scope='research_input_consistency',
                    run_id=report['run_id'], fixture_sha256=hashlib.sha256(self.raw).hexdigest(),
                    expected=metadata, observed=metadata, mismatches=[], error=None))
                self.assertEqual(report, before)
                self.assertEqual(self.path.read_bytes(), self.raw)
                self.assertNotIn(str(self.path), json.dumps(data_check))

    def test_formatting_only_hash_mismatch_keeps_data_metadata_matched(self):
        raw = self.raw + b'\n'
        self.path.write_bytes(raw)
        byte_check, data_check = self.check()
        self.assertEqual(byte_check['status'], 'mismatch')
        self.assertEqual(data_check['status'], 'matched')
        self.assertEqual(data_check['fixture_sha256'], hashlib.sha256(raw).hexdigest())

    def test_default_synthetic_metadata_and_legacy_float_input_are_accepted(self):
        for kind in ('defaults', 'float'):
            data = copy.deepcopy(self.data)
            if kind == 'defaults':
                for key in ('mode', 'symbol', 'timeframe'):
                    data.pop(key)
            else:
                data['candles'][4][4] = 100.25
            self.path.write_text(json.dumps(data))
            with self.subTest(kind=kind):
                self.assertEqual(self.check()[1]['status'], 'matched')

    def test_all_metadata_differences_follow_fixed_order(self):
        data = dataset(61)
        data.update(symbol='ETH/USDT', timeframe='2h')
        for index, row in enumerate(data['candles']):
            row[0] = (index+1)*7200000
        self.path.write_text(json.dumps(data))
        byte_check, data_check = self.check()
        self.assertEqual(byte_check['status'], 'mismatch')
        self.assertEqual(data_check['status'], 'mismatch')
        self.assertEqual(data_check['mismatches'],
                         ['symbol', 'timeframe', 'bars', 'first_bar_ms', 'last_bar_ms'])
        self.assertEqual(data_check['observed'], dict(symbol='ETH/USDT', timeframe='2h', bars=61,
                         first_bar_ms=7200000, last_bar_ms=61*7200000))

    def test_valid_imported_fixture_and_provenance_tampering(self):
        from test_market_history_integration import synthetic_market_fixture
        data = synthetic_market_fixture()
        raw = json.dumps(data).encode()
        report = make_report(data, raw, robust=True)
        self.path.write_bytes(raw)
        self.assertEqual(self.check(report)[1]['status'], 'matched')
        for change in ('hash', 'authenticated', 'decimal', 'gap'):
            broken = copy.deepcopy(data)
            if change == 'hash': broken['data_provenance']['candles_sha256'] = 'b'*64
            elif change == 'authenticated': broken['data_provenance']['authenticated'] = True
            elif change == 'decimal': broken['candles'][0][1] = '100.0'
            else: broken['candles'][5][0] += 1
            self.path.write_text(json.dumps(broken))
            with self.subTest(change=change):
                self.assertEqual(self.check(report)[1]['status'], 'invalid')

    def test_hash_match_does_not_hide_invalid_json_or_prices(self):
        invalid = dict(self.data, candles=copy.deepcopy(self.data['candles']))
        invalid['candles'][4][4] = 'PRIVATE_INVALID_PRICE'
        for raw in (b'', b'\xff', b'{"candles":[],"candles":[]}',
                    b'{"candles":NaN}', json.dumps(invalid).encode()):
            self.path.write_bytes(raw)
            report = copy.deepcopy(self.report)
            report['experiment']['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
            report['run_id'] = research_identity(report['experiment'])
            with self.subTest(raw=raw[:20]):
                byte_check, data_check = self.check(report)
                self.assertEqual(byte_check['status'], 'matched')
                self.assertEqual(data_check['status'], 'invalid')
                self.assertIsNone(data_check['observed'])
                self.assertEqual(data_check['mismatches'], [])
                self.assertEqual(data_check['error'], 'research_input_invalid')
                self.assertNotIn('PRIVATE_INVALID_PRICE', json.dumps(data_check))

    def test_bad_ohlcv_and_interior_timestamps_are_invalid_not_metadata_mismatches(self):
        for column, value in ((0, True), (0, 4*3600000), (0, 6*3600000),
                              (4, True), (4, 'NaN'), (4, '-1'), (4, 0), (5, '-1')):
            data = copy.deepcopy(self.data)
            data['candles'][5][column] = value
            self.path.write_text(json.dumps(data))
            with self.subTest(column=column, value=value):
                result = self.check()[1]
                self.assertEqual(result['status'], 'invalid')
                self.assertIsNone(result['observed'])
        raw = self.raw.replace(b'"100"', b'1e999', 1)
        self.path.write_bytes(raw)
        self.assertEqual(self.check()[1]['status'], 'invalid')

    def test_sources_rejected_by_report_windows_or_research_size_are_invalid(self):
        for bars in (10, 5001):
            self.path.write_text(json.dumps(dataset(bars)))
            with self.subTest(bars=bars):
                result = self.check()[1]
                self.assertEqual(result['status'], 'invalid')
                self.assertIsNone(result['observed'])

    def test_parser_receives_only_the_snapshot_even_if_path_changes_after_observation(self):
        from trading_bot import fixture_input
        loader = fixture_input.load_fixture
        received, opened = [], []
        original_open = os.open
        def track(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        def after_snapshot(raw):
            received.append(raw)
            self.path.write_bytes(b'changed after stable observation')
            return loader(raw)
        with patch.object(os, 'open', side_effect=track), \
                patch.object(fixture_input, 'load_fixture', side_effect=after_snapshot):
            byte_check, data_check = self.check()
        self.assertEqual(received, [self.raw])
        self.assertEqual(sum(path == self.path.name for path in opened), 1)
        self.assertEqual(byte_check['actual_sha256'], hashlib.sha256(received[0]).hexdigest())
        self.assertEqual(data_check['status'], 'matched')
        self.assertEqual(self.path.read_bytes(), b'changed after stable observation')

    def test_mid_read_change_fails_before_parsing_or_returning_a_context(self):
        from trading_bot import fixture_input
        read = os.read
        changed = False
        def race(fd, count):
            nonlocal changed
            raw = read(fd, count)
            if not changed:
                changed = True
                self.path.write_bytes(b'changed during observation')
            return raw
        with patch.object(os, 'read', side_effect=race), \
                patch.object(fixture_input, 'load_fixture', side_effect=AssertionError) as load:
            with self.assertRaisesRegex(ValueError, 'research_fixture_unavailable'):
                self.check()
            load.assert_not_called()

    def test_unexpected_loader_error_is_not_reported_as_invalid_data(self):
        from trading_bot import fixture_input
        with patch.object(fixture_input, 'load_fixture', side_effect=RuntimeError('internal failure')):
            with self.assertRaises(RuntimeError):
                self.check()

    def test_context_validator_is_pure_bound_and_returns_nested_copies(self):
        from trading_bot import fixture_input, research_input, research_fixture
        byte_check, data_check = self.check()
        original = copy.deepcopy((byte_check, data_check, self.report))
        with patch.object(fixture_input, 'load_fixture', side_effect=AssertionError), \
                patch.object(research_input, 'prepare_research_input', side_effect=AssertionError), \
                patch.object(research_fixture, 'read_fixture_snapshot', side_effect=AssertionError), \
                patch.object(os, 'open', side_effect=AssertionError):
            result = self.module.validate_fixture_data_check(data_check, self.report, byte_check)
        self.assertEqual((byte_check, data_check, self.report), original)
        self.assertEqual(result, data_check)
        result['expected']['bars'] = 1
        result['observed']['bars'] = 2
        result['mismatches'].append('bars')
        self.assertEqual((byte_check, data_check, self.report), original)

    def test_context_validator_rejects_foreign_or_inconsistent_results(self):
        byte_check, original = self.check()
        mutations = (
            lambda v: v.update(extra='PRIVATE'), lambda v: v.pop('scope'),
            lambda v: v.update(scope='authenticated_source'),
            lambda v: v.update(run_id='b'*64), lambda v: v.update(fixture_sha256='b'*64),
            lambda v: v.update(status='mismatch'), lambda v: v.update(mismatches=['bars']),
            lambda v: v.update(error='PRIVATE'), lambda v: v.update(observed=None),
            lambda v: v['expected'].update(first_bar_ms=False),
            lambda v: v['observed'].update(last_bar_ms=True),
            lambda v: v['observed'].update(last_bar_ms=1),
            lambda v: v['observed'].update(symbol='<script>'),
            lambda v: v.update(status='invalid', error='research_input_invalid'),
        )
        for mutate in mutations:
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                self.module.validate_fixture_data_check(value, self.report, byte_check)
        with self.assertRaises(ValueError):
            self.module.validate_fixture_data_check(original, self.report, None)


if __name__ == '__main__':
    unittest.main()
