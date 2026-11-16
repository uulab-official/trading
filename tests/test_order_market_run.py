"""Imported history handoff: real original engine, no broker/network access."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from trading_data.upbit_history import candles_hash, timeframe, validate_market_fixture
from trading_bot.paper_runner import PaperRunConfig, PaperRunner

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
START = 1700002800000  # exact hour boundary


def fixture(count=100, unit=60):
    start = START - START % (unit * 60000)
    prices = [100 + abs((i % 40) - 20) for i in range(count)]
    rows = [[start+i*unit*60000, str(p), str(p+2), str(p-2), str(p+1), '1']
            for i,p in enumerate(prices)]
    return dict(mode='paper', symbol='BTC/KRW', timeframe=timeframe(unit), candles=rows,
        data_provenance=dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
            unit_minutes=unit, end_exclusive_ms=start+count*unit*60000,
            retrieved_at='2025-01-01T00:00:00+00:00', bar_count=count,
            candles_sha256=candles_hash(rows), gap_policy='reject', missing_intervals=0,
            authenticated=False))


class MarketRunTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('trading_bot.market_paper_run')
        except ModuleNotFoundError as exc:
            if exc.name == 'trading_bot.market_paper_run':
                self.fail('market handoff module not implemented')
            raise
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root/'prices.json'
        self.data = fixture()
        self.write(self.data)
        self.guard = ExitStack()
        self.addCleanup(self.guard.close)
        self.guard.enter_context(patch('socket.create_connection', side_effect=AssertionError('network forbidden')))
        self.guard.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')))

    def write(self, value):
        self.input.write_text(json.dumps(value, ensure_ascii=False, indent=2))

    def inspect(self):
        return self.api.inspect_market_input(self.input, clock=lambda: NOW)

    def run_it(self, *, config=None, directory=None):
        return asyncio.run(self.api.run_market_replay(self.input, config=config,
            directory=directory or self.root/'bundle', clock=lambda: NOW))

    def assert_rejected(self):
        with self.assertRaises(ValueError): self.inspect()
        with self.assertRaises(ValueError): self.run_it()
        self.assertFalse((self.root/'bundle').exists())

    def test_inspect_preserves_bytes_and_reports_exact_provenance(self):
        raw=self.input.read_bytes(); before=set(self.root.iterdir())
        report=self.inspect()
        self.assertEqual(report['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['bar_count'],100)
        self.assertEqual(report['provenance'], self.data['data_provenance'])
        self.assertFalse(report['live_enabled']); self.assertFalse(report['authenticated'])
        self.assertEqual(raw,self.input.read_bytes()); self.assertEqual(before,set(self.root.iterdir()))

    def test_same_validator_contract_for_all_units(self):
        for unit in (1,3,5,10,15,30,60,240):
            with self.subTest(unit=unit):
                self.write(fixture(3,unit)); self.assertEqual(self.inspect()['timeframe'],timeframe(unit))

    def test_historical_age_does_not_imply_live_freshness(self):
        r=self.inspect(); self.assertGreater(r['historical_age_ms'],0)
        self.assertEqual(r['scope'],'historical_simulation_input')

    def test_missing_provenance_is_not_silently_invented(self):
        del self.data['data_provenance']; self.write(self.data); self.assert_rejected()

    def test_unknown_outer_field_including_credentials_refused(self):
        self.data['api_key']='never-leak-secret';self.write(self.data);self.assert_rejected()

    def test_mode_live_refused(self):
        self.data['mode']='live';self.write(self.data);self.assert_rejected()

    def test_symbol_mismatch_refused(self):
        self.data['symbol']='ETH/KRW';self.write(self.data);self.assert_rejected()

    def test_timeframe_mismatch_refused(self):
        self.data['timeframe']='5m';self.write(self.data);self.assert_rejected()

    def test_changed_price_with_old_checksum_refused(self):
        self.data['candles'][1][4]='100';self.write(self.data);self.assert_rejected()

    def test_gapped_history_not_filled(self):
        self.data['candles'][4][0]+=60000;self.write(self.data);self.assert_rejected()

    def test_reordered_history_not_sorted(self):
        self.data['candles'].reverse();self.write(self.data);self.assert_rejected()

    def test_noncanonical_decimal_not_normalized(self):
        self.data['candles'][0][1]+='.0';self.write(self.data);self.assert_rejected()

    def test_bool_timestamp_is_not_integer(self):
        self.data['candles'][0][0]=True;self.write(self.data);self.assert_rejected()

    def test_duplicate_json_key_refused(self):
        self.input.write_text('{"mode":"paper",'+json.dumps(self.data)[1:]);self.assert_rejected()

    def test_nan_json_refused(self):
        self.data['candles'][0][1]=float('nan');self.write(self.data);self.assert_rejected()

    def test_more_than_durable_limit_refused_before_directory(self):
        self.write(fixture(5001));self.assert_rejected()

    def test_oversized_file_refused(self):
        self.input.write_bytes(b' '*(4*1024*1024+1));self.assert_rejected()

    def test_future_retrieval_time_is_not_trusted(self):
        self.data['data_provenance']['retrieved_at']='2099-01-01T00:00:00Z'
        self.write(self.data);self.assert_rejected()

    def test_naive_clock_refused(self):
        with self.assertRaises(ValueError):
            self.api.inspect_market_input(self.input,clock=lambda:datetime(2026,1,1))

    def test_authenticated_claim_refused(self):
        self.data['data_provenance']['authenticated']=True;self.write(self.data);self.assert_rejected()

    def test_symlink_source_refused(self):
        copy=self.root/'copy';self.input.rename(copy);self.input.symlink_to(copy);self.assert_rejected()

    def test_fifo_source_does_not_block(self):
        self.input.unlink();os.mkfifo(self.input)
        self.assert_rejected()

    def test_existing_bundle_preserved(self):
        target=self.root/'bundle';target.mkdir();(target/'keep').write_text('unchanged')
        with self.assertRaises((ValueError,FileExistsError)):self.run_it()
        self.assertEqual((target/'keep').read_text(),'unchanged')
        self.assertEqual(len(list(target.iterdir())),1)

    def test_configuration_mismatch_before_output(self):
        config=PaperRunConfig(symbol='ETH/KRW',timeframe='1h')
        with self.assertRaises(ValueError):self.run_it(config=config)
        self.assertFalse((self.root/'bundle').exists())

    def test_original_ma_rsi_economic_parity_and_source_binding(self):
        from trading_bot.durable_paper_audit import audit_durable_replay
        for strategy in ('ma_cross','rsi'):
            with self.subTest(strategy=strategy):
                target=self.root/strategy
                config=PaperRunConfig(symbol='BTC/KRW',timeframe='1h',strategy=strategy,
                    initial_balance=Decimal('1000000'),fast_period=3,slow_period=5)
                expected=asyncio.run(PaperRunner(config).run(self.data['candles']))
                result=self.run_it(config=config,directory=target)
                self.assertEqual(result['run']['replay'],expected)
                self.assertEqual(result['status'],'completed_halted')
                self.assertEqual((target/'source.json').read_bytes(),self.input.read_bytes())
                manifest=json.loads((target/'source-manifest.json').read_bytes())
                self.assertEqual(manifest['execution_input_sha256'],
                    hashlib.sha256((target/'run/input.json').read_bytes()).hexdigest())
                self.assertEqual(manifest['provenance'],self.data['data_provenance'])
                self.assertEqual(manifest['config'],config.identity())
                self.assertEqual(asyncio.run(audit_durable_replay(target/'run'))['status'],'verified_completion')
                self.assertEqual(target.stat().st_mode & 0o777,0o700)
                self.assertEqual((target/'source.json').stat().st_mode & 0o777,0o600)

    def test_source_files_exist_before_account_creation(self):
        async def fail(*args, **kwargs):
            target=self.root/'bundle'
            self.assertEqual((target/'source.json').read_bytes(),self.input.read_bytes())
            self.assertTrue((target/'source-manifest.json').exists())
            self.assertFalse((target/'run').exists())
            self.assertTrue(kwargs['resumable'])
            raise RuntimeError('controlled_failure')
        with patch.object(self.api,'run_durable_replay',side_effect=fail):
            with self.assertRaises(RuntimeError):self.run_it()
        self.assertFalse((self.root/'bundle/run/result.json').exists())

    def test_changed_original_after_capture_not_reopened(self):
        original=self.api.run_durable_replay
        raw=self.input.read_bytes()
        async def change(*args, **kwargs):
            self.input.write_text('invalid new original')
            return await original(*args,**kwargs)
        with patch.object(self.api,'run_durable_replay',side_effect=change): result=self.run_it()
        self.assertEqual((self.root/'bundle/source.json').read_bytes(),raw)
        self.assertEqual(result['source']['source_sha256'],hashlib.sha256(raw).hexdigest())

    def test_changed_bundle_source_after_execution_is_refused(self):
        original=self.api.run_durable_replay
        async def change(*args,**kwargs):
            result=await original(*args,**kwargs)
            (self.root/'bundle/source.json').write_text('{}')
            return result
        with patch.object(self.api,'run_durable_replay',side_effect=change):
            with self.assertRaises(ValueError):self.run_it()
        self.assertTrue((self.root/'bundle/run/result.json').exists())  # never delete valid child artifacts

    def test_cancellation_preserves_source_and_never_reports_success(self):
        async def cancel(*a,**kw):raise asyncio.CancelledError()
        with patch.object(self.api,'run_durable_replay',side_effect=cancel):
            with self.assertRaises(asyncio.CancelledError):self.run_it()
        self.assertTrue((self.root/'bundle/source-manifest.json').exists())

    def test_cli_inspect_json_and_no_write(self):
        process=subprocess.run([sys.executable,str(ROOT/'tools/paper_market.py'),'inspect',str(self.input)],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(json.loads(process.stdout)['bar_count'],100)
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_cli_invalid_input_fixed_error_no_secret(self):
        self.input.write_text('never-leak-secret')
        process=subprocess.run([sys.executable,str(ROOT/'tools/paper_market.py'),'inspect',str(self.input)],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(process.returncode,2)
        self.assertEqual(json.loads(process.stdout),{'error':'market_paper_input_invalid'})
        self.assertNotIn('never-leak-secret',process.stdout+process.stderr)

    def test_final_source_change_during_execution_binding_check_is_rejected(self):
        original=self.api._read_regular
        def change(path, *args, **kwargs):
            raw=original(path,*args,**kwargs)
            if Path(path)==self.root/'bundle/run/input.json':
                (self.root/'bundle/source.json').write_text('{}')
            return raw
        with patch.object(self.api,'_read_regular',side_effect=change):
            with self.assertRaises(ValueError):self.run_it()

    def test_published_source_permissions_changed_are_refused(self):
        original=self.api.run_durable_replay
        async def change(*a,**kw):
            result=await original(*a,**kw)
            (self.root/'bundle/source-manifest.json').chmod(0o644)
            return result
        with patch.object(self.api,'run_durable_replay',side_effect=change):
            with self.assertRaises(ValueError):self.run_it()

    def test_source_replaced_while_it_is_read_is_refused(self):
        real=self.api.os.fstat
        replaced=False
        def replace(fd):
            nonlocal replaced
            result=real(fd)
            if not replaced:
                replaced=True
                copy=self.root/'replacement'
                copy.write_bytes(self.input.read_bytes())
                os.replace(copy,self.input)
            return result
        with patch.object(self.api.os,'fstat',side_effect=replace):
            with self.assertRaises(ValueError):self.inspect()

    def test_oversized_strategy_options_cannot_select_another_market(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.api.run_market_replay(self.input,directory=self.root/'bundle',
                config_options={'symbol':'ETH/KRW'}))
        self.assertFalse((self.root/'bundle').exists())

    def test_insufficient_strategy_warmup_is_a_valid_no_trade_run(self):
        self.write(fixture(10));result=self.run_it()
        self.assertEqual(result['run']['replay']['trades'],[])
        self.assertEqual(result['status'],'completed_halted')

    def test_actual_import_client_output_feeds_existing_durable_engine(self):
        from trading_data.upbit_history import UpbitHistoryClient
        source=fixture(100)
        p=source['data_provenance'];end=datetime.fromtimestamp(p['end_exclusive_ms']/1000,timezone.utc)
        rows=[]
        for t,o,h,l,c,v in reversed(source['candles']):
            rows.append(dict(market='KRW-BTC',unit=60,
                candle_date_time_utc=datetime.fromtimestamp(t/1000,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                opening_price=o,high_price=h,low_price=l,trade_price=c,candle_acc_trade_volume=v))
        client=UpbitHistoryClient(get=lambda url:(json.dumps(rows).encode(),{}),
            sleep=lambda _:None,clock=lambda:NOW)
        imported=client.download('KRW-BTC',unit=60,count=100,end=end)
        self.write(imported); result=self.run_it()
        self.assertEqual(result['source']['provenance'],imported['data_provenance'])
        self.assertEqual(result['run']['replay']['bars_processed'],100)

    def test_cli_no_args_and_live_flag_use_fixed_json(self):
        for args in ([],['inspect',str(self.input),'--live']):
            process=subprocess.run([sys.executable,str(ROOT/'tools/paper_market.py'),*args],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(process.returncode,2)
            self.assertEqual(json.loads(process.stdout),{'error':'market_paper_arguments_invalid'})

    def test_sigterm_preserves_nested_run_then_explicit_recovery(self):
        script = r"""
