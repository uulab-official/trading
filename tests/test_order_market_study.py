"""Fixed-plan studies use the real market handoff/engine, never provider sockets."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from test_order_market_run import fixture
from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.order_state_store import _canonical


def plan():
    return dict(version=1, common=dict(initial_balance='1000000', position_size_pct='20',
        fee_pct='0.1', slippage_pct='0.05', max_daily_loss_pct='3', max_drawdown_pct='10'),
        candidates=[dict(id='ma-fast', strategy='ma_cross', fast_period=3, slow_period=5),
                    dict(id='rsi-14', strategy='rsi', rsi_period=14)])


def inventory(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in directory.rglob('*') if p.is_file()}


class MarketStudyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('trading_bot.market_paper_study')
        except ModuleNotFoundError as exc:
            if exc.name == 'trading_bot.market_paper_study':
                self.fail('fixed-plan market study is not implemented')
            raise
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'history.json'
        self.source.write_text(json.dumps(fixture(), indent=2))
        self.directory = self.root/'study'
        self.settings = plan()
        guard = ExitStack()
        self.addCleanup(guard.close)
        guard.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')))
        guard.enter_context(patch('socket.create_connection', side_effect=AssertionError('network forbidden')))

    def run_study(self):
        return asyncio.run(self.api.run_market_study(self.source, self.settings, directory=self.directory))

    def inspect(self):
        return asyncio.run(self.api.inspect_market_study(self.directory))

    def reject_plan(self):
        with self.assertRaises(ValueError):
            self.run_study()
        self.assertFalse(self.directory.exists())

    def test_run_reuses_engine_and_publishes_verified_comparison(self):
        result = self.run_study()
        self.assertEqual(result['status'], 'verified_study')
        self.assertTrue(result['receipt_validated'])
        self.assertFalse(result['live_enabled'])
        self.assertFalse(result['resume_authorized'])
        self.assertEqual(result['baseline'], 'ma-fast')
        self.assertEqual([r['id'] for r in result['candidates']], ['ma-fast', 'rsi-14'])
        for candidate, row in zip(self.settings['candidates'], result['candidates']):
            config = PaperRunConfig(symbol='BTC/KRW', timeframe='1h', **self.settings['common'],
                                   **{k:v for k,v in candidate.items() if k != 'id'})
            original = asyncio.run(PaperRunner(config).run(fixture()['candles']))
            self.assertEqual(row['metrics']['equity'], original['equity'])
            self.assertEqual(row['metrics']['return_pct'], original['total_return_pct'])
            self.assertEqual(row['metrics']['fees'], original['total_fees'])
            self.assertEqual(row['metrics']['fills'], len(original['trades']))
            self.assertEqual(row['status'], 'verified_completion')
        self.assertEqual(Decimal(result['candidates'][0]['return_delta_pp']), 0)
        expected = Decimal(result['candidates'][1]['metrics']['return_pct']) - Decimal(result['candidates'][0]['metrics']['return_pct'])
        self.assertEqual(Decimal(result['candidates'][1]['return_delta_pp']), expected)
        self.assertNotIn('winner', result)
        self.assertEqual(self.source.read_bytes(), (self.directory/'source.json').read_bytes())

    def test_plan_is_frozen_before_first_candidate(self):
        original = self.api.run_market_replay
        calls = []
        async def wrapped(*args, **kwargs):
            calls.append(kwargs['directory'].name)
            self.assertTrue((self.directory/'study.json').is_file())
            saved = json.loads((self.directory/'plan.json').read_bytes())
            self.assertEqual(saved, self.settings)
            self.assertFalse((self.directory/'study-result.json').exists())
            return await original(*args, **kwargs)
        with patch.object(self.api, 'run_market_replay', wrapped):
            self.run_study()
        self.assertEqual(calls, ['ma-fast', 'rsi-14'])

    def test_check_never_opens_writer_or_changes_source(self):
        self.run_study()
        before = inventory(self.directory)
        with patch('trading_core.paper_session.PaperTradingSession', side_effect=AssertionError('writer forbidden')):
            result = self.inspect()
        self.assertEqual(result['status'], 'verified_study')
        self.assertEqual(inventory(self.directory), before)

    def test_invalid_candidate_does_not_create_any_output(self):
        self.settings['candidates'][1]['rsi_period'] = 500
        self.reject_plan()

    def test_plan_must_explicitly_set_all_shared_conditions(self):
        del self.settings['common']['fee_pct']
        self.reject_plan()

    def test_per_candidate_cost_override_is_refused(self):
        self.settings['candidates'][1]['fee_pct'] = '2'
        self.reject_plan()

    def test_duplicate_effective_strategy_is_refused(self):
        self.settings['candidates'][1] = dict(id='another', strategy='ma_cross', fast_period=3, slow_period=5)
        self.reject_plan()

    def test_unused_strategy_parameters_are_not_silent_candidates(self):
        self.settings['candidates'][1]['fast_period'] = 7
        self.reject_plan()

    def test_candidate_ids_and_count_are_bounded(self):
        for identifier in ('../escape', '/absolute', 'two words', 'A', 'x'*33):
            with self.subTest(identifier=identifier):
                self.settings = plan()
                self.settings['candidates'][1]['id'] = identifier
                self.reject_plan()
        for count in (0,1,9):
            self.settings = plan()
            self.settings['candidates'] = [dict(id=f'item-{i}', strategy='rsi', rsi_period=i+1) for i in range(count)]
            self.reject_plan()

    def test_bool_float_and_nonfinite_values_rejected(self):
        for value in (True, 1.0, 'NaN', 'Infinity', '-1', '1e999', '1e-100'):
            with self.subTest(value=value):
                self.settings = plan()
                self.settings['common']['initial_balance'] = value
                self.reject_plan()
        self.settings = plan(); self.settings['version'] = True; self.reject_plan()
        self.settings = plan(); self.settings['candidates'][0]['fast_period'] = True; self.reject_plan()

    def test_total_candidate_bar_budget_is_checked_before_create(self):
        self.source.write_text(json.dumps(fixture(5000)))
        self.settings['candidates'] = [dict(id=f'rsi-{i}', strategy='rsi', rsi_period=i+5) for i in range(5)]
        self.reject_plan()

    def test_new_directory_only_preserves_existing_contents(self):
        self.directory.mkdir(); marker=self.directory/'do-not-delete'; marker.write_text('keep')
        with self.assertRaises(Exception): self.run_study()
        self.assertEqual(marker.read_text(), 'keep')
        self.assertEqual(list(self.directory.iterdir()), [marker])

    def test_second_failure_preserves_first_and_does_not_start_third(self):
        self.settings['candidates'].append(dict(id='third', strategy='rsi', rsi_period=21))
        original = self.api.run_market_replay
        calls=[]
        async def wrapped(*args, **kwargs):
            calls.append(kwargs['directory'].name)
            if len(calls)==2: raise RuntimeError('injected failure')
            return await original(*args, **kwargs)
        with patch.object(self.api, 'run_market_replay', wrapped):
            with self.assertRaises(RuntimeError): self.run_study()
        self.assertEqual(calls, ['ma-fast', 'rsi-14'])
        self.assertTrue((self.directory/'runs/ma-fast/run/result.json').exists())
        self.assertFalse((self.directory/'runs/third').exists())
        self.assertFalse((self.directory/'study-result.json').exists())
        report=self.inspect()
        self.assertEqual(len(report['candidates']),3)
        self.assertEqual([r['status'] for r in report['candidates']], ['verified_completion','not_started','not_started'])
        self.assertTrue(all(r['return_delta_pp'] is None for r in report['candidates']))
        self.assertEqual(report['status'], 'incomplete_study')

    def test_cancellation_stops_batch_and_keeps_partial_inventory(self):
        async def interrupted(*a, **k): raise asyncio.CancelledError()
        with patch.object(self.api, 'run_market_replay', interrupted):
            with self.assertRaises(asyncio.CancelledError): self.run_study()
        self.assertFalse((self.directory/'study-result.json').exists())
        self.assertEqual(len(self.inspect()['candidates']), 2)

    def test_source_change_between_candidates_prevents_next_execution(self):
        original=self.api.run_market_replay; calls=[]
        async def mutate(*a, **k):
            calls.append(k['directory'].name)
            result=await original(*a, **k)
            (self.directory/'source.json').write_text('{}')
            return result
        with patch.object(self.api, 'run_market_replay', mutate):
            with self.assertRaises(Exception): self.run_study()
        self.assertEqual(calls, ['ma-fast'])
        self.assertFalse((self.directory/'study-result.json').exists())

    def test_source_manifest_and_plan_changes_block_check(self):
        self.run_study()
        for name in ('source.json','plan.json','study.json'):
            path=self.directory/name; raw=path.read_bytes()
            path.write_text('{}')
            with self.subTest(name=name):
                report=self.inspect(); self.assertEqual(report['status'],'blocked')
                self.assertFalse(report['receipt_validated'])
            path.write_bytes(raw)

    def test_a_valid_but_other_child_configuration_is_refused(self):
        self.run_study()
        path=self.directory/'runs/rsi-14/source-manifest.json'
        value=json.loads(path.read_bytes()); value['config']['rsi_period']=21
        path.write_text(_canonical(value)+'\n')
        report=self.inspect()
        self.assertEqual(report['candidates'][1]['status'],'blocked')
        self.assertFalse(report['receipt_validated'])
        self.assertIsNone(report['candidates'][0]['return_delta_pp'])

    def test_corrupt_child_report_never_becomes_completion(self):
        self.run_study()
        (self.directory/'runs/ma-fast/run/result.json').write_text('{}')
        report=self.inspect()
        self.assertEqual(len(report['candidates']),2)
        self.assertEqual(report['candidates'][0]['status'],'blocked')
        self.assertEqual(report['candidates'][1]['status'],'verified_completion')
        self.assertFalse(report['receipt_validated'])

    def test_receipt_tampering_is_not_a_valid_study(self):
        self.run_study()
        path=self.directory/'study-result.json'
        value=json.loads(path.read_bytes()); value['candidates'][0]['metrics']['return_pct']='999999'
        path.write_text(_canonical(value)+'\n')
        report=self.inspect()
        self.assertEqual(report['status'],'blocked')
        self.assertEqual(report['reason'],'study_receipt_mismatch')
        self.assertFalse(report['receipt_validated'])

    def test_absent_receipt_is_not_certified_or_recreated(self):
        self.run_study(); path=self.directory/'study-result.json'; path.unlink()
        result=self.inspect()
        self.assertEqual(result['status'],'complete_without_receipt')
        self.assertTrue(result['all_candidates_verified'])
        self.assertFalse(result['receipt_validated']); self.assertFalse(path.exists())

    def test_prior_candidate_changed_during_later_audit_discards_success(self):
        self.run_study(); original=self.api.audit_durable_replay
        async def mutate(directory):
            result=await original(directory)
            if Path(directory).parent.name=='rsi-14':
                path=self.directory/'runs/ma-fast/run/result.json'
                value=json.loads(path.read_bytes());value['replay']['total_return_pct']='999'
                path.write_text(_canonical(value)+'\n')
            return result
        with patch.object(self.api,'audit_durable_replay',mutate):
            result=self.inspect()
        self.assertNotEqual(result['status'],'verified_study')
        self.assertFalse(result['receipt_validated'])

    def test_public_shared_source_and_symlink_child_refused(self):
        self.run_study(); path=self.directory/'source.json';path.chmod(0o644)
        self.assertEqual(self.inspect()['status'],'blocked')
        path.chmod(0o600)
        child=self.directory/'runs/rsi-14'; moved=self.root/'elsewhere';child.rename(moved);child.symlink_to(moved,target_is_directory=True)
        result=self.inspect();self.assertNotEqual(result['status'],'verified_study')
        self.assertEqual(result['candidates'][1]['status'],'blocked')

    def test_no_trade_candidates_are_kept(self):
        self.source.write_text(json.dumps(fixture(20)))
        result=self.run_study()
        self.assertEqual(result['status'],'verified_study')
        self.assertTrue(all(row['metrics']['fills']==0 for row in result['candidates']))
        self.assertTrue(all(Decimal(row['metrics']['return_pct'])==0 for row in result['candidates']))

    def test_missing_study_is_unavailable_without_creating_directory(self):
        report=self.inspect()
        self.assertEqual(report['status'],'unavailable')
        self.assertFalse(self.directory.exists())

    def test_cli_run_check_and_fixed_json_errors(self):
        plan_file=self.root/'plan.json';plan_file.write_text(json.dumps(self.settings))
        cli=ROOT/'tools/paper_study.py'
        run=subprocess.run([sys.executable,str(cli),'run',str(self.source),'--plan',str(plan_file),
            '--directory',str(self.directory)],capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'],'verified_study')
        check=subprocess.run([sys.executable,str(cli),'check',str(self.directory)],capture_output=True,text=True,timeout=30)
        self.assertEqual(check.returncode,0,check.stdout+check.stderr)
        self.assertEqual(json.loads(check.stdout)['status'],'verified_study')
        error=subprocess.run([sys.executable,str(cli),'check',str(self.root/'private-secret')],capture_output=True,text=True)
        self.assertEqual(error.returncode,2)
        self.assertNotIn('private-secret',error.stdout)
        self.assertEqual(json.loads(error.stdout)['status'],'unavailable')

    def test_final_evidence_changed_after_child_audit_never_certifies(self):
        self.run_study(); original=self.api.audit_durable_replay
        async def mutate(directory):
            result=await original(directory)
            if Path(directory).parent.name=='ma-fast':
                (Path(directory)/result['final_evidence']).write_text('{}')
            return result
        with patch.object(self.api,'audit_durable_replay',mutate):
            result=self.inspect()
        self.assertNotEqual(result['status'],'verified_study')
        self.assertFalse(result['receipt_validated'])

    def test_malformed_receipt_does_not_hide_candidate_inventory(self):
        self.run_study()
        (self.directory/'study-result.json').write_text('{truncated')
        result=self.inspect()
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['reason'],'study_receipt_invalid')
        self.assertEqual([r['id'] for r in result['candidates']],['ma-fast','rsi-14'])
        self.assertTrue(all(r['return_delta_pp'] is None for r in result['candidates']))

    def test_execute_report_is_not_labelled_a_read_only_operation(self):
        result=self.run_study()
        self.assertFalse(result['read_only'])
        self.assertEqual(result['operation'],'execute_new_study')
        result=self.inspect()
        self.assertTrue(result['read_only'])
        self.assertEqual(result['operation'],'inspect_existing_study')

    def test_engine_changed_after_last_child_audit_blocks(self):
        self.run_study(); original=self.api.audit_durable_replay
        fingerprint=self.api.recovery._engine_fingerprint
        changed=[]
        async def trigger(directory):
            result=await original(directory)
            if Path(directory).parent.name=='rsi-14': changed.append(True)
            return result
        with patch.object(self.api,'audit_durable_replay',trigger), patch.object(
                self.api.recovery,'_engine_fingerprint',lambda:'f'*64 if changed else fingerprint()):
            result=self.inspect()
        self.assertNotEqual(result['status'],'verified_study')
        self.assertFalse(result['receipt_validated'])

    def test_shared_manifest_binds_original_execution_fingerprint(self):
        result=self.run_study()
        manifest=json.loads((self.directory/'study.json').read_bytes())
        self.assertIn('execution_sha256',manifest)
        self.assertEqual(manifest['execution_sha256'], self.api.recovery._engine_fingerprint())

    def test_real_sigterm_preserves_incomplete_child_and_stops_other_candidates(self):
        plan_file=self.root/'plan.json';plan_file.write_text(json.dumps(self.settings))
        script="""
