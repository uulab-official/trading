"""Contract tests use deterministic evaluators, not simulated proof of engine execution."""
import copy
from decimal import Decimal as D, localcontext
import hashlib
import inspect
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
from trading_bot import research_report as contract
from trading_bot.walk_forward_research import run_research


def fixture(n=90):
    return {'mode':'paper', 'symbol':'BTC/USDT', 'timeframe':'1h',
            'candles': [[i*3600000, '100', '120', '80', '110', '1000'] for i in range(n)]}


def metrics(settings, result='1', fills=2):
    with localcontext(contract.CONTEXT):
        initial = D(settings['initial_balance'])
        equity = initial * (1 + D(result) / 100)
        return dict(cash=str(equity), equity=str(equity), position_value='0', fees='0',
                    fills=fills, return_pct=str((equity-initial)/initial*100),
                    daily_loss_halted=False, drawdown_halted=False)


def evaluate(c, warm, bars, settings):
    # Base 'fast' wins; stressed ranking would reverse. Never use it to reselect.
    return metrics(settings, str((D('2')-D(settings['fee_pct'])*10) if c['id']=='fast' else D('0')))


def reference(bars, settings):
    return contract.benchmark_metrics(str(bars[1][1]), str(bars[-1][4]), settings)


CANDIDATES = [dict(id='fast',strategy='ma_cross',fast_period=2,slow_period=3),
              dict(id='slow',strategy='ma_cross',fast_period=3,slow_period=5)]
SETTINGS = dict(train_size=30,test_size=20,warmup=10,initial_balance='1000',gap=1)