import asyncio, importlib.util, os, signal, sys
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server'):
    sys.path.insert(0,str(root/part))
from trading_core.paper_session import _PaperAdapter
original=_PaperAdapter.create_order
async def stop(adapter,*args,**kwargs):
    result=await original(adapter,*args,**kwargs)
    os.kill(os.getpid(),signal.SIGTERM)
    await asyncio.sleep(0.02)
    return result
_PaperAdapter.create_order=stop
spec=importlib.util.spec_from_file_location('market_cli',root/'tools/paper_market.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
raise SystemExit(module.main(['run',sys.argv[2],'--directory',sys.argv[3]]))
"""
        process=subprocess.run([sys.executable,'-c',script,str(ROOT),str(self.input),str(self.root/'bundle')],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,143,process.stdout+process.stderr)
        self.assertEqual(json.loads(process.stdout)['signal'],'SIGTERM')
        child=self.root/'bundle/run'
        self.assertFalse((child/'result.json').exists())
        from trading_core.paper_session_observer import PaperSessionObserver
        snapshot=PaperSessionObserver(child/'account.sqlite').read_evidence()['payload']
        self.assertEqual(len(snapshot['book']['trades']),1)
        self.assertEqual(snapshot['control']['mode'],'halted')
        from trading_bot.durable_paper_resume import resume_durable_replay
        result=asyncio.run(resume_durable_replay(child,reconcile_saved_fills=True))
        self.assertEqual(result['status'],'completed_halted')
        self.assertEqual(len(result['replay']['trades']),2)
        from trading_bot.durable_paper_audit import audit_durable_replay
        self.assertEqual(asyncio.run(audit_durable_replay(child))['status'],'verified_completion')

    def test_cli_run_then_original_audit(self):
        process=subprocess.run([sys.executable,str(ROOT/'tools/paper_market.py'),'run',str(self.input),
            '--directory',str(self.root/'bundle'),'--initial-balance','1000000'],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,0,process.stderr+process.stdout)
        self.assertEqual(json.loads(process.stdout)['status'],'completed_halted')
        audit=subprocess.run([sys.executable,str(ROOT/'tools/paper_audit.py'),str(self.root/'bundle/run')],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(audit.returncode,0,audit.stderr+audit.stdout)


if __name__=='__main__':unittest.main()
