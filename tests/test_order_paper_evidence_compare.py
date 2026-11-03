"""Compare real saved paper-account observations; no replacement fill engine."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D, localcontext
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import Order, OrderOutcomeUnknown, OrderSide, OrderStatus, OrderType
from trading_core.order_state_store import _canonical, _digest
from trading_core.paper_evidence import export_evidence, verify_evidence
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver


class EvidenceComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'account.sqlite'
        self.session = PaperTradingSession(self.db, create=True, initial_balance='1000', fee_pct='0.1')
        await self.session.__aenter__()
        self.session.exchange.set_price('BTC/USDT', D('100'), timestamp=1700000000000)
        self.reader = PaperSessionObserver(self.db)
        self.before = self.save('before')

    async def asyncTearDown(self):
        await self.session.close()
        self.tmp.cleanup()

    def api(self):
        spec = importlib.util.find_spec('trading_core.paper_evidence_compare')
        self.assertIsNotNone(spec, 'evidence comparison module missing')
        return importlib.import_module('trading_core.paper_evidence_compare')

    def save(self, name):
        path = self.root / (name + '.paper-evidence.json')
        export_evidence(self.reader, path)
        return path

    def compare(self, after, before=None):
        return self.api().compare_evidence(before or self.before, after)

    def rewrite(self, path, change):
        value = json.loads(path.read_text())
        change(value)
        value['snapshot']['digest'] = _digest(_canonical(value['payload']))
        value['content_sha256'] = _digest(_canonical({k: v for k, v in value.items() if k != 'content_sha256'}))
        path.write_text(_canonical(value) + '\n')
        verify_evidence(path)  # Conflicts must be independently valid evidence, not corrupt input.

    def conflict(self, after, reason, before=None):
        api = self.api()
        with self.assertRaises(api.PaperEvidenceComparisonError) as caught:
            api.compare_evidence(before or self.before, after)
        self.assertEqual(str(caught.exception), 'paper_evidence_comparison_conflict')
        self.assertEqual(caught.exception.reason, reason)

    async def order(self, key, side=OrderSide.BUY, amount='2'):
        return await self.session.manager.place_order('BTC/USDT', side, OrderType.MARKET,
            D(amount), client_order_id=key)

    async def test_same_snapshot_is_zero_change_not_new_trade(self):
        r = self.compare(self.before)
        self.assertEqual(r['relation'], 'same_snapshot')
        self.assertEqual(D(r['cash']['delta']), 0)
        self.assertEqual(r['new_fills']['count'], 0)
        self.assertFalse(r['live_enabled'])
        self.assertEqual(r['scope'], 'offline_observations_not_current_account')

    async def test_resaved_same_revision_different_capture_envelope_is_same_snapshot(self):
        r = self.compare(self.save('repeat'))
        self.assertEqual(r['relation'], 'same_snapshot')
        self.assertEqual(r['before']['snapshot'], r['after']['snapshot'])

    async def test_buy_explains_cash_inventory_and_fee_once(self):
        await self.order('buy')
        r = self.compare(self.save('after'))
        self.assertEqual(D(r['cash']['delta']), D('-200.2'))
        self.assertEqual(D(r['cash']['from_new_fills']), D('-200.2'))
        self.assertEqual(D(r['cash']['rounding_delta']), 0)
        self.assertEqual(D(r['fees']['delta']), D('0.2'))
        self.assertEqual(r['new_fills']['count'], 1)
        self.assertEqual(r['orders']['new']['count'], 1)
        self.assertEqual(D(r['positions']['items'][0]['delta']), 2)
        self.assertTrue(r['reconciliation']['balances_match'])

    async def test_sell_after_price_change_uses_only_new_fill(self):
        await self.order('buy'); before = self.save('bought')
        self.session.exchange.set_price('BTC/USDT', D('125'), timestamp=1700000000001)
        await self.order('sell', OrderSide.SELL, '1')
        r = self.compare(self.save('sold'), before)
        self.assertEqual(D(r['cash']['delta']), D('124.875'))
        self.assertEqual(D(r['fees']['delta']), D('0.125'))
        self.assertEqual(r['new_fills']['count'], 1)
        self.assertEqual(D(r['positions']['items'][0]['delta']), -1)
        self.assertEqual(r['marks']['count'], 1)
        self.assertEqual(r['valuation']['basis'], 'saved_marks_not_realized_pnl')

    async def test_price_only_change_is_not_trade_or_fee(self):
        await self.order('buy'); before = self.save('bought')
        self.session.exchange.set_price('BTC/USDT', D('110'), timestamp=1700000000001)
        r = self.compare(self.save('marked'), before)
        self.assertEqual(r['new_fills']['count'], 0)
        self.assertEqual(D(r['cash']['delta']), 0)
        self.assertEqual(D(r['fees']['delta']), 0)
        self.assertEqual(D(r['valuation']['delta']), 20)
        self.assertEqual(r['marks']['count'], 1)

    async def test_lost_ack_recovery_does_not_count_existing_fill_twice(self):
        original = self.session.exchange.create_order
        async def lost(**kwargs):
            await original(**kwargs)
            raise TimeoutError('not exported')
        with patch.object(self.session.exchange, 'create_order', side_effect=lost):
            with self.assertRaises(OrderOutcomeUnknown): await self.order('lost')
        before = self.save('unknown')
        from trading_core.order_reconciliation import reconcile_order
        await reconcile_order(self.session.manager, 'lost', allow_client_lookup=True)
        r = self.compare(self.save('confirmed'), before)
        self.assertEqual(r['orders']['confirmed_existing_fills'], 1)
        self.assertEqual(r['orders']['changed']['count'], 1)
        self.assertEqual(r['new_fills']['count'], 0)
        self.assertEqual(D(r['fees']['delta']), 0)
        self.assertEqual(D(r['cash']['delta']), 0)

    async def test_unresolved_without_fill_is_not_rejection(self):
        with patch.object(self.session.exchange, 'create_order', side_effect=TimeoutError()):
            with self.assertRaises(OrderOutcomeUnknown): await self.order('unknown')
        r = self.compare(self.save('unknown'))
        self.assertEqual(r['new_fills']['count'], 0)
        self.assertEqual(r['orders']['unresolved_after'], 1)
        self.assertEqual(r['orders']['new']['items'][0]['status'], 'unknown')

    async def test_no_database_or_network_needed_and_input_bytes_unchanged(self):
        await self.order('buy'); after = self.save('after')
        contents = self.before.read_bytes(), after.read_bytes()
        await self.session.close(); self.db.unlink()
        with patch('sqlite3.connect', side_effect=AssertionError('DB used')), patch('socket.socket', side_effect=AssertionError('network used')):
            self.compare(after)
        self.assertEqual(contents, (self.before.read_bytes(), after.read_bytes()))

    async def test_each_source_is_opened_once_not_verified_then_reopened(self):
        api = self.api(); after = self.save('after')
        original = os.open
        with patch('os.open', wraps=original) as opened:
            api.compare_evidence(self.before, after)
        self.assertEqual(opened.call_count, 2)

    async def test_different_account_is_rejected_without_economic_report(self):
        after = self.save('other')
        self.rewrite(after, lambda v: (v['payload'].__setitem__('account_id', 'f'*32), v['snapshot'].__setitem__('account_id', 'f'*32)))
        self.conflict(after, 'different_account')

    async def test_changed_initial_settings_rejected(self):
        after = self.save('other')
        def change(v):
            v['payload']['initial']['initial_balance'] = '2000'
            v['payload']['book']['balances']['USDT'] = '2000'
        self.rewrite(after, change)
        self.conflict(after, 'configuration_changed')

    async def test_schema_policy_change_rejected(self):
        after = self.save('other')
        def change(v):
            v['snapshot']['session_version'] = 2
            v['payload']['policy'] = {'max_quote_age_ms': None, 'max_order_notional': '200'}
        self.rewrite(after, change)
        self.conflict(after, 'configuration_changed')

    async def test_reversed_revisions_are_rejected(self):
        await self.order('buy'); after = self.save('after')
        self.conflict(self.before, 'revision_reversed', after)

    async def test_same_revision_changed_content_rejected(self):
        await self.order('buy'); after = self.save('after')
        revision = json.loads(self.before.read_text())['snapshot']['revision']
        self.rewrite(after, lambda v: v['snapshot'].__setitem__('revision', revision))
        self.conflict(after, 'same_revision_changed')

    async def test_large_revision_is_preserved_as_string(self):
        after = self.save('after')
        self.rewrite(self.before, lambda v: v['snapshot'].__setitem__('revision', str(2**60)))
        self.rewrite(after, lambda v: v['snapshot'].__setitem__('revision', str(2**60+1)))
        r = self.compare(after)
        self.assertEqual(r['after']['snapshot']['revision'], str(2**60+1))
        self.assertEqual(r['revision_delta'], '1')

    async def test_reversed_capture_clock_is_warning_not_reordering(self):
        await self.order('buy'); after = self.save('after')
        self.rewrite(after, lambda v: v.__setitem__('captured_at', '2020-01-01T00:00:00+00:00'))
        r = self.compare(after)
        self.assertIn('capture_clock_reversed', r['warnings'])
        self.assertEqual(r['relation'], 'forward_snapshot')

    async def test_old_fill_timestamp_rewrite_rejected_even_with_valid_checksums(self):
        await self.order('buy'); before = self.save('bought'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['book']['orders']['buy']['timestamp'] += 1
            v['payload']['book']['trades'][0]['timestamp'] += 1
        self.rewrite(after, change)
        self.conflict(after, 'fill_history_changed', before)

    async def test_existing_order_strategy_change_rejected(self):
        await self.order('buy'); before = self.save('bought'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['orders']['buy']['strategy_id'] = 'other'
        self.rewrite(after, change)
        self.conflict(after, 'order_history_changed', before)

    async def test_filled_order_cannot_regress_to_unknown(self):
        await self.order('buy'); before = self.save('bought'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            o = v['payload']['orders']['buy']; o.update(status='unknown', filled='0', remaining=o['amount'])
        self.rewrite(after, change)
        self.conflict(after, 'order_history_changed', before)

    async def test_all_137_orders_counted_with_bounded_details(self):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        orders = {f'o-{i:04}': Order(f'o-{i:04}', 'BTC/USDT', OrderSide.BUY, OrderType.MARKET,
            D('1'), remaining=D('1'), status=OrderStatus.UNKNOWN, created_at=when, updated_at=when) for i in range(137)}
        self.session._store.save(orders)
        after = self.save('after'); r = self.compare(after)
        self.assertEqual(r['orders']['new']['count'], 137)
        self.assertEqual(len(r['orders']['new']['items']), 100)
        self.assertTrue(r['orders']['new']['truncated'])
        self.assertEqual(r['orders']['unresolved_after'], 137)

    async def test_removed_order_rejected_when_both_accounts_individually_validate(self):
        with patch.object(self.session.exchange, 'create_order', side_effect=TimeoutError()):
            with self.assertRaises(OrderOutcomeUnknown): await self.order('unknown')
        before = self.save('unknown'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['orders'].clear()
        self.rewrite(after, change)
        self.conflict(after, 'order_history_changed', before)

    async def test_small_fill_against_large_cash_exposes_rounding_residual(self):
        await self.order('tiny', amount='0.00000000000000000000000000001')
        r = self.compare(self.save('after'))
        self.assertEqual(D(r['cash']['delta']), 0)
        self.assertEqual(D(r['cash']['from_new_fills']), D('-1.001E-27'))
        self.assertEqual(D(r['cash']['rounding_delta']), D('1.001E-27'))
        self.assertTrue(r['reconciliation']['balances_match'])

    async def test_ambient_decimal_context_does_not_change_results(self):
        await self.order('buy'); after = self.save('after')
        r = self.compare(after)
        with localcontext() as ctx:
            ctx.prec = 3
            other = self.compare(after)
        for key in ('cash','fees','positions','valuation','new_fills'):
            self.assertEqual(r[key], other[key])

    async def test_missing_mark_keeps_valuation_null(self):
        await self.order('buy'); before = self.save('bought'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['book']['tickers'].clear()
        self.rewrite(after, change)
        r = self.compare(after, before)
        self.assertIsNone(r['valuation']['after'])
        self.assertIsNone(r['valuation']['delta'])
        self.assertEqual(r['marks']['items'][0]['after'], None)

    async def test_new_control_generation_is_observed_without_claimed_timeline(self):
        path = self.root/'controlled.sqlite'
        async with PaperTradingSession(path, create=True, enable_control=True) as session:
            reader = PaperSessionObserver(path)
            before, after = self.root/'c1.json', self.root/'c2.json'
            export_evidence(reader, before)
            await session.set_operating_mode('active', expected_generation=0, reason='operator')
            export_evidence(reader, after)
        r = self.compare(after, before)
        self.assertTrue(r['control']['changed'])
        self.assertEqual(r['control']['generation_delta'], 1)
        self.assertEqual(r['control']['intermediate_changes'], 'not_recorded')

    async def test_recomputed_bad_cash_fails_validation_before_comparison(self):
        after = self.save('after'); value=json.loads(after.read_text())
        value['payload']['book']['balances']['USDT'] = '1'
        value['snapshot']['digest'] = _digest(_canonical(value['payload']))
        value['content_sha256'] = _digest(_canonical({k:v for k,v in value.items() if k!='content_sha256'}))
        after.write_text(_canonical(value)+'\n')
        from trading_core.paper_evidence import PaperEvidenceInvalid
        api=self.api()
        with self.assertRaises(PaperEvidenceInvalid): api.compare_evidence(self.before,after)

    async def test_cli_compare_outputs_clean_json_and_no_file_change(self):
        await self.order('buy'); after = self.save('after')
        contents=(self.before.read_bytes(),after.read_bytes())
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_evidence.py'),
            'compare',str(self.before),str(after)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertEqual(json.loads(result.stdout)['new_fills']['count'],1)
        self.assertEqual(contents,(self.before.read_bytes(),after.read_bytes()))

    async def test_cli_conflict_is_exit_five_with_fixed_reason(self):
        await self.order('buy'); after = self.save('after')
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_evidence.py'),
            'compare',str(after),str(self.before)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,5,result.stderr)
        self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_comparison_conflict','reason':'revision_reversed'})
        self.assertNotIn(str(self.root),result.stderr)

    async def test_cli_invalid_input_stays_exit_two_without_path_leak(self):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_evidence.py'),
            'compare',str(self.before),str(self.root/'missing')],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,'')
        self.assertTrue(result.stderr.startswith('{'), result.stderr)
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_invalid'})
        self.assertNotIn(str(self.root),result.stderr)

    async def test_cli_import_failure_is_redacted_without_secondary_import(self):
        import builtins
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        spec = importlib.util.spec_from_file_location('comparison_cli', ROOT/'tools/paper_evidence.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        original = builtins.__import__
        def broken(name, *args, **kwargs):
            if name in ('trading_core.paper_evidence', 'trading_core.paper_evidence_compare'):
                raise ImportError('SECRET_LOCAL_PATH')
            return original(name, *args, **kwargs)
        out, err = StringIO(), StringIO()
        with patch('builtins.__import__', side_effect=broken), redirect_stderr(err), redirect_stdout(out):
            result = module.main(['compare', str(self.before), str(self.before)])
        self.assertEqual(result, 2)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(json.loads(err.getvalue()), {'error': 'paper_evidence_operation_failed'})
        self.assertNotIn('SECRET', err.getvalue())

    async def test_reordered_old_fills_rejected_despite_equal_balances(self):
        await self.order('buy1', amount='1'); await self.order('buy2', amount='1')
        before = self.save('bought'); after = self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['book']['trades'].reverse()
        self.rewrite(after, change)
        self.conflict(after, 'fill_history_changed', before)

    async def test_existing_order_creation_time_cannot_be_rewritten(self):
        await self.order('buy'); before=self.save('bought'); after=self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['orders']['buy']['created_at'] = '2020-01-01T00:00:00+00:00'
        self.rewrite(after, change)
        self.conflict(after, 'order_history_changed', before)

    async def test_control_same_generation_different_content_is_conflict(self):
        path=self.root/'controlled.sqlite'
        async with PaperTradingSession(path,create=True,enable_control=True) as session:
            await session.set_operating_mode('active',expected_generation=0,reason='operator')
            before, after=self.root/'c1.json',self.root/'c2.json'
            reader=PaperSessionObserver(path)
            export_evidence(reader,before);export_evidence(reader,after)
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['control']['reason']='maintenance'
        self.rewrite(after,change)
        self.conflict(after,'control_history_changed',before)

    async def test_control_generation_regression_is_conflict(self):
        path=self.root/'controlled.sqlite'
        async with PaperTradingSession(path,create=True,enable_control=True) as session:
            await session.set_operating_mode('active',expected_generation=0,reason='operator')
            before, after=self.root/'c1.json',self.root/'c2.json'
            reader=PaperSessionObserver(path)
            export_evidence(reader,before);export_evidence(reader,after)
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['control']={'generation':0,'mode':'halted','reason':'created'}
        self.rewrite(after,change)
        self.conflict(after,'control_history_changed',before)

    async def test_krw_multiple_assets_use_correct_units_and_all_changes(self):
        path=self.root/'krw.sqlite'
        async with PaperTradingSession(path,create=True,initial_balance='1000000',quote_currency='KRW',fee_pct='0.05') as session:
            reader=PaperSessionObserver(path);before,after=self.root/'k1.json',self.root/'k2.json'
            export_evidence(reader,before)
            for asset,price in [('BTC','10000'),('ETH','2000')]:
                symbol=asset+'/KRW';session.exchange.set_price(symbol,D(price),timestamp=1700000000000)
                await session.manager.place_order(symbol,OrderSide.BUY,OrderType.MARKET,D('2'),client_order_id=asset)
            export_evidence(reader,after)
        r=self.compare(after,before)
        self.assertEqual(r['quote_currency'],'KRW')
        self.assertEqual(r['new_fills']['count'],2)
        self.assertEqual(r['positions']['count'],2)
        self.assertEqual(D(r['fees']['delta']),D('12'))
        self.assertEqual(D(r['cash']['delta']),D('-24012'))

    async def test_large_mark_analysis_bound_fails_without_silent_rounding(self):
        after=self.save('large')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            ticker=v['payload']['book']['tickers']['BTC/USDT']
            for key in ('last','bid','ask'):ticker[key]='1E+121'
        self.rewrite(after,change)
        self.conflict(after,'numeric_analysis_bound')

    async def test_invalid_second_file_never_returns_partial_first_report(self):
        bad=self.root/'bad.json';bad.write_text('not-json');bad.chmod(0o600)
        from trading_core.paper_evidence import PaperEvidenceInvalid
        with self.assertRaises(PaperEvidenceInvalid):self.api().compare_evidence(self.before,bad)

    async def test_file_replaced_during_validation_is_rejected(self):
        import trading_core.paper_evidence as evidence
        api=self.api();after=self.save('after');original=evidence.validate_evidence
        def replace(raw):
            report=original(raw)
            replacement=self.root/'replacement';replacement.write_bytes(after.read_bytes());replacement.chmod(0o600)
            os.replace(replacement,self.before)
            return report
        with patch.object(evidence,'validate_evidence',side_effect=replace):
            with self.assertRaises(evidence.PaperEvidenceInvalid):api.compare_evidence(self.before,after)

    async def test_unknown_never_returns_to_submitting(self):
        with patch.object(self.session.exchange,'create_order',side_effect=TimeoutError()):
            with self.assertRaises(OrderOutcomeUnknown):await self.order('unknown')
        before=self.save('unknown');after=self.save('after')
        def change(v):
            v['snapshot']['revision'] = str(int(v['snapshot']['revision'])+1)
            v['payload']['orders']['unknown']['status']='submitting'
        self.rewrite(after,change)
        self.conflict(after,'order_history_changed',before)
