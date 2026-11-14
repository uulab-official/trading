"""Ordered saved evidence: real account/exports, without broker or DB writes."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal as D, localcontext
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver
from trading_core.paper_evidence import export_evidence, verify_evidence, PaperEvidenceInvalid
from trading_core.paper_evidence_compare import compare_evidence, PaperEvidenceComparisonError
from trading_core.order_manager import OrderSide, OrderType, OrderOutcomeUnknown
from trading_core.order_reconciliation import reconcile_order
from trading_core.order_state_store import _canonical, _digest


class SequenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = []
        self.paths = []
        self.s = await self.new_session()
        network = patch('socket.socket.connect', side_effect=AssertionError('no network'))
        network.start(); self.addCleanup(network.stop)

    async def asyncTearDown(self):
        for session in reversed(self.sessions):
            await session.close()
        self.tmp.cleanup()

    async def new_session(self, **options):
        options.setdefault('initial_balance', '1000')
        options.setdefault('fee_pct', '0.1')
        session = PaperTradingSession(self.root / f'account-{len(self.sessions)}.sqlite', create=True, **options)
        await session.__aenter__(); self.sessions.append(session)
        return session

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_core.paper_evidence_sequence'),
                             'ordered evidence sequence is not implemented')
        return importlib.import_module('trading_core.paper_evidence_sequence')

    def capture(self, session=None):
        session = session or self.s
        path = self.root / f'capture-{len(self.paths)}.paper-evidence.json'
        export_evidence(PaperSessionObserver(session._store.path), path)
        self.paths.append(path)
        return path

    async def order(self, side='buy', amount='1', session=None, symbol='BTC/USDT', price='100'):
        session = session or self.s
        session.exchange.set_price(symbol, D(price), timestamp=1700000000000 + len(self.paths) * 1000)
        return await session.manager.place_order(symbol, OrderSide(side), OrderType.MARKET,
            D(amount), client_order_id=f'order-{len(session.manager.get_all_orders())}')

    def rewrite(self, path, edit):
        value = json.loads(path.read_bytes()); edit(value)
        value['snapshot']['digest'] = _digest(_canonical(value['payload']))
        value['content_sha256'] = _digest(_canonical({k:v for k,v in value.items() if k != 'content_sha256'}))
        path.write_bytes((_canonical(value) + '\n').encode())
        verify_evidence(path)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT/'tools/paper_evidence.py'), *map(str,args)],
            cwd=ROOT, capture_output=True, text=True, timeout=15)

    async def test_two_files_preserve_pair_accounting(self):
        before = self.capture(); await self.order(); after = self.capture()
        report = self.api().compare_evidence_sequence([before, after])
        pair = compare_evidence(before, after)
        self.assertEqual(report['source'], 'paper_evidence_sequence')
        self.assertEqual(report['snapshot_count'], 2)
        self.assertEqual(report['verified_intervals'], 1)
        for key in ('cash','fees','positions','orders','new_fills','valuation'):
            self.assertEqual(report['overall'][key], pair[key])
        self.assertFalse(report['live_enabled'])
        self.assertTrue(report['read_only'])

    async def test_repeated_snapshots_do_not_double_count_fills_or_fees(self):
        self.capture(); await self.order(amount='2'); self.capture(); self.capture()
        await self.order(side='sell', price='125'); self.capture()
        report = self.api().compare_evidence_sequence(self.paths)
        self.assertEqual(report['repeated_intervals'], 1)
        self.assertEqual(report['interval_totals']['new_fills'], 2)
        self.assertEqual(D(report['overall']['cash']['delta']), D('-75.325'))
        self.assertEqual(D(report['overall']['fees']['delta']), D('.325'))
        self.assertEqual(report['intervals'][1]['relation'], 'same_snapshot')

    async def test_acknowledgement_recovery_is_not_another_fill(self):
        self.capture()
        original = self.s.exchange.create_order
        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise TimeoutError('private transport detail')
        with patch.object(self.s.exchange, 'create_order', side_effect=lost):
            with self.assertRaises(OrderOutcomeUnknown): await self.order()
        self.capture()
        await reconcile_order(self.s.manager, 'order-0', allow_client_lookup=True)
        self.capture()
        report = self.api().compare_evidence_sequence(self.paths)
        self.assertEqual(report['interval_totals']['new_fills'], 1)
        self.assertEqual(report['interval_totals']['observed_fill_confirmations'], 1)
        self.assertEqual(report['overall']['orders']['confirmed_existing_fills'], 0)
        step = report['intervals'][1]
        self.assertEqual(step['new_fills'], 0)
        self.assertEqual(step['confirmed_existing_fills'], 1)
        self.assertEqual(D(step['cash']['delta']), 0)
        self.assertEqual(D(step['fees']['delta']), 0)

    async def test_middle_account_conflict_is_not_hidden_by_matching_endpoints(self):
        first=self.capture(); foreign=await self.new_session(); middle=self.capture(foreign); last=self.capture()
        self.assertEqual(compare_evidence(first,last)['relation'], 'same_snapshot')
        with self.assertRaises(PaperEvidenceComparisonError) as got:
            self.api().compare_evidence_sequence([first,middle,last])
        self.assertEqual(got.exception.reason,'different_account')
        self.assertEqual(got.exception.interval_index,1)

    async def test_reversed_middle_revision_reports_exact_interval(self):
        a=self.capture(); await self.order(); b=self.capture(); await self.order(); c=self.capture()
        with self.assertRaises(PaperEvidenceComparisonError) as got:
            self.api().compare_evidence_sequence([a,c,b])
        self.assertEqual(got.exception.reason,'revision_reversed')
        self.assertEqual(got.exception.interval_index,2)

    async def test_rewritten_order_history_is_rejected_in_middle(self):
        await self.order(); a=self.capture(); await self.order(); b=self.capture(); c=self.capture()
        self.rewrite(b,lambda v:v['payload']['orders']['order-0'].update(strategy_id='changed'))
        with self.assertRaises(PaperEvidenceComparisonError) as got:
            self.api().compare_evidence_sequence([a,b,c])
        self.assertEqual(got.exception.reason,'order_history_changed')

    async def test_invalid_middle_file_produces_no_cli_partial_result(self):
        a=self.capture(); b=self.root/'corrupt-secret-name.json';b.write_text('broken');b.chmod(0o600);c=self.capture()
        self.api()
        result=self.cli('sequence',a,b,c)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_invalid'})
        self.assertNotIn(str(self.root),result.stderr)

    async def test_each_argument_is_read_once_and_source_is_not_changed(self):
        self.capture();await self.order();self.capture();self.capture()
        api=self.api(); before={p:p.read_bytes() for p in self.paths}
        with patch.object(api,'_read_evidence',wraps=api._read_evidence) as read:
            api.compare_evidence_sequence(self.paths)
        self.assertEqual([c.args[0] for c in read.call_args_list],self.paths)
        self.assertEqual({p:p.read_bytes() for p in self.paths},before)

    async def test_captured_input_is_not_reopened_after_replacement(self):
        a=self.capture();await self.order();b=self.capture();api=self.api();original=api._read_evidence
        def replace(path):
            value=original(path)
            Path(path).write_text('replacement must not be reread')
            return value
        with patch.object(api,'_read_evidence',side_effect=replace):
            report=api.compare_evidence_sequence([a,b])
        self.assertEqual(report['overall']['new_fills']['count'],1)

    async def test_input_count_bound_precedes_any_file_read(self):
        api=self.api()
        for paths in ([],[self.root/'missing'],[self.root/'missing']*33,'not-a-sequence',(x for x in [])):
            with self.subTest(paths=type(paths).__name__),patch.object(api,'_read_evidence') as read:
                with self.assertRaises(PaperEvidenceInvalid): api.compare_evidence_sequence(paths)
                read.assert_not_called()

    async def test_maximum_count_and_identical_paths_are_bounded(self):
        a=self.capture();report=self.api().compare_evidence_sequence([a]*32)
        self.assertEqual(report['snapshot_count'],32);self.assertEqual(report['repeated_intervals'],31)
        self.assertEqual(report['interval_totals']['new_fills'],0)

    async def test_total_byte_budget_fails_without_partial_success(self):
        a=self.capture();b=self.capture();api=self.api()
        with patch.object(api,'MAX_SEQUENCE_BYTES',a.stat().st_size+b.stat().st_size-1):
            with self.assertRaises(PaperEvidenceInvalid): api.compare_evidence_sequence([a,b])
        with patch.object(api,'MAX_SEQUENCE_BYTES',a.stat().st_size+b.stat().st_size):
            self.assertEqual(api.compare_evidence_sequence([a,b])['snapshot_count'],2)

    async def test_capture_clock_reversal_warns_without_reordering(self):
        a=self.capture();await self.order();b=self.capture()
        self.rewrite(b,lambda v:v.update(captured_at='2000-01-01T00:00:00+00:00'))
        report=self.api().compare_evidence_sequence([a,b])
        self.assertIn('capture_clock_reversed',report['warnings'])
        self.assertEqual(report['overall']['new_fills']['count'],1)

    async def test_multiple_assets_and_currency_remain_explicit(self):
        s=await self.new_session(quote_currency='KRW',initial_balance='1000000')
        a=self.capture(s);await self.order(session=s,symbol='BTC/KRW',price='1000');b=self.capture(s)
        await self.order(session=s,symbol='ETH/KRW',price='2000');c=self.capture(s)
        report=self.api().compare_evidence_sequence([a,b,c])
        self.assertEqual(report['overall']['quote_currency'],'KRW')
        self.assertEqual(report['overall']['positions']['count'],2)
        self.assertEqual(D(report['overall']['cash']['delta']),D('-3003'))

    async def test_control_roundtrip_is_visible_as_two_observed_transitions(self):
        s=await self.new_session(enable_control=True)
        a=self.capture(s)
        await s.set_operating_mode('active',expected_generation=0,reason='operator');b=self.capture(s)
        await s.set_operating_mode('halted',expected_generation=1,reason='operator');c=self.capture(s)
        report=self.api().compare_evidence_sequence([a,b,c])
        self.assertEqual(report['interval_totals']['control_change_intervals'],2)
        self.assertEqual(report['interval_totals']['new_fills'],0)
        self.assertEqual(D(report['overall']['cash']['delta']),0)

    async def test_database_not_required_after_exports(self):
        a=self.capture();await self.order();b=self.capture()
        await self.s.close(); self.s._store.path.unlink()
        self.assertEqual(self.api().compare_evidence_sequence([a,b])['verified_intervals'],1)

    async def test_large_balance_rounding_is_not_invented_cashflow(self):
        s=await self.new_session(initial_balance='1e20')
        a=self.capture(s);await self.order(session=s, amount='0.00000000001', price='1');b=self.capture(s)
        report=self.api().compare_evidence_sequence([a,b])
        cash=report['overall']['cash']
        self.assertEqual(D(cash['delta']),0)
        self.assertEqual(D(cash['from_new_fills']),D('-0.00000000001001'))
        self.assertEqual(D(cash['rounding_delta']),D('0.00000000001001'))

    async def test_exact_sums_ignore_ambient_decimal_context(self):
        self.capture();await self.order();self.capture();await self.order(side='sell',price='101.23456789');self.capture()
        api=self.api();normal=api.compare_evidence_sequence(self.paths)
        with localcontext() as ctx:
            ctx.prec=3
            low=api.compare_evidence_sequence(self.paths)
        self.assertEqual(low['overall']['cash'],normal['overall']['cash'])
        self.assertTrue(low['reconciliation']['interval_totals_match'])

    async def test_more_than_display_limit_fills_all_enter_totals(self):
        self.capture()
        for i in range(105):
            await self.order(amount='.001')
        self.capture();report=self.api().compare_evidence_sequence(self.paths)
        self.assertEqual(report['interval_totals']['new_fills'],105)
        self.assertEqual(report['overall']['new_fills']['count'],105)
        self.assertEqual(len(report['overall']['new_fills']['items']),100)
        self.assertEqual(D(report['overall']['cash']['delta']),D('-10.5105'))

    async def test_cli_success_is_one_json_and_no_diagnostics(self):
        a=self.capture();await self.order();b=self.capture();self.api()
        result=self.cli('sequence',a,b)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stderr,'')
        report=json.loads(result.stdout);self.assertEqual(report['snapshot_count'],2)
        self.assertNotIn(str(self.root),result.stdout)

    async def test_cli_conflict_has_index_not_partial_account(self):
        a=self.capture();await self.order();b=self.capture();self.api()
        result=self.cli('sequence',a,b,a)
        self.assertEqual(result.returncode,5);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_comparison_conflict',
            'reason':'revision_reversed','interval_index':2})

    async def test_empty_sequence_cli_still_emits_one_fixed_json_error(self):
        self.api()
        result=self.cli('sequence')
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,'')
        self.assertEqual(result.stderr.strip(),'{"error": "paper_evidence_invalid"}')

    async def test_failure_after_valid_interval_has_no_partial_report(self):
        a=self.capture();await self.order();b=self.capture();self.api()
        result=self.cli('sequence',a,b,self.root/'secret-missing-name')
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_invalid'})

    async def test_inconsistent_interval_sums_cannot_report_success(self):
        self.capture();await self.order();self.capture();await self.order();self.capture()
        api=self.api();original=api._compare_captured_evidence;calls=0
        def inconsistent(*args):
            nonlocal calls
            result=original(*args);calls+=1
            if calls==1: result['cash']['delta']='0'
            return result
        with patch.object(api,'_compare_captured_evidence',side_effect=inconsistent):
            with self.assertRaises(PaperEvidenceComparisonError) as got:
                api.compare_evidence_sequence(self.paths)
        self.assertEqual(got.exception.reason,'sequence_totals_conflict')

    async def test_sequence_import_failure_does_not_leak_exception(self):
        a=self.capture();b=self.capture();self.api()
        code = r"""
import builtins, runpy, sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name=='trading_core.paper_evidence_sequence':
        raise ImportError('secret-import-path')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
sys.argv=['tools/paper_evidence.py','sequence',sys.argv[1],sys.argv[2]]
runpy.run_path('tools/paper_evidence.py',run_name='__main__')
"""
        result=subprocess.run([sys.executable,'-B','-c',code,str(a),str(b)],
            cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_operation_failed'})

    async def test_existing_compare_and_verify_cli_unchanged(self):
        a=self.capture();await self.order();b=self.capture()
        self.api()
        pair=self.cli('compare',a,b);verified=self.cli('verify',b)
        self.assertEqual(pair.returncode,0,pair.stderr);self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertEqual(json.loads(pair.stdout)['source'],'paper_evidence_comparison')
        self.assertTrue(json.loads(verified.stdout)['valid'])


if __name__ == '__main__': unittest.main()