def report(**kwargs):
    return run_research(fixture(), 'a'*64, candidates=CANDIDATES, settings=SETTINGS,
                        evaluator=evaluate, robustness=True, benchmark_evaluator=reference, **kwargs)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        if self._testMethodName != "test_legacy_path_is_unchanged":
            self.assertIn("robustness", inspect.signature(run_research).parameters, "optional robustness path is missing")

    def test_legacy_path_is_unchanged(self):
        old=run_research(fixture(), 'a'*64, candidates=CANDIDATES,settings=SETTINGS,evaluator=evaluate)
        self.assertEqual(old['schema_version'],1)
        self.assertNotIn('robustness',old)
        self.assertEqual(old['run_id'],hashlib.sha256(contract.canonical(old['experiment']).encode()).hexdigest())
        self.assertEqual(contract.validate_report(old),old)

    def test_fixed_winner_and_base_metrics(self):
        calls=[]
        def spy(c,warm,bars,options):
            calls.append((c['id'],bars[0][0],options['fee_pct']))
            return evaluate(c,warm,bars,options)
        r=run_research(fixture(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,
                       evaluator=spy,robustness=True,benchmark_evaluator=reference)
        self.assertEqual(r['schema_version'],2)
        self.assertEqual(r['robustness']['policy']['multipliers'],['1','2','3'])
        for fold, diagnostics in zip(r['folds'],r['robustness']['folds']):
            self.assertEqual(fold['selected'],'fast')
            self.assertEqual(diagnostics['scenarios'][0]['strategy'],fold['test'])
            start=fold['test_start']*3600000
            self.assertEqual([(c,fee) for c,t,fee in calls if t==start],
                             [('fast','0.1'),('fast','0.2'),('fast','0.3')])
            self.assertEqual(diagnostics['entry_bar_ms'],start+3600000)
        self.assertEqual(contract.validate_report(r),r)

    def test_reference_same_allocation_and_costs(self):
        settings={**contract.DEFAULT_SETTINGS,'initial_balance':'1000','fee_pct':'1',
                  'slippage_pct':'0','position_size_pct':'20'}
        m=contract.benchmark_metrics('100','110',settings)
        q=(D('200')/D('101')).quantize(D('0.00000001'),rounding='ROUND_DOWN')
        self.assertEqual(D(m['cash']),D('1000')-q*D('101'))
        self.assertEqual(D(m['position_value']),q*110)
        self.assertEqual(D(m['fees']),q)
        self.assertEqual(m['fills'],1)
        settings['fee_pct']='0'
        self.assertEqual(D(contract.benchmark_metrics('100','110',settings)['return_pct']),D('2'))
        settings['initial_balance']='0.000000001'
        self.assertEqual(contract.benchmark_metrics('100','110',settings)['fills'],0)

    def test_skips_are_zero_interest_cash_all_scenarios(self):
        r=run_research(fixture(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,
                       evaluator=lambda c,w,b,s:metrics(s,'0',0),robustness=True,benchmark_evaluator=reference)
        for fold in r['robustness']['folds']:
            for scenario in fold['scenarios']:
                self.assertEqual(D(scenario['strategy']['return_pct']),0)
                self.assertEqual(scenario['strategy']['fills'],0)
                self.assertLess(D(scenario['excess_return_pp']),0)
        self.assertEqual(r['summary']['skipped_folds'],r['summary']['total_folds'])
        self.assertEqual(r['robustness']['summary'][0]['outperformed_benchmark_folds'],0)

    def test_identity_and_no_clobber(self):
        r=report()
        old=run_research(fixture(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,evaluator=evaluate)
        self.assertNotEqual(r['run_id'],old['run_id'])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'report.json'
            contract.write_report(path,r); before=path.read_bytes()
            contract.write_report(path,report())
            self.assertEqual(before,path.read_bytes())
            with self.assertRaises(ValueError): contract.write_report(path,old)
            self.assertEqual(contract.read_report(path),r)

    def test_tampering_is_rejected(self):
        r=report()
        mutators=[
            lambda x:x['robustness']['policy'].update(entry='first_open'),
            lambda x:x['robustness']['folds'][0].update(entry_bar_ms=0),
            lambda x:x['robustness']['folds'][0]['scenarios'][0].update(fee_pct='9'),
            lambda x:x['robustness']['folds'][0]['scenarios'][0].update(excess_return_pp='9'),
            lambda x:x['robustness']['folds'][0]['scenarios'][0]['buy_hold'].update(fees='999'),
            lambda x:x['robustness']['folds'][0]['scenarios'].reverse(),
            lambda x:x['robustness']['summary'][0].update(mean_excess_return_pp='7'),
            lambda x:x['robustness']['summary'][0].update(outperformed_benchmark_folds=True),
            lambda x:x['robustness']['folds'][0]['scenarios'][0].update(strategy=metrics(SETTINGS,'5')),
            lambda x:x.update(schema_version=1),
        ]
        for mutate in mutators:
            bad=copy.deepcopy(r);mutate(bad)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):contract.validate_report(bad)

    def test_preflight_rejects_invalid_costs_before_any_evaluation(self):
        def never(*args):self.fail('must fail before evaluation')
        for bad in ('34','NaN','Infinity'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                run_research(fixture(),'a'*64,candidates=CANDIDATES,
                             settings={**SETTINGS,'fee_pct':bad},evaluator=never,robustness=True)
        with self.assertRaises(ValueError):
            run_research(fixture(),'a'*64,robustness=1,evaluator=never)

    def test_extra_work_is_bounded_before_evaluation(self):
        def never(*args):self.fail('work must be checked before evaluation')
        with self.assertRaises(ValueError):
            run_research(fixture(5000),'a'*64,candidates=CANDIDATES,
                         settings={**SETTINGS,'train_size':100,'test_size':900},
                         evaluator=never,robustness=True)

    def test_summary_includes_losing_windows_and_uses_points(self):
        r=report()
        for column, summary in enumerate(r['robustness']['summary']):
            scenarios=[f['scenarios'][column] for f in r['robustness']['folds']]
            with localcontext(contract.CONTEXT):
                mean=sum(D(s['excess_return_pp']) for s in scenarios)/len(scenarios)
            self.assertEqual(D(summary['mean_excess_return_pp']),mean)
            self.assertEqual(summary['total_folds'],len(scenarios))
        self.assertLess(D(r['robustness']['summary'][2]['mean_strategy_return_pct']),0)

    def test_reference_uses_ledger_precision_for_tiny_residual_marks(self):
        m=contract.benchmark_metrics('100','0.000000000000000000000000000001',
                                     {**contract.DEFAULT_SETTINGS,'initial_balance':'1000'})
        with localcontext(contract.CONTEXT):
            self.assertEqual(D(m['position_value']),D(m['equity'])-D(m['cash']))
        self.assertEqual(m['fills'],1)  # Tiny mark does not mean no purchase occurred.

    def test_zero_costs_stay_zero_in_all_scenarios(self):
        r=run_research(fixture(),'a'*64,candidates=CANDIDATES,
                       settings={**SETTINGS,'fee_pct':'0','slippage_pct':'0'},
                       evaluator=evaluate,robustness=True,benchmark_evaluator=reference)
        for fold in r['robustness']['folds']:
            for scenario in fold['scenarios']:
                self.assertEqual(D(scenario['fee_pct']),0)
                self.assertEqual(D(scenario['slippage_pct']),0)
                self.assertEqual(D(scenario['buy_hold']['fees']),0)

    def test_higher_cost_positive_result_is_not_discarded_as_impossible(self):
        # Changed risk paths can improve a stressed run. Do not impose monotonicity.
        r=run_research(fixture(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,
                       evaluator=lambda c,w,b,s:metrics(s,str(D(s['fee_pct'])*10)),
                       robustness=True,benchmark_evaluator=reference)
        self.assertGreater(D(r['robustness']['summary'][2]['mean_strategy_return_pct']),
                           D(r['robustness']['summary'][0]['mean_strategy_return_pct']))
        self.assertEqual(contract.validate_report(r),r)

    def test_context_independence(self):
        with localcontext() as c:
            c.prec=3
            low=report()
        normal=report()
        low.pop('generated_at');normal.pop('generated_at')
        self.assertEqual(low,normal)


if __name__=='__main__':unittest.main()
