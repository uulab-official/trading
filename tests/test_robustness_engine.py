"""Original-engine integration. No exchange requests; synthetic history only."""
from contextlib import contextmanager, ExitStack
from decimal import Decimal as D
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT/'packages/core',ROOT/'packages/data',ROOT/'packages/strategy',ROOT/'apps/server',ROOT/'tools',ROOT/'tests'):
    sys.path.insert(0,str(path))
from test_research_engine import history, CANDIDATES, SETTINGS
from trading_bot.walk_forward_research import run_research, evaluate_benchmark_with_exchange
from trading_bot.research_report import DEFAULT_SETTINGS, benchmark_metrics, same_metrics, read_report
from trading_core.paper_exchange import PaperExchange
from quant_research import export_research

@contextmanager
def offline():
    with ExitStack() as stack:
        for target in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo'):
            stack.enter_context(patch(target,side_effect=AssertionError('evaluation must stay offline')))
        yield


class RobustnessEngineTests(unittest.TestCase):
    def test_baseline_original_exchange_rounding_costs_currency_and_entry(self):
        for quote in ('KRW','USDT'):
            bars=history(quote=quote)['candles'][60:80]
            opts={**DEFAULT_SETTINGS,'symbol':'BTC/'+quote,'timeframe':'1h','initial_balance':'1000'}
            seen=[]
            original=PaperExchange.create_order
            async def spy(self,*args,**kw):
                result=await original(self,*args,**kw)
                seen.append(result)
                return result
            with offline(),patch.object(PaperExchange,'create_order',spy):
                actual=evaluate_benchmark_with_exchange(bars,opts)
            self.assertTrue(same_metrics(actual,benchmark_metrics(bars[1][1],bars[-1][4],opts)))
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0]['timestamp'],bars[1][0])
            self.assertEqual(seen[0]['fee']['currency'],quote)
            self.assertGreater(D(actual['fees']),0)

    def test_original_runner_base_selection_unchanged_and_repeatable(self):
        with offline():
            legacy=run_research(history(),'a'*64,candidates=CANDIDATES,settings=SETTINGS)
            robust=run_research(history(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
            repeated=run_research(history(),'a'*64,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
        self.assertEqual(robust['folds'],legacy['folds'])
        self.assertEqual(robust['robustness'],repeated['robustness'])
        self.assertGreater(robust['summary']['evaluated_folds'],0)
        self.assertNotEqual(legacy['run_id'],robust['run_id'])
        for d in robust['robustness']['folds']:
            self.assertEqual([r['multiplier'] for r in d['scenarios']],['1','2','3'])
            self.assertTrue(all(r['buy_hold']['fills']==1 for r in d['scenarios']))

    def test_short_windows_stay_cash_not_forced_strategies(self):
        with offline():
            r=run_research(history(),'a'*64,candidates=CANDIDATES,
                settings={**SETTINGS,'train_size':36,'warmup':12},robustness=True)
        self.assertEqual(r['summary']['evaluated_folds'],0)
        self.assertTrue(all(s['strategy']['fills']==0 and D(s['strategy']['return_pct'])==0
                            for d in r['robustness']['folds'] for s in d['scenarios']))

    def test_future_changes_never_reselect_first_fold(self):
        data=history()
        with offline():
            first=run_research(data,'a'*64,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
            for bar in data['candles'][74:]:bar[1:5]=['200','201','199','200']
            changed=run_research(data,'b'*64,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
        self.assertEqual(first['folds'][0]['train_scores'],changed['folds'][0]['train_scores'])
        self.assertEqual(first['folds'][0]['selected'],changed['folds'][0]['selected'])
        self.assertNotEqual(first['robustness']['folds'][0]['entry_open'],changed['robustness']['folds'][0]['entry_open'])

    def test_zero_cost_and_unaffordable_quantity(self):
        bars=history()['candles'][60:80]
        for initial in ('1000','0.000000001'):
            opts={**DEFAULT_SETTINGS,'symbol':'BTC/USDT','timeframe':'1h','initial_balance':initial,
                  'fee_pct':'0','slippage_pct':'0'}
            with offline(): actual=evaluate_benchmark_with_exchange(bars,opts)
            self.assertTrue(same_metrics(actual,benchmark_metrics(bars[1][1],bars[-1][4],opts)))
            self.assertEqual(D(actual['fees']),0)
            self.assertEqual(actual['fills'],int(initial=='1000'))

    def test_tiny_mark_obeys_original_ledger_precision(self):
        bars=history()['candles'][60:80]
        bars[-1][1:5]=['1e-30']*4
        opts={**DEFAULT_SETTINGS,'symbol':'BTC/USDT','timeframe':'1h','initial_balance':'1000'}
        with offline(): actual=evaluate_benchmark_with_exchange(bars,opts)
        self.assertTrue(same_metrics(actual,benchmark_metrics(bars[1][1],bars[-1][4],opts)))

    def test_export_and_actual_readonly_api_reject_corrupt_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'history.json';out=Path(temp)/'report.json'
            source.write_text(json.dumps(history(quote='KRW')))
            before=source.read_bytes()
            with offline():
                first=export_research(source,out,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
                saved=out.read_bytes()
                second=export_research(source,out,candidates=CANDIDATES,settings=SETTINGS,robustness=True)
                self.assertEqual(first,second)
                with self.assertRaises(ValueError):export_research(source,out,candidates=CANDIDATES,settings=SETTINGS)
            self.assertEqual(out.read_bytes(),saved);self.assertEqual(source.read_bytes(),before)
            self.assertEqual(read_report(out),first)
            spec=importlib.util.spec_from_file_location('stress_http',ROOT/'apps/dashboard/server.py')
            server_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(server_module)
            server=server_module.create_server(port=0,research_path=out)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method='GET'):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                try:
                    conn.request(method,'/api/research');response=conn.getresponse()
                    return response.status,json.loads(response.read())
                finally:conn.close()
            try:
                code,body=request();self.assertEqual(code,200);self.assertEqual(body,first)
                self.assertEqual(request('POST')[0],405)
                changed=json.loads(saved);changed['robustness']['summary'][0]['mean_excess_return_pp']='999'
                out.write_text(json.dumps(changed))
                code,body=request();self.assertEqual(code,503);self.assertNotIn('summary',body)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=='__main__':unittest.main()
