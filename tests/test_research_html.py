"""Consumer-visible guarantees for the standalone saved research projection."""
from copy import deepcopy
from html.parser import HTMLParser
import importlib
import os
from pathlib import Path
import sys
import time
import unittest

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

    def test_standalone_document_has_no_active_or_external_content(self):
        page = self.page(robust_report())
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
