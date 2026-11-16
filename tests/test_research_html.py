"""Consumer-visible guarantees for the standalone saved research projection."""
from copy import deepcopy
from html.parser import HTMLParser
import importlib
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
from trading_bot import research_report as contract
from trading_bot.walk_forward_research import run_research
from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
from test_research_robustness import (
    report as robust_report, fixture as robust_fixture, metrics as robust_metrics,
    reference as robust_reference, CANDIDATES as ROBUST_CANDIDATES, SETTINGS as ROBUST_SETTINGS,
)


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return ''.join(c.text if isinstance(c, Node) else c for c in self.children)

    def find(self, **attrs):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if all(child.attrs.get(k.replace('_', '-')) == v for k, v in attrs.items()):
                    found.append(child)
                found.extend(child.find(**attrs))
        return found


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root, self.nodes = Node(), []
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.nodes.append(node)
        self.stack[-1].children.append(node)
        if tag not in {'meta', 'br', 'hr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def report():
    return run_research(dataset(), 'a'*64, candidates=CANDIDATES, settings=SETTINGS,
                        evaluator=lambda *args: metrics())


def fixture_check(value, *, matched=True, size=123):
    expected = value['experiment']['fixture_sha256']
    actual = expected if matched else ('b'*64 if expected != 'b'*64 else 'c'*64)
    return dict(status='matched' if matched else 'mismatch', scope='local_fixture_bytes',
                algorithm='sha256', expected_sha256=expected, actual_sha256=actual,
                size_bytes=size)


def fixture_data_check(value, byte_check, *, observed=None, invalid=False):
    spec = value['experiment']
    frame = spec['timeframe']
    duration = int(frame[:-1])*{'m':60000, 'h':3600000, 'd':86400000, 'w':604800000}[frame[-1]]
    expected = {key: spec[key] for key in ('symbol', 'timeframe', 'bars', 'first_bar_ms')}
    expected['last_bar_ms'] = spec['first_bar_ms']+(spec['bars']-1)*duration
    observed = None if invalid else deepcopy(expected if observed is None else observed)
    mismatches = [] if invalid else [key for key in expected if expected[key] != observed[key]]
    return dict(status='invalid' if invalid else ('mismatch' if mismatches else 'matched'),
                scope='research_input_consistency', run_id=value['run_id'],
                fixture_sha256=byte_check['actual_sha256'], expected=expected, observed=observed,
                mismatches=mismatches, error='research_input_invalid' if invalid else None)


def skip_fold(value, index):
    fold = value['folds'][index]
    fold.update(selected=None, test=None)
    for score in fold['train_scores']:
        score['metrics'] = metrics('0', 0)
    value['summary'] = contract.summary(value['folds'], value['experiment'])


class ResearchHtmlTests(unittest.TestCase):
    def setUp(self):
        try:
            self.render = importlib.import_module('trading_bot.research_html').render_research_report
        except ModuleNotFoundError:
            self.fail('the pure saved-research HTML renderer is missing')

    def page(self, value):
        html = self.render(value)
        self.assertTrue(html.lower().startswith('<!doctype html>'))
        return Document(html)

    def field(self, node, name):
        found = node.find(data_field=name)
        self.assertEqual(len(found), 1, name)
        return found[0].text

    def test_saved_consistency_scope_is_distinct_from_execution_authentication(self):
        value = report()
        page = self.page(value)
        self.assertEqual(self.field(page.root, 'research-status'), '연구 보고서 일관성 확인')
        for phrase in ('저장된', '구조', '산술', '데이터 출처', '실행', '인증', '완료 기록',
                       '독립 자본', '산술 평균', '복리', '강제 청산', '최대 낙폭'):
            self.assertIn(phrase, page.root.text)
        self.assertNotIn('연구 검증 완료', page.root.text)
        self.assertIn(value['run_id'], page.root.text)
        self.assertIn(value['generated_at'], page.root.text)
        self.assertIn('a'*64, page.root.text)

    def test_every_fold_and_training_candidate_is_retained_in_report_order(self):
        value = report()
        page = self.page(value)
        folds = page.root.find(data_fold='1') + page.root.find(data_fold='2') + page.root.find(data_fold='3')
        self.assertEqual(len(folds), 3)
        for fold, expected in zip(folds, value['folds']):
            self.assertEqual(self.field(fold, 'selected'), expected['selected'])
            scores = [n for n in fold.find(data_role='training-score')]
            self.assertEqual([n.attrs['data-candidate-id'] for n in scores], ['a', 'b'])
            self.assertEqual(self.field(fold, 'test-range'),
                             f"[{expected['test_start']}, {expected['test_end']})")
            self.assertTrue(any(n.tag == 'details' and n.find(data_role='training-score')
                                for n in page.nodes))
        self.assertEqual(self.field(page.root, 'unused_tail_bars'), '8 개')

    def test_common_conditions_candidate_parameters_and_original_summary_are_visible(self):
        value = report()
        page = self.page(value)
        self.assertEqual(self.field(page.root, 'symbol'), 'BTC/USDT')
        self.assertEqual(self.field(page.root, 'timeframe'), '1h')
        for key, item in value['experiment']['settings'].items():
            self.assertIn(str(item) if type(item) is not bool else '롤링', self.field(page.root, key))
        for candidate in value['experiment']['candidates']:
            node = page.root.find(data_role='candidate', data_candidate_id=candidate['id'])[0]
            for item in candidate.values():
                self.assertIn(str(item), node.text)
        for key, item in value['summary'].items():
            self.assertIn(str(item), self.field(page.root, key))

    def test_exact_decimal_strings_losses_zero_exponents_and_signs_are_preserved(self):
        value = report()
        value['folds'][0]['train_scores'][0]['metrics'] = metrics('1.234567890123456789')
        original = metrics('-1.0000E-9')
        original.update(position_value='0E-9', fees='+0.000', cash='9999.99999990000',
                        equity='9999.99999990000', daily_loss_halted=True)
        value['folds'][0]['test'] = original
        value['summary'] = contract.summary(value['folds'], value['experiment'])
        contract.validate_report(value)
        page = self.page(value)
        fold = page.root.find(data_fold='1')[0]
        measured = fold.find(data_role='test-metrics')[0]
        for key in ('cash', 'equity', 'position_value', 'fees'):
            self.assertEqual(self.field(measured, key), original[key]+' USDT')
        self.assertEqual(self.field(measured, 'return_pct'), '-1.0000E-9 %')
        self.assertIn('1.234567890123456789 %', fold.text)
        self.assertIn('예', self.field(measured, 'daily_loss_halted'))
        self.assertIn('아니요', self.field(measured, 'drawdown_halted'))
        self.assertLess(float(self.field(page.root, 'worst_test_return_pct').split()[0]), 0)

    def test_mixed_skips_never_become_measured_zero_test_results(self):
        value = report()
        skip_fold(value, 1)
        page = self.page(value)
        skipped = page.root.find(data_fold='2')[0]
        self.assertIn('건너뜀', skipped.text)
        self.assertIn('측정된 테스트 결과 없음', skipped.text)
        self.assertIn('현금', skipped.text)
        self.assertEqual(skipped.find(data_role='test-metrics'), [])
        self.assertEqual(len(page.root.find(data_role='test-metrics')), 2)
        self.assertIn('0', skipped.text)  # Cash-zero summary contribution is explained.
        self.assertEqual(self.field(page.root, 'skipped_folds'), '1 개')

    def test_all_skipped_windows_remain_visible_without_test_metrics(self):
        value = report()
        for index in range(3):
            skip_fold(value, index)
        page = self.page(value)
        self.assertEqual(len(page.root.find(data_role='training-score')), 6)
        self.assertEqual(page.root.find(data_role='test-metrics'), [])
        self.assertEqual(self.field(page.root, 'mean_test_return_pct'), '0 %')
        self.assertEqual(self.field(page.root, 'evaluated_folds'), '0 개')

    def test_schema_one_explicitly_has_no_cost_robustness_diagnostics(self):
        page = self.page(report())
        self.assertIn('비용 강건성 미실시', page.root.text)
        self.assertEqual(page.root.find(data_role='cost-scenario'), [])
        self.assertEqual(page.root.find(data_role='robustness-summary'), [])
        self.assertEqual(page.root.find(data_field='mean_excess_return_pp'), [])

    def test_schema_two_keeps_all_cost_summaries_and_per_fold_metrics(self):
        value = robust_report()
        page = self.page(value)
        summaries = page.root.find(data_role='robustness-summary')
        self.assertEqual([n.attrs['data-multiplier'] for n in summaries], ['1', '2', '3'])
        for node, expected in zip(summaries, value['robustness']['summary']):
            for key, item in expected.items():
                self.assertIn(str(item), self.field(node, key))
        for index, diagnostic in enumerate(value['robustness']['folds'], 1):
            fold = page.root.find(data_fold=str(index))[0]
            rows = fold.find(data_role='cost-scenario')
            self.assertEqual([n.attrs['data-multiplier'] for n in rows], ['1', '2', '3'])
            for row, expected in zip(rows, diagnostic['scenarios']):
                for role in ('strategy', 'buy_hold'):
                    block = row.find(data_role=role)[0]
                    for key, item in expected[role].items():
                        if type(item) is not bool:
                            self.assertIn(str(item), self.field(block, key))
                for key in ('fee_pct', 'slippage_pct', 'excess_return_pp'):
                    self.assertIn(expected[key], self.field(row, key))
            for key in ('entry_open', 'terminal_close'):
                self.assertIn(diagnostic[key], self.field(fold, key))
        self.assertIn('고정', page.root.text)
        self.assertIn('두 번째 테스트 봉 시가', page.root.text)
        self.assertIn('마지막 테스트 봉 종가', page.root.text)
        self.assertIn('pp', self.field(summaries[0], 'mean_excess_return_pp'))
        self.assertIn('-1', self.field(summaries[2], 'mean_strategy_return_pct'))

    def test_skipped_schema_two_diagnostics_are_labeled_cash_reference_not_measured_test(self):
        value = run_research(robust_fixture(), 'a'*64, candidates=ROBUST_CANDIDATES,
                             settings=ROBUST_SETTINGS, robustness=True,
                             evaluator=lambda c,w,b,s: robust_metrics(s,'0',0),
                             benchmark_evaluator=robust_reference)
        page = self.page(value)
        self.assertEqual(page.root.find(data_role='test-metrics'), [])
        self.assertEqual(len(page.root.find(data_role='cost-scenario')), 6)
        for index in (1, 2):
            fold = page.root.find(data_fold=str(index))[0]
            self.assertIn('현금 유지 진단', fold.text)
            self.assertIn('측정된 테스트 결과 없음', fold.text)

    def test_included_boundaries_use_utc_integer_milliseconds_even_near_year_9999(self):
        value = report()
        value['experiment']['first_bar_ms'] = 253402300799999 - 59*3600000
        value['run_id'] = contract.research_identity(value['experiment'])
        before = os.environ.get('TZ')
        pages = []
        try:
            for zone in ('Pacific/Honolulu', 'Asia/Seoul'):
                os.environ['TZ'] = zone
                time.tzset()
                pages.append(self.render(value))
        finally:
            if before is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = before
            time.tzset()
        self.assertEqual(pages[0], pages[1])
        fold = Document(pages[0]).root.find(data_fold='1')[0]
        self.assertEqual(self.field(fold, 'train-first-utc'), '9999-12-29T12:59:59.999 UTC')
        self.assertEqual(self.field(fold, 'train-last-utc'), '9999-12-30T07:59:59.999 UTC')
        self.assertEqual(self.field(fold, 'test-first-utc'), '9999-12-30T10:59:59.999 UTC')
        self.assertEqual(self.field(fold, 'test-last-utc'), '9999-12-30T19:59:59.999 UTC')
        self.assertNotIn('9999-12-30T20:59:59.999', fold.text)

    def test_valid_unusual_timestamp_separator_is_escaped_as_text(self):
        value = report()
        value['generated_at'] = '2026-11-13<01:02:03+00:00'
        contract.validate_report(value)
        html = self.render(value)
        self.assertIn('2026-11-13&lt;01:02:03+00:00', html)
        self.assertNotIn('2026-11-13<01:02:03', html)
        self.assertIn(value['generated_at'], Document(html).root.text)

    def test_invalid_report_cannot_be_projected_as_success(self):
        for change in ('schema', 'summary', 'identity', 'selection', 'injected_timestamp', 'extra'):
            value = report()
            if change == 'schema': value['schema_version'] = 3
            elif change == 'summary': value['summary']['evaluated_folds'] = 99
            elif change == 'identity': value['run_id'] = 'b'*64
            elif change == 'selection': value['folds'][0]['selected'] = 'b'
            elif change == 'injected_timestamp': value['generated_at'] = '<script>alert(1)</script>'
            else: value['secret'] = 'do-not-display'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.render(value)

    def test_pure_projection_does_not_mutate_input_and_is_deterministic(self):
        value = robust_report()
        before = deepcopy(value)
        self.assertEqual(self.render(value), self.render(value))
        self.assertEqual(value, before)

    def test_default_explicitly_distinguishes_unchecked_fixture_from_report_consistency(self):
        value = report()
        page = self.page(value)
        self.assertEqual(self.field(page.root, 'fixture-check-status'), '원본 파일 미대조')
        self.assertEqual(len(page.root.find(data_fixture_status='not_requested')), 1)
        self.assertEqual(self.field(page.root, 'research-status'), '연구 보고서 일관성 확인')
        self.assertEqual(page.root.find(data_field='fixture-actual-sha256'), [])
        self.assertEqual(page.root.find(data_field='fixture-size-bytes'), [])
        self.assertIn('로컬 파일을 지정하지 않아', page.root.text)
        self.assertIn(value['experiment']['fixture_sha256'], page.root.text)

    def test_matched_fixture_keeps_separate_byte_scope_and_exact_evidence_for_both_schemas(self):
        for value in (report(), robust_report()):
            check = fixture_check(value)
            with self.subTest(schema=value['schema_version']):
                page = Document(self.render(value, fixture_check=check))
                section = page.root.find(data_fixture_status='matched')
                self.assertEqual(len(section), 1)
                self.assertEqual(self.field(section[0], 'fixture-check-status'), '파일 해시 일치')
                self.assertEqual(self.field(section[0], 'fixture-expected-sha256'), check['expected_sha256'])
                self.assertEqual(self.field(section[0], 'fixture-actual-sha256'), check['actual_sha256'])
                self.assertEqual(self.field(section[0], 'fixture-size-bytes'), '123 bytes')
                self.assertEqual(self.field(page.root, 'fixture_sha256'), check['expected_sha256'])
                self.assertEqual(self.field(page.root, 'generated_at'), value['generated_at'])
                self.assertEqual(self.field(page.root, 'research-status'), '연구 보고서 일관성 확인')
                for phrase in ('검사 때 읽은', '바이트', 'SHA-256', '데이터 출처', '가격 데이터의 유효성',
                               '실행', '인증', '이후 파일 변경'):
                    self.assertIn(phrase, section[0].text)
                self.assertNotIn('열어 확인하지 않았', page.root.text)
                self.assertEqual(len(page.root.find(data_role='training-score')),
                                 sum(len(fold['train_scores']) for fold in value['folds']))
                self.assertEqual(len(page.root.find(data_role='cost-scenario')),
                                 3*len(value['folds']) if value['schema_version'] == 2 else 0)

    def test_mismatched_fixture_is_visible_without_discarding_saved_research_values(self):
        value = robust_report()
        check = fixture_check(value, matched=False)
        page = Document(self.render(value, fixture_check=check))
        section = page.root.find(data_fixture_status='mismatch')
        self.assertEqual(len(section), 1)
        status = section[0].find(data_field='fixture-check-status')[0]
        self.assertEqual(status.text, '파일 해시 불일치')
        self.assertIn('mismatch', status.attrs['class'].split())
        self.assertEqual(self.field(section[0], 'fixture-actual-sha256'), check['actual_sha256'])
        self.assertIn('다릅니다', section[0].text)
        self.assertEqual(self.field(page.root, 'research-status'), '연구 보고서 일관성 확인')
        self.assertEqual(self.field(page.root, 'mean_test_return_pct'),
                         value['summary']['mean_test_return_pct']+' %')
        self.assertEqual(len(page.root.find(data_role='cost-scenario')), 3*len(value['folds']))

    def test_zero_and_maximum_comparison_sizes_are_displayed_without_truthiness_loss(self):
        value = report()
        for size in (0, 32*1024*1024):
            with self.subTest(size=size):
                page = Document(self.render(value, fixture_check=fixture_check(value, size=size)))
                self.assertEqual(self.field(page.root, 'fixture-size-bytes'), str(size)+' bytes')

    def test_inconsistent_foreign_or_injected_fixture_context_is_rejected(self):
        value = report()
        checks = [[], '', False]
        changes = (
            {'status': 'mismatch'}, {'actual_sha256': 'b'*64},
            {'status': 'not_requested'}, {'scope': 'authenticated_source'},
            {'algorithm': 'sha512'}, {'size_bytes': True}, {'size_bytes': -1},
            {'size_bytes': 32*1024*1024+1}, {'actual_sha256': '<script>alert(1)</script>'},
            {'expected_sha256': 'b'*64, 'actual_sha256': 'b'*64},
            {'path': '/private/do-not-display'},
        )
        checks.extend(dict(fixture_check(value), **change) for change in changes)
        missing = fixture_check(value)
        del missing['actual_sha256']
        checks.append(missing)
        for check in checks:
            with self.subTest(check=check), self.assertRaises(ValueError):
                self.render(value, fixture_check=check)

    def test_fixture_context_projection_has_no_io_and_does_not_mutate_inputs(self):
        from trading_bot import research_fixture
        value = robust_report()
        check = fixture_check(value, matched=False)
        original = deepcopy((value, check))
        with patch('builtins.open', side_effect=AssertionError('renderer opened a file')), \
                patch('os.open', side_effect=AssertionError('renderer opened a descriptor')), \
                patch('os.stat', side_effect=AssertionError('renderer inspected a file')), \
                patch('os.lstat', side_effect=AssertionError('renderer inspected a link')), \
                patch.object(Path, 'open', side_effect=AssertionError('renderer opened a path')), \
                patch.object(research_fixture, 'check_fixture',
                             side_effect=AssertionError('renderer reran the source check')):
            self.assertEqual(self.render(value, fixture_check=check),
                             self.render(value, fixture_check=check))
        self.assertEqual((value, check), original)

    def test_default_and_hash_only_paths_explicitly_leave_input_structure_unchecked(self):
        value = report()
        for byte_check in (None, fixture_check(value), fixture_check(value, matched=False)):
            with self.subTest(byte_check=byte_check):
                page = Document(self.render(value, fixture_check=byte_check))
                sections = page.root.find(data_fixture_data_status='not_requested')
                self.assertEqual(len(sections), 1)
                self.assertEqual(self.field(sections[0], 'fixture-data-status'), '입력 구조 미검사')
                self.assertEqual(sections[0].find(data_field='fixture-data-observed-symbol'), [])
                self.assertIn('요청하지 않았', sections[0].text)

    def test_input_metadata_match_can_coexist_with_byte_mismatch_for_both_schemas(self):
        for value in (report(), robust_report()):
            byte_check = fixture_check(value, matched=False)
            data_check = fixture_data_check(value, byte_check)
            with self.subTest(schema=value['schema_version']):
                page = Document(self.render(value, fixture_check=byte_check,
                                            fixture_data_check=data_check))
                section = page.root.find(data_fixture_data_status='matched')[0]
                self.assertEqual(self.field(section, 'fixture-data-status'), '입력 구조·정보 일치')
                self.assertEqual(self.field(page.root, 'fixture-check-status'), '파일 해시 불일치')
                self.assertEqual(self.field(page.root, 'research-status'), '연구 보고서 일관성 확인')
                for role in ('expected', 'observed'):
                    for key in ('symbol', 'timeframe'):
                        self.assertEqual(self.field(section, f'fixture-data-{role}-{key}'),
                                         data_check[role][key])
                    self.assertEqual(self.field(section, f'fixture-data-{role}-bars'),
                                     str(value['experiment']['bars'])+' 개')
                    self.assertEqual(self.field(section, f'fixture-data-{role}-first_bar_ms'),
                                     '1970-01-01T00:00:00.000 UTC')
                rows = section.find(data_role='fixture-data-row')
                self.assertEqual([node.attrs['data-metadata-field'] for node in rows],
                                 ['symbol', 'timeframe', 'bars', 'first_bar_ms', 'last_bar_ms'])
                for phrase in ('후보', '설정', '스키마', '입력 규칙', '데이터 출처', '가격의 사실성',
                               '재현', '실제 실행'):
                    self.assertIn(phrase, section.text)
                self.assertEqual(len(page.root.find(data_role='training-score')),
                                 sum(len(fold['train_scores']) for fold in value['folds']))
                self.assertEqual(len(page.root.find(data_role='cost-scenario')),
                                 3*len(value['folds']) if value['schema_version'] == 2 else 0)

    def test_differing_metadata_fields_have_ordered_exact_values_and_textual_markers(self):
        value = report()
        byte_check = fixture_check(value, matched=False)
        observed = dict(symbol='ETH/USDT', timeframe='1h', bars=61,
                        first_bar_ms=3600000, last_bar_ms=219600000)
        data_check = fixture_data_check(value, byte_check, observed=observed)
        page = Document(self.render(value, fixture_check=byte_check, fixture_data_check=data_check))
        section = page.root.find(data_fixture_data_status='mismatch')[0]
        self.assertEqual(self.field(section, 'fixture-data-status'), '보고서 입력 정보 불일치')
        self.assertEqual(self.field(section, 'fixture-data-observed-symbol'), 'ETH/USDT')
        self.assertEqual(self.field(section, 'fixture-data-observed-bars'), '61 개')
        self.assertEqual(self.field(section, 'fixture-data-observed-first_bar_ms'),
                         '1970-01-01T01:00:00.000 UTC')
        self.assertEqual(self.field(section, 'fixture-data-observed-last_bar_ms'),
                         '1970-01-03T13:00:00.000 UTC')
        for row in section.find(data_role='fixture-data-row'):
            key = row.attrs['data-metadata-field']
            expected = '불일치' if key in data_check['mismatches'] else '일치'
            self.assertEqual(self.field(row, 'fixture-data-match-'+key), expected)
        self.assertEqual(self.field(page.root, 'mean_test_return_pct'),
                         value['summary']['mean_test_return_pct']+' %')

    def test_invalid_input_has_no_invented_observed_values_even_when_hash_matches(self):
        value = robust_report()
        byte_check = fixture_check(value)
        data_check = fixture_data_check(value, byte_check, invalid=True)
        page = Document(self.render(value, fixture_check=byte_check, fixture_data_check=data_check))
        section = page.root.find(data_fixture_data_status='invalid')[0]
        self.assertEqual(self.field(section, 'fixture-data-status'), '연구 입력 형식 오류')
        self.assertEqual(self.field(page.root, 'fixture-check-status'), '파일 해시 일치')
        self.assertIn('관측 정보', section.text)
        self.assertIn('표시하지 않습니다', section.text)
        for key in data_check['expected']:
            self.assertEqual(section.find(data_field='fixture-data-observed-'+key), [])
        self.assertEqual(self.field(section, 'fixture-data-expected-bars'), '90 개')
        self.assertEqual(len(page.root.find(data_role='cost-scenario')), 6)

    def test_data_context_requires_matching_report_and_byte_context_and_strict_fields(self):
        value = report()
        byte_check = fixture_check(value, matched=False)
        valid = fixture_data_check(value, byte_check)
        with self.assertRaises(ValueError):
            self.render(value, fixture_data_check=valid)
        cases = [False, [], {}, dict(valid, run_id='b'*64),
                 dict(valid, fixture_sha256='a'*64), dict(valid, scope='provider_authenticated'),
                 dict(valid, status='mismatch'), dict(valid, mismatches=['symbol']),
                 dict(valid, error='PRIVATE_DETAILS'), dict(valid, path='/private/hidden')]
        foreign_expected = deepcopy(valid)
        foreign_expected['expected']['symbol'] = 'ETH/USDT'
        cases.append(foreign_expected)
        invalid_observed = deepcopy(valid)
        invalid_observed['observed']['bars'] = True
        cases.append(invalid_observed)
        wrong_end = deepcopy(valid)
        wrong_end['observed']['last_bar_ms'] += 1
        cases.append(wrong_end)
        injected = deepcopy(valid)
        injected['observed']['symbol'] = '<script>alert(1)</script>'
        cases.append(injected)
        for data_check in cases:
            with self.subTest(data_check=data_check), self.assertRaises(ValueError):
                self.render(value, fixture_check=byte_check, fixture_data_check=data_check)

    def test_data_metadata_included_endpoints_keep_exact_utc_milliseconds(self):
        value = report()
        value['experiment']['first_bar_ms'] = 253402300799999-59*3600000
        value['run_id'] = contract.research_identity(value['experiment'])
        byte_check = fixture_check(value)
        data_check = fixture_data_check(value, byte_check)
        page = Document(self.render(value, fixture_check=byte_check, fixture_data_check=data_check))
        for role in ('expected', 'observed'):
            self.assertEqual(self.field(page.root, f'fixture-data-{role}-first_bar_ms'),
                             '9999-12-29T12:59:59.999 UTC')
            self.assertEqual(self.field(page.root, f'fixture-data-{role}-last_bar_ms'),
                             '9999-12-31T23:59:59.999 UTC')

    def test_data_projection_has_no_io_fixture_parsing_or_preparation_and_preserves_context(self):
        from trading_bot import research_fixture, research_fixture_data, fixture_input, research_input
        value = robust_report()
        byte_check = fixture_check(value, matched=False)
        data_check = fixture_data_check(value, byte_check)
        original = deepcopy((value, byte_check, data_check))
        # The existing report validator uses JSON internally to copy its saved
        # values. Forbid fixture parsing/preparation, not that unchanged copy.
        with patch('builtins.open', side_effect=AssertionError('renderer opened a file')), \
                patch('os.open', side_effect=AssertionError('renderer opened a descriptor')), \
                patch('os.stat', side_effect=AssertionError('renderer inspected a file')), \
                patch('os.lstat', side_effect=AssertionError('renderer inspected a link')), \
                patch.object(Path, 'open', side_effect=AssertionError('renderer opened a path')), \
                patch.object(research_fixture, 'read_fixture_snapshot',
                             side_effect=AssertionError('renderer read input')), \
                patch.object(research_fixture_data, 'check_fixture_data',
                             side_effect=AssertionError('renderer checked input')), \
                patch.object(fixture_input, 'load_fixture',
                             side_effect=AssertionError('renderer loaded input')), \
                patch.object(research_input, 'prepare_research_input',
                             side_effect=AssertionError('renderer prepared input')):
            first = self.render(value, fixture_check=byte_check, fixture_data_check=data_check)
            self.assertEqual(first, self.render(value, fixture_check=byte_check,
                                               fixture_data_check=data_check))
        self.assertEqual((value, byte_check, data_check), original)

    def test_standalone_document_has_no_active_or_external_content(self):
        value = robust_report()
        matching, different = fixture_check(value), fixture_check(value, matched=False)
        observed = dict(fixture_data_check(value, different)['observed'], symbol='ETH/USDT')
        contexts = ((None, None), (matching, None), (different, None),
                    (matching, fixture_data_check(value, matching)),
                    (different, fixture_data_check(value, different)),
                    (different, fixture_data_check(value, different, observed=observed)),
                    (matching, fixture_data_check(value, matching, invalid=True)))
        for check, data_check in contexts:
            with self.subTest(status=None if check is None else check['status'],
                              data_status=None if data_check is None else data_check['status']):
                page = Document(self.render(value, fixture_check=check, fixture_data_check=data_check))
                self.assertTrue(any(n.tag == 'html' and n.attrs.get('lang') == 'ko' for n in page.nodes))
                self.assertTrue(any(n.tag == 'style' for n in page.nodes))
                self.assertFalse({n.tag for n in page.nodes} & {'script', 'link', 'img', 'iframe', 'form', 'object', 'a'})
                for node in page.nodes:
                    self.assertFalse(set(node.attrs) & {'src', 'href', 'action'})
                    self.assertFalse(any(key.lower().startswith('on') for key in node.attrs))
                metadata = [n.attrs for n in page.nodes if n.tag == 'meta']
                csp = next(m['content'] for m in metadata if m.get('http-equiv') == 'Content-Security-Policy')
                self.assertIn("default-src 'none'", csp)
                self.assertIn("form-action 'none'", csp)
                self.assertTrue(any(m.get('name') == 'referrer' and m['content'] == 'no-referrer' for m in metadata))
                self.assertTrue(any(m.get('name') == 'viewport' for m in metadata))
                self.assertFalse(any(m.get('http-equiv', '').lower() == 'refresh' for m in metadata))


if __name__ == '__main__':
    unittest.main()
