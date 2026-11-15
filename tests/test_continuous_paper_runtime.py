"""Real capture/account continuation, with all network boundaries forbidden."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))
from trading_bot.market_capture_store import capture_writer, read_capture
from trading_data.upbit_history import candles_hash

BASE = datetime(2026, 11, 15, tzinfo=timezone.utc)
START = int(BASE.timestamp()) * 1000
CAPTURE_CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=50,
                      retention_bars=200, poll_seconds=15)
OPTIONS = dict(initial_balance='1000', fee_pct='0', slippage_pct='0',
               position_size_pct='100', max_daily_loss_pct='100', max_drawdown_pct='100')


class Clock:
    def __init__(self):
        self.now = BASE + timedelta(minutes=50, seconds=6)
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)


def row(index):
    if index < 50:
        return [START + index * 60000, '100', '101', '99', '100', '1']
    if index == 50:
        return [START + index * 60000, '105', '115', '100', '110', '1']
    return [START + index * 60000, '120', '125', '115', '120', '1']


def fixture(first, count, now, rows=None):
    rows = rows or [row(i) for i in range(first, first + count)]
    provenance = dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
        unit_minutes=1, end_exclusive_ms=rows[-1][0] + 60000, retrieved_at=now.isoformat(),
        bar_count=len(rows), candles_sha256=candles_hash(rows), gap_policy='reject',
        missing_intervals=0, authenticated=False)
    return dict(mode='paper', symbol='BTC/KRW', timeframe='1m', candles=rows,
                data_provenance=provenance)


class StreamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / 'capture'
        self.account = self.root / 'account'
        self.clock = Clock()
        self.module = importlib.import_module('trading_bot.paper_stream')
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            p = patch(target, side_effect=AssertionError('network forbidden'))
            p.start()
            self.addCleanup(p.stop)
        with capture_writer(self.capture, config=CAPTURE_CONFIG, now=self.clock()) as writer:
            writer.apply(fixture(0, 50, self.clock()), at=self.clock())

    async def start(self, **kwargs):
        return await self.module.run_stream(self.account, capture=self.capture,
            config_options=OPTIONS, max_polls=1, clock=self.clock, sleep=self.clock.sleep, **kwargs)

    async def resume(self, **kwargs):
        return await self.module.run_stream(self.account, max_polls=1,
            clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def append(self, count=1):
        previous = read_capture(self.capture)['fixture']['candles'][-1][0]
        index = (previous - START) // 60000
        self.clock.now = BASE + timedelta(minutes=index + count + 1, seconds=6)
        with capture_writer(self.capture) as writer:
            writer.apply(fixture(index, count + 1, self.clock()), at=self.clock())

    def saved(self):
        from trading_bot.paper_stream_store import read_stream
        return read_stream(self.account)

    async def test_activation_records_cash_without_historical_trades(self):
        before = (self.capture / 'capture.sqlite3').read_bytes()
        result = await self.start()
        self.assertEqual(result['exit_code'], 0)
        saved = self.saved()['engine']
        self.assertEqual(saved['next_index'], 0)
        self.assertEqual(saved['cash'], '1000')
        self.assertEqual(saved['holding'], '0')
        self.assertEqual(saved['fill_count'], 0)
        self.assertIsNone(saved['pending_signal'])
        self.assertEqual(saved['last_open_ms'], START + 49 * 60000)
        self.assertEqual((self.capture / 'capture.sqlite3').read_bytes(), before)

    async def test_next_bar_and_verified_resume_preserve_one_continuous_account(self):
        await self.start()
        self.append()
        first = await self.resume()
        self.assertEqual(first['exit_code'], 0)
        self.assertEqual(self.saved()['engine']['pending_signal'], {'action': 'buy', 'source_index': 0})
        self.assertEqual(self.saved()['engine']['fill_count'], 0)
        self.append()
        await self.resume()
        saved = self.saved()
        self.assertEqual(saved['engine']['next_index'], 2)
        self.assertEqual(saved['engine']['fill_count'], 1)
        self.assertEqual(Decimal(saved['engine']['holding']), Decimal('8.33333333'))
        self.assertEqual(Decimal(saved['engine']['cash']), Decimal('0.00000040'))
        audit = await self.module.audit_stream(self.account)
        self.assertEqual(audit['status'], 'verified_prefix')
        self.assertEqual(audit['events_verified'], 2)
        self.assertEqual(audit['engine_sha256'], saved['engine_sha256'])
        await self.resume()
        self.assertEqual(self.saved()['engine'], saved['engine'])
        self.assertEqual(self.saved()['event_head_sha256'], saved['event_head_sha256'])

    async def test_current_capture_catches_up_chronologically_in_one_poll(self):
        await self.start()
        self.append(5)
        result = await self.resume()
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(self.saved()['engine']['next_index'], 5)
        self.assertEqual(self.saved()['engine']['fill_count'], 1)
        self.assertEqual((await self.module.audit_stream(self.account))['events_verified'], 5)

    async def test_stale_source_waits_without_consuming_pending(self):
        await self.start()
        self.append()
        await self.resume()
        before = self.saved()['engine']
        self.append()
        self.clock.now += timedelta(minutes=5)
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine'], before)
        self.assertEqual(self.saved()['control']['last_error'], 'source_not_current')

    async def test_stopped_source_does_not_admit_a_new_bar(self):
        await self.start()
        self.append(2)
        with capture_writer(self.capture) as writer:
            writer.stop(self.clock())
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine']['next_index'], 0)

    async def test_terminal_capture_failure_blocks_and_signal_cannot_clear_it(self):
        await self.start()
        with capture_writer(self.capture) as writer:
            writer.fail('rate_blocked', at=self.clock())
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        before = self.saved()
        self.assertEqual(before['control']['phase'], 'blocked')
        result = await self.resume(stop=lambda: 15)
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(self.saved(), before)

    async def test_source_replacement_is_terminal_and_never_switches_accounts(self):
        await self.start()
        before = self.saved()['engine']
        self.capture.rename(self.root / 'original-capture')
        with capture_writer(self.capture, config=CAPTURE_CONFIG, now=self.clock()) as writer:
            writer.apply(fixture(0, 50, self.clock()), at=self.clock())
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['last_error'], 'source_identity_changed')
        self.assertEqual(self.saved()['engine'], before)

    async def test_post_audit_source_refusal_preserves_signal_exit_code(self):
        await self.start()
        original = self.module._verify
        signal_number = [0]
        async def interrupted(owner, snapshot, stop):
            result = await original(owner, snapshot, stop)
            self.capture.rename(self.root / 'moved-after-audit')
            signal_number[0] = 15
            return result
        with patch.object(self.module, '_verify', interrupted):
            result = await self.resume(stop=lambda: signal_number[0])
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(self.saved()['control']['phase'], 'blocked')
        self.assertEqual(self.saved()['control']['last_error'], 'source_identity_changed')
        self.assertEqual(self.saved()['engine']['next_index'], 0)

    async def test_lost_cursor_overlap_blocks_instead_of_jumping_to_recent_bars(self):
        await self.start()
        self.append(199)
        self.append(2)
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['last_error'], 'retention_lost')
        self.assertEqual(self.saved()['engine']['next_index'], 0)

    async def test_read_failure_waits_without_source_repair_or_economic_writes(self):
        await self.start()
        before = self.saved()['engine']
        from trading_bot.market_capture_store import CaptureUnavailable
        with patch.object(self.module, 'read_capture', side_effect=CaptureUnavailable('private')):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine'], before)
        self.assertEqual(self.saved()['control']['last_error'], 'source_unavailable')

    async def test_fingerprint_mismatch_refuses_resume_without_control_mutation(self):
        await self.start()
        before = (self.account / 'stream.sqlite3').read_bytes()
        with patch.object(self.module, '_engine_fingerprint', return_value='f' * 64):
            with self.assertRaises(ValueError):
                await self.resume()
        self.assertEqual((self.account / 'stream.sqlite3').read_bytes(), before)

    async def test_precommit_stop_discards_prepared_bar_and_restores_pending(self):
        await self.start()
        self.append(2)
        before = self.saved()['engine']
        real = self.module.advance_state
        signal_number = [0]
        async def interrupted(state, candle):
            result = await real(state, candle)
            signal_number[0] = 15
            return result
        with patch.object(self.module, 'advance_state', interrupted):
            result = await self.resume(stop=lambda: signal_number[0])
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual(self.saved()['engine'], before)

    async def test_clock_regression_preserves_prefix_and_reports_clock_error(self):
        await self.start()
        self.append()
        before = self.saved()['engine']
        real = self.module.advance_state
        async def backwards(state, candle):
            value = await real(state, candle)
            self.clock.now -= timedelta(seconds=1)
            return value
        with patch.object(self.module, 'advance_state', backwards):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['status'], 'clock_error')
        self.assertEqual(self.saved()['engine'], before)

    async def test_clock_regression_is_a_durable_refusal_on_later_resume(self):
        await self.start()
        self.append()
        real = self.module.advance_state
        async def backwards(state, candle):
            value = await real(state, candle)
            self.clock.now -= timedelta(seconds=1)
            return value
        with patch.object(self.module, 'advance_state', backwards):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['phase'], 'blocked')
        self.assertEqual(self.saved()['control']['last_error'], 'clock_regression')
        frozen = self.saved()
        self.clock.now += timedelta(seconds=1)
        self.assertEqual((await self.resume())['exit_code'], 3)
        self.assertEqual(self.saved(), frozen)

    async def test_clock_regression_cannot_erase_a_received_source_identity_refusal(self):
        await self.start()
        self.append(2)
        real = self.module.advance_state
        moved = self.root / 'moved-source'
        async def changed(state, candle):
            value = await real(state, candle)
            self.capture.rename(moved)
            self.clock.now -= timedelta(seconds=1)
            return value
        with patch.object(self.module, 'advance_state', changed):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(result['observation']['status'], 'clock_error')
        frozen = self.saved()
        self.assertEqual(frozen['control']['phase'], 'blocked')
        self.assertEqual(frozen['control']['last_error'], 'source_identity_changed')
        self.assertEqual(frozen['engine']['next_index'], 0)
        self.assertEqual(frozen['control']['updated_at'],
                         (self.clock() + timedelta(seconds=1)).isoformat())
        moved.rename(self.capture)
        self.clock.now += timedelta(seconds=1)
        self.assertEqual((await self.resume())['exit_code'], 3)
        self.assertEqual(self.saved(), frozen)

    async def test_out_of_range_clock_does_not_erase_a_received_refusal(self):
        await self.start()
        self.append()
        real = self.module.advance_state
        moved = self.root / 'moved-source'
        async def changed(state, candle):
            value = await real(state, candle)
            self.capture.rename(moved)
            self.clock.now = datetime(1969, 1, 1, tzinfo=timezone.utc)
            return value
        with patch.object(self.module, 'advance_state', changed):
            try:
                result = await self.resume()
            except ValueError:
                result = None
        saved = self.saved()
        self.assertEqual(saved['control']['phase'], 'blocked')
        self.assertEqual(saved['control']['last_error'], 'source_identity_changed')
        self.assertEqual(saved['engine']['next_index'], 0)
        self.assertIsNotNone(result)
        self.assertEqual(result['exit_code'], 3)
        self.assertIsNone(result['observation'])

    async def test_invalid_options_and_stale_bootstrap_do_not_create_account(self):
        for kwargs in ({'max_polls': True}, {'poll_seconds': 4}, {'config_options': {'mode': 'live'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.module.run_stream(self.account, capture=self.capture, clock=self.clock, **kwargs)
            self.assertFalse(self.account.exists())
        self.clock.now += timedelta(minutes=5)
        with self.assertRaises(ValueError):
            await self.start()
        self.assertFalse(self.account.exists())

    async def test_account_must_be_outside_capture_directory(self):
        target = self.capture / 'account'
        with self.assertRaises(ValueError):
            await self.module.run_stream(target, capture=self.capture,
                config_options=OPTIONS, max_polls=1, clock=self.clock)
        self.assertFalse(target.exists())

    async def test_same_revision_changed_snapshot_blocks_before_new_fill(self):
        await self.start()
        self.append()
        await self.resume()
        before = self.saved()['engine']
        original = self.module.read_capture
        def changed(path):
            value = original(path)
            value['fixture']['candles'][0][4] = '100.5'
            value['fixture']['data_provenance']['candles_sha256'] = candles_hash(value['fixture']['candles'])
            return value
        with patch.object(self.module, 'read_capture', changed):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['last_error'], 'source_history_conflict')
        self.assertEqual(self.saved()['engine'], before)

    async def test_new_revision_rewriting_old_overlap_blocks_entire_suffix(self):
        await self.start()
        self.append(2)
        original = self.module.read_capture
        def changed(path):
            value = original(path)
            value['fixture']['candles'][0][4] = '100.5'
            value['fixture']['data_provenance']['candles_sha256'] = candles_hash(value['fixture']['candles'])
            return value
        with patch.object(self.module, 'read_capture', changed):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine']['next_index'], 0)
        self.assertEqual(self.saved()['engine']['fill_count'], 0)

    async def test_regressed_source_revision_preserves_engine(self):
        await self.start()
        old_capture = (self.capture / 'capture.sqlite3').read_bytes()
        self.append()
        await self.resume()
        before = self.saved()['engine']
        (self.capture / 'capture.sqlite3').write_bytes(old_capture)
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['last_error'], 'source_regression')
        self.assertEqual(self.saved()['engine'], before)

    async def test_source_replaced_during_candidate_computation_blocks_commit(self):
        await self.start()
        self.append()
        before = self.saved()['engine']
        real = self.module.advance_state
        async def changed(state, candle):
            candidate = await real(state, candle)
            self.capture.rename(self.root / 'moved-source')
            return candidate
        with patch.object(self.module, 'advance_state', changed):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine'], before)
        self.assertEqual(self.saved()['control']['last_error'], 'source_identity_changed')

    async def test_source_ages_out_during_candidate_computation(self):
        await self.start()
        self.append()
        before = self.saved()['engine']
        real = self.module.advance_state
        async def slow(state, candle):
            candidate = await real(state, candle)
            self.clock.now += timedelta(minutes=3)
            return candidate
        with patch.object(self.module, 'advance_state', slow):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['engine'], before)
        self.assertEqual(self.saved()['control']['last_error'], 'source_not_current')

    async def test_postcommit_signal_preserves_complete_bar_and_audits(self):
        await self.start()
        self.append(2)
        from trading_bot.paper_stream_store import _Writer
        real = _Writer.commit_bar
        signal_number = [0]
        def committed(owner, *args, **kwargs):
            result = real(owner, *args, **kwargs)
            signal_number[0] = 2
            return result
        with patch.object(_Writer, 'commit_bar', committed):
            result = await self.resume(stop=lambda: signal_number[0])
        self.assertEqual(result['exit_code'], 130)
        self.assertEqual(self.saved()['engine']['next_index'], 1)
        self.assertEqual((await self.module.audit_stream(self.account))['events_verified'], 1)
        await self.resume()
        self.assertEqual(self.saved()['engine']['fill_count'], 1)

    async def test_commit_acknowledgement_loss_is_recovered_without_duplicate(self):
        await self.start()
        self.append()
        await self.resume()
        self.append()
        from trading_bot.paper_stream_store import _Writer, StreamStorageError
        real = _Writer.commit_bar
        def lost(owner, *args, **kwargs):
            real(owner, *args, **kwargs)
            raise StreamStorageError('unconfirmed')
        with patch.object(_Writer, 'commit_bar', lost):
            with self.assertRaises(StreamStorageError):
                await self.resume()
        committed = self.saved()['engine']
        self.assertEqual(committed['fill_count'], 1)
        await self.resume()
        self.assertEqual(self.saved()['engine'], committed)
        self.assertEqual((await self.module.audit_stream(self.account))['events_verified'], 2)

    async def test_audit_cancellation_does_not_change_any_account_bytes(self):
        await self.start()
        self.append(2)
        await self.resume()
        before = (self.account / 'stream.sqlite3').read_bytes()
        result = await self.module.audit_stream(self.account, stop=lambda: 15)
        self.assertEqual(result['exit_code'], 143)
        self.assertEqual((self.account / 'stream.sqlite3').read_bytes(), before)

    async def test_continuous_loop_consumes_new_capture_instead_of_resetting(self):
        updated = [False]
        def advance(_):
            if not updated[0]:
                self.append(2)
                updated[0] = True
        result = await self.module.run_stream(self.account, capture=self.capture,
            config_options=OPTIONS, max_polls=2, clock=self.clock, sleep=advance)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(self.saved()['engine']['next_index'], 2)
        self.assertEqual(self.saved()['engine']['fill_count'], 1)

    async def test_archive_capacity_stops_before_extra_effect_and_keeps_auditable_prefix(self):
        await self.start()
        self.append(3)
        from trading_bot import paper_stream_store as store
        with patch.object(store, 'MAX_BARS', 2):
            result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved()['control']['last_error'], 'capacity')
        self.assertEqual(self.saved()['engine']['next_index'], 2)
        self.assertEqual(self.saved()['engine']['fill_count'], 1)
        self.assertEqual((await self.module.audit_stream(self.account))['events_verified'], 2)
        before = self.saved()
        await self.resume()
        self.assertEqual(self.saved(), before)

    async def test_terminal_reason_in_saved_stopped_phase_is_never_cleared(self):
        await self.start()
        from trading_bot.paper_stream_store import canonical_json, digest
        with sqlite3.connect(self.account / 'stream.sqlite3') as connection:
            head = json.loads(connection.execute('SELECT body FROM stream_head').fetchone()[0])
            head['control'].update(phase='stopped', last_error='retention_lost')
            connection.execute('UPDATE stream_head SET body=?,sha256=?',
                (canonical_json(head), digest(head)))
        before = self.saved()
        result = await self.resume()
        self.assertEqual(result['exit_code'], 3)
        self.assertEqual(self.saved(), before)

    def forge_last_state(self, mutate):
        from trading_bot.paper_stream_store import canonical_json, digest
        with sqlite3.connect(self.account / 'stream.sqlite3') as connection:
            head = json.loads(connection.execute('SELECT body FROM stream_head').fetchone()[0])
            mutate(head['engine'])
            head['engine_sha256'] = digest(head['engine'])
            event = json.loads(connection.execute(
                'SELECT body FROM stream_events ORDER BY sequence DESC LIMIT 1').fetchone()[0])
            event['engine_sha256'] = head['engine_sha256']
            event['sha256'] = digest({key: value for key, value in event.items() if key != 'sha256'})
            head['event_head_sha256'] = event['sha256']
            connection.execute('UPDATE stream_events SET body=?,sha256=? WHERE sequence=?',
                (canonical_json(event), event['sha256'], event['sequence']))
            connection.execute('UPDATE stream_head SET body=?,sha256=?',
                (canonical_json(head), digest(head)))

    async def test_digest_consistent_forged_cash_fails_authoritative_audit_and_resume(self):
        await self.start()
        self.append(2)
        await self.resume()
        def mint(engine):
            for name in ('cash', 'equity', 'peak_equity'):
                engine[name] = str(Decimal(engine[name]) + 100)
        self.forge_last_state(mint)
        self.assertGreater(Decimal(self.saved()['engine']['cash']), 100)
        before = (self.account / 'stream.sqlite3').read_bytes()
        with self.assertRaisesRegex(ValueError, 'audit_mismatch'):
            await self.module.audit_stream(self.account)
        with self.assertRaisesRegex(ValueError, 'audit_mismatch'):
            await self.resume()
        self.assertEqual((self.account / 'stream.sqlite3').read_bytes(), before)

    async def test_digest_consistent_forged_risk_and_strategy_fail_full_replay(self):
        for name in ('risk', 'strategy', 'pending'):
            self.account = self.root / ('account-' + name)
            await self.start()
            self.append(2)
            await self.resume()
            def change(engine):
                if name == 'risk':
                    engine['drawdown_halted'] = not engine['drawdown_halted']
                elif name == 'strategy':
                    value = engine['strategy_state']['prev_fast_above_slow']
                    engine['strategy_state']['prev_fast_above_slow'] = not value
                else:
                    previous = engine['pending_signal']
                    action = 'sell' if previous and previous['action'] == 'buy' else 'buy'
                    engine['pending_signal'] = {'action': action, 'source_index': engine['next_index'] - 1}
            self.forge_last_state(change)
            self.saved()  # Structural consistency is deliberately insufficient.
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'audit_mismatch'):
                await self.module.audit_stream(self.account)


if __name__ == '__main__':
    unittest.main()