import asyncio, os, signal, sys
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server','tools'):
    sys.path.insert(0,str(root/part))
from trading_core.paper_session import _PaperAdapter
original=_PaperAdapter.create_order
async def stop(self,*args,**kwargs):
    result=await original(self,*args,**kwargs)
    os.kill(os.getpid(),signal.SIGTERM)
    await asyncio.sleep(0)
    return result
_PaperAdapter.create_order=stop
import paper_study
raise SystemExit(paper_study.main(['run',sys.argv[2],'--plan',sys.argv[3],'--directory',sys.argv[4]]))
"""
        process=subprocess.run([sys.executable,'-B','-c',script,str(ROOT),str(self.source),
            str(plan_file),str(self.directory)],capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,143,process.stdout+process.stderr)
        self.assertEqual(json.loads(process.stdout)['signal'],'SIGTERM')
        self.assertFalse((self.directory/'study-result.json').exists())
        self.assertFalse((self.directory/'runs/rsi-14').exists())
        report=self.inspect()
        self.assertEqual(report['status'],'incomplete_study')
        self.assertEqual([r['status'] for r in report['candidates']],['reconciliation_required','not_started'])
        from trading_core.paper_session_observer import PaperSessionObserver
        child=self.directory/'runs/ma-fast/run'
        captured=PaperSessionObserver(child/'account.sqlite').read_evidence()
        self.assertEqual(len(captured['payload']['book']['trades']),1)
        self.assertEqual(captured['payload']['control']['mode'],'halted')
        # Recovery still requires an explicit separate operation, never the batch checker.
        from trading_bot.durable_paper_resume import resume_durable_replay
        recovered=asyncio.run(resume_durable_replay(child,reconcile_saved_fills=True))
        self.assertEqual(recovered['recovery']['existing_fills_skipped'],1)
        self.assertEqual(recovered['recovery']['saved_fills_reconciled'],1)
        self.assertEqual(self.inspect()['candidates'][0]['status'],'verified_completion')
        self.assertFalse((self.directory/'runs/rsi-14').exists())

    def test_external_source_and_caller_plan_changes_do_not_change_frozen_study(self):
        source_raw=self.source.read_bytes();original=self.api.run_market_replay;calls=[]
        async def change_original(*args,**kwargs):
            calls.append(kwargs['config'].strategy)
            self.source.write_text('{}')
            self.settings['candidates'][1]['rsi_period']=99
            return await original(*args,**kwargs)
        with patch.object(self.api,'run_market_replay',change_original):
            result=self.run_study()
        self.assertEqual(result['status'],'verified_study')
        self.assertEqual(result['candidates'][1]['config']['rsi_period'],14)
        self.assertEqual((self.directory/'source.json').read_bytes(),source_raw)

    def test_late_publication_error_keeps_verifiable_receipt(self):
        from trading_core.paper_evidence import PaperEvidencePublishedError
        original=self.api._publish
        def lost_reply(raw,path):
            original(raw,path)
            if Path(path).name=='study-result.json':
                raise PaperEvidencePublishedError('paper_evidence_published_check_required')
        with patch.object(self.api,'_publish',lost_reply):
            with self.assertRaises(PaperEvidencePublishedError): self.run_study()
        self.assertTrue((self.directory/'study-result.json').exists())
        self.assertEqual(self.inspect()['status'],'verified_study')

    def test_exactly_eight_candidates_and_zero_costs_are_valid(self):
        self.settings['common'].update(fee_pct='0',slippage_pct='0')
        self.settings['candidates']=[dict(id=f'rsi-{i}',strategy='rsi',rsi_period=i+1) for i in range(8)]
        frozen,records=self.api._prepare(self.settings,fixture(3))
        self.assertEqual(len(records),8)
        self.assertTrue(all(record['config']['fee_pct']=='0' for record in records))

    def test_unknown_cli_flags_cannot_enable_orders_or_resume(self):
        cli=ROOT/'tools/paper_study.py'
        for flag in ('--live','--force','--reconcile-saved-fills'):
            result=subprocess.run([sys.executable,str(cli),'check',str(self.directory),flag],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIsInstance(json.loads(result.stdout),dict)
        self.assertFalse(self.directory.exists())


if __name__=='__main__':
    unittest.main()
