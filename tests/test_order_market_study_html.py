"""Consumer-facing guarantees for the static, read-only study snapshot."""
from copy import deepcopy
from html.parser import HTMLParser
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return ''.join(c.text if isinstance(c, Node) else c for c in self.children)

    def find(self, **attrs):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if all(child.attrs.get(k.replace('_', '-')) == v for k, v in attrs.items()):
                    result.append(child)
                result.extend(child.find(**attrs))
        return result


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.nodes.append(node)
        self.stack[-1].children.append(node)
        if tag not in {'meta', 'br', 'hr', 'link', 'img', 'input', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def fixture():
    common = dict(initial_balance='1000000.0000', position_size_pct='20', fee_pct='0.100',
                  slippage_pct='0.05', max_daily_loss_pct='3', max_drawdown_pct='10')
    rows = []
    for label, strategy, parameters in (
        ('ma-fast', 'ma_cross', dict(fast_period=3, slow_period=5)),
        ('rsi-14', 'rsi', dict(rsi_period=14)),
    ):
        rows.append(dict(id=label, status='verified_completion', reason='original_audit_and_source_match',
            config=dict(common, symbol='BTC/USDT', timeframe='1h', strategy=strategy, **parameters),
            observed_at='2026-11-12T01:02:03+00:00', run_directory='runs/'+label+'/run',
            metrics=dict(equity='1234567.890123456789', cash='456.000', return_pct='23.4567890123456789',
                         fees='0.000', fills=0, bars=123, daily_loss_halted=False, drawdown_halted=True),
            return_delta_pp='0.000' if not rows else '7.000123456789',
            result_sha256='abc123', snapshot='snapshot'))
    return dict(schema_version=1, source='market_paper_study', mode='paper', read_only=True,
        operation='inspect_existing_study', live_enabled=False, resume_authorized=False,
        observed_at='2026-11-12T01:03:04+00:00', status='verified_study',
        reason='all_candidates_and_receipt_match', baseline='ma-fast', symbol='BTC/USDT',
        timeframe='1h', bar_count=123, common=common, source_sha256='source123',
        all_candidates_verified=True, receipt_validated=True, candidates=rows,
        boundaries=dict(observations='sequential_not_simultaneous', source_authenticated=False))


class MarketStudyHtmlTests(unittest.TestCase):
    def setUp(self):
        try:
            self.render = importlib.import_module('trading_bot.market_study_html').render_study_report
        except ModuleNotFoundError:
            self.fail('the read-only HTML renderer is not implemented')

    def page(self, report):
        html = self.render(report)
        self.assertIsInstance(html, str)
        self.assertTrue(html.lower().startswith('<!doctype html>'))
        return Document(html)

    def field(self, node, name):
        found = node.find(data_field=name)
        self.assertEqual(len(found), 1, name)
        return found[0].text

    def test_verified_snapshot_keeps_plan_order_units_exact_values_and_baseline(self):
        report = fixture()
        page = self.page(report)
        rows = page.root.find(data_candidate_id='ma-fast') + page.root.find(data_candidate_id='rsi-14')
        self.assertEqual([n.attrs['data-candidate-id'] for n in page.nodes if 'data-candidate-id' in n.attrs],
                         ['ma-fast', 'rsi-14'])
        self.assertIn('검증 완료', self.field(page.root, 'study-status'))
        self.assertIn('ma-fast', self.field(page.root, 'baseline'))
        self.assertIn('1234567.890123456789 USDT', self.field(rows[0], 'equity'))
        self.assertEqual(self.field(rows[0], 'return_pct'), '23.4567890123456789 %')
        self.assertEqual(self.field(rows[0], 'cash'), '456.000 USDT')
        self.assertEqual(self.field(rows[0], 'fees'), '0.000 USDT')
        self.assertEqual(self.field(rows[0], 'fills'), '0 건')
        self.assertEqual(self.field(rows[0], 'bars'), '123 개')
        self.assertEqual(self.field(rows[1], 'return_delta_pp'), '7.000123456789 pp')
        self.assertIn('아니요', self.field(rows[0], 'daily_loss_halted'))
        self.assertIn('예', self.field(rows[0], 'drawdown_halted'))
        self.assertIn('3', rows[0].text)
        self.assertIn('5', rows[0].text)
        self.assertIn('14', rows[1].text)
        for value in report['common'].values():
            self.assertIn(value, page.root.text)
        self.assertIn(report['observed_at'], page.root.text)
        self.assertIn('순차', page.root.text)
        self.assertIn('다시 검사', page.root.text)
        self.assertIn('시뮬레이션', page.root.text)

    def test_missing_or_corrupt_receipt_cannot_look_verified_or_expose_deltas(self):
        for status, reason in [('complete_without_receipt', 'receipt_missing'),
                               ('blocked', 'study_receipt_invalid'),
                               ('blocked', 'study_receipt_mismatch'),
                               ('verified_study', 'receipt_missing')]:
            with self.subTest(status=status, reason=reason):
                report = fixture()
                report.update(status=status, reason=reason, receipt_validated=False)
                page = self.page(report)
                self.assertNotIn('검증 완료', self.field(page.root, 'study-status'))
                self.assertIn('미검증', self.field(page.root, 'receipt-status'))
                self.assertNotIn('7.000123456789', page.root.text)
                self.assertIn('1234567.890123456789 USDT', page.root.text)
                self.assertIn(reason, page.root.text)

    def test_receipt_diagnostic_failure_overrides_contradictory_success_flags(self):
        for reason in ('receipt_missing', 'study_receipt_invalid', 'study_receipt_mismatch'):
            with self.subTest(reason=reason):
                report = fixture()
                report['reason'] = reason
                page = self.page(report)
                self.assertNotIn('검증 완료', self.field(page.root, 'study-status'))
                self.assertIn('미검증', self.field(page.root, 'receipt-status'))
                self.assertNotIn('7.000123456789', page.root.text)

    def test_quote_currency_follows_the_inspected_symbol(self):
        for symbol, expected in [('ETH/KRW', 'KRW'), ('BTC/USDT', 'USDT')]:
            with self.subTest(symbol=symbol):
                report = fixture()
                report['symbol'] = symbol
                page = self.page(report)
                row = page.root.find(data_candidate_id='ma-fast')[0]
                self.assertEqual(self.field(row, 'equity'), '1234567.890123456789 '+expected)
                self.assertEqual(self.field(page.root, 'initial_balance'), '1000000.0000 '+expected)

    def test_delta_requires_full_study_and_every_candidate_verification(self):
        for change in ('top_flag', 'top_status', 'row_status', 'no_rows'):
            with self.subTest(change=change):
                report = fixture()
                if change == 'top_flag':
                    report['all_candidates_verified'] = False
                elif change == 'top_status':
                    report['status'] = 'incomplete_study'
                elif change == 'row_status':
                    report['candidates'][1]['status'] = 'blocked'
                else:
                    report['candidates'] = []
                page = self.page(report)
                self.assertNotIn('7.000123456789', page.root.text)
                self.assertNotIn('검증 완료', self.field(page.root, 'study-status'))

    def test_preserves_all_incomplete_rows_parameters_and_hides_unverified_metrics(self):
        report = fixture()
        report.update(status='incomplete_study', all_candidates_verified=False, receipt_validated=False)
        report['candidates'] = []
        states = ('not_started', 'incomplete', 'compatible_snapshot', 'reconciliation_required', 'blocked', 'unavailable')
        for i, status in enumerate(states):
            row = deepcopy(fixture()['candidates'][0])
            row.update(id='candidate-'+str(i), status=status, reason='diagnostic-'+str(i))
            row['metrics']['equity'] = '998877.665544'
            report['candidates'].append(row)
        page = self.page(report)
        actual = [n for n in page.nodes if 'data-candidate-id' in n.attrs]
        self.assertEqual([n.attrs['data-candidate-id'] for n in actual], [r['id'] for r in report['candidates']])
        for row, status in zip(actual, states):
            self.assertIn(status, row.text)
            self.assertIn('diagnostic-', row.text)
            self.assertIn('정보 없음', self.field(row, 'equity'))
            self.assertIn('3', row.text)
        self.assertNotIn('998877.665544', page.root.text)

    def test_unknown_and_missing_values_are_unavailable_while_zero_is_preserved(self):
        report = fixture()
        report.pop('symbol')
        report.pop('bar_count')
        report['common']['fee_pct'] = None
        row = report['candidates'][0]
        row['metrics']['return_pct'] = None
        row['metrics']['daily_loss_halted'] = None
        row['metrics'].pop('cash')
        row['metrics']['fills'] = 0
        report['candidates'][1]['status'] = '<unknown>'
        page = self.page(report)
        row_node = page.root.find(data_candidate_id='ma-fast')[0]
        for key in ('return_pct', 'daily_loss_halted', 'cash'):
            self.assertIn('정보 없음', self.field(row_node, key))
        self.assertEqual(self.field(row_node, 'fills'), '0 건')
        self.assertNotIn('KRW', self.field(row_node, 'equity'))
        self.assertIn('<unknown>', page.root.text)

    def test_minimal_unavailable_envelope_explains_missing_candidates(self):
        report = {k: v for k, v in fixture().items() if k in (
            'schema_version', 'source', 'mode', 'read_only', 'operation', 'live_enabled',
            'resume_authorized', 'observed_at', 'receipt_validated', 'all_candidates_verified')}
        report.update(status='unavailable', reason='study_source_unavailable', candidates=[])
        page = self.page(report)
        self.assertIn('study_source_unavailable', page.root.text)
        self.assertIn('후보 정보를 불러올 수 없습니다', page.root.text)
        self.assertNotIn('검증 완료', self.field(page.root, 'study-status'))

    def test_escapes_every_supplied_location_and_has_no_active_content(self):
        attack = '\"><script src="https://evil.invalid/a">alert(1)</script><img onerror="boom">&'
        report = fixture()
        report.update(symbol='BTC/'+attack, baseline=attack, source_sha256=attack, observed_at=attack, reason=attack)
        report['common']['initial_balance'] = attack
        report['boundaries']['observations'] = attack
        for row in report['candidates']:
            row.update(id=attack, reason=attack, observed_at=attack, result_sha256=attack, run_directory=attack)
            row['config'].update(strategy=attack, fast_period=attack)
            row['metrics']['equity'] = attack
            row['return_delta_pp'] = attack
        html = self.render(report)
        page = Document(html)
        self.assertIn('&lt;script', html)
        self.assertIn(attack, page.root.text)
        self.assertFalse(any(n.tag in {'script', 'img', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'a', 'link'}
                             for n in page.nodes))
        self.assertFalse(any(k.startswith('on') or k in {'href', 'src', 'action'} for n in page.nodes for k in n.attrs))
        metas = [n.attrs for n in page.nodes if n.tag == 'meta']
        self.assertTrue(any(m.get('http-equiv') == 'Content-Security-Policy' and "default-src 'none'" in m['content']
                            and "form-action 'none'" in m['content'] for m in metas))
        self.assertTrue(any(m.get('name') == 'referrer' and m.get('content') == 'no-referrer' for m in metas))
        self.assertTrue(any(m.get('name') == 'viewport' for m in metas))
        self.assertTrue(any(n.tag == 'details' for n in page.nodes))
        self.assertTrue(any(n.tag == 'summary' for n in page.nodes))

    def test_is_pure_deterministic_and_does_not_mutate_input(self):
        report = fixture()
        before = deepcopy(report)
        with patch('builtins.open', side_effect=AssertionError('renderer must not read or write files')):
            first = self.render(report)
            second = self.render(report)
        self.assertEqual(first, second)
        self.assertEqual(report, before)

    def test_rejects_execution_or_invalid_inspection_envelope_with_fixed_error(self):
        cases = [None, [], {}, dict(fixture(), candidates={}), dict(fixture(), candidates=[None])]
        for key, value in [('schema_version', True), ('schema_version', 2), ('source', 'other'),
                           ('mode', 'live'), ('read_only', False), ('read_only', 1),
                           ('operation', 'execute_new_study'), ('live_enabled', True),
                           ('resume_authorized', True)]:
            cases.append(dict(fixture(), **{key: value}))
        for key in ('status', 'reason', 'observed_at', 'receipt_validated', 'all_candidates_verified'):
            missing = fixture()
            del missing[key]
            cases.append(missing)
        cases.extend((dict(fixture(), receipt_validated=1), dict(fixture(), all_candidates_verified=1)))
        for report in cases:
            with self.subTest(report=report):
                with self.assertRaisesRegex(ValueError, '^market_study_report_invalid$'):
                    self.render(report)


if __name__ == '__main__':
    unittest.main()
