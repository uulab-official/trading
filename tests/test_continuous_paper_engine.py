"""Bounded incremental economics against the unchanged original paper engine."""
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))

from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_data import Candle

START = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
MINUTE = 60_000


def config(**options):
    values = dict(symbol='BTC/KRW', timeframe='1m', initial_balance=Decimal('1000'),
                  position_size_pct=Decimal('50'), fee_pct=Decimal('0'),
                  slippage_pct=Decimal('0'), max_daily_loss_pct=Decimal('99'),
                  max_drawdown_pct=Decimal('99'))
    values.update(options)
    return PaperRunConfig(**values)


def bars(prices, *, start=START):
    result = []
    for index, value in enumerate(prices):
        opening, close = value if isinstance(value, tuple) else (value, value)
        result.append([start + index * MINUTE, str(opening), str(max(opening, close)),
                       str(min(opening, close)), str(close), '10'])
    return result


class ContinuousPaperEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        try:
            self.engine = importlib.import_module('trading_bot.paper_stream_engine')
        except ModuleNotFoundError:
            self.engine = None
        self.assertIsNotNone(self.engine, 'missing incremental paper engine')
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    async def advance_all(self, settings, bootstrap, future):
        state = self.engine.initial_state(settings, bootstrap)
        fills = []
        for row in future:
            state, fill = await self.engine.advance_state(state, row)
            if fill is not None:
                fills.append(fill)
        return state, fills

    async def test_bootstrap_discards_even_the_latest_generated_signal(self):
        bootstrap = bars([100] * 49 + [200])
        state = self.engine.initial_state(config(), bootstrap)
        self.assertEqual((state['cash'], state['holding'], state['total_fees']), ('1000', '0', '0'))
        self.assertEqual(state['next_index'], 0)
        self.assertEqual(state['fill_count'], 0)
        self.assertIsNone(state['pending_signal'])
        self.assertIsNone(state['risk_day'])
        self.assertTrue(state['strategy_state']['prev_fast_above_slow'])
        future = bars([200], start=bootstrap[-1][0] + MINUTE)
        after, fill = await self.engine.advance_state(state, future[0])
        self.assertIsNone(fill, 'a bootstrap BUY must never execute after activation')
        self.assertIsNone(after['pending_signal'])
        self.assertEqual(after['cash'], '1000')

    async def test_post_activation_signal_fills_only_the_following_open(self):
        bootstrap = bars([100] * 50)
        state = self.engine.initial_state(config(), bootstrap)
        future = bars([(105, 110), (200, 220)], start=bootstrap[-1][0] + MINUTE)
        signalled, fill = await self.engine.advance_state(state, future[0])
        self.assertIsNone(fill)
        self.assertEqual(signalled['pending_signal'], {'action': 'buy', 'source_index': 0})
        self.assertEqual(signalled['cash'], '1000')
        final, fill = await self.engine.advance_state(signalled, future[1])
        self.assertEqual(fill['timestamp'], future[1][0])
        self.assertEqual(fill['average'], '200')
        self.assertEqual(fill['amount'], '2.50000000')
        self.assertEqual(fill['clientOrderId'], 'fixture-0-buy')
        self.assertEqual(final['cash'], '500.00000000')
        self.assertEqual(final['holding'], '2.50000000')
        self.assertEqual(Decimal(final['equity']), Decimal('1050'))
        self.assertEqual(final['fill_count'], 1)

    async def test_original_ma_and_rsi_parity_after_more_than_650_future_bars(self):
        bootstrap = bars([180 - index for index in range(80)])
        prices = [100 + (index % 64 if index % 64 <= 32 else 64 - index % 64) * 3
                  for index in range(720)]
        future = bars([(price + (5 if index % 3 else -3), price)
                       for index, price in enumerate(prices)], start=bootstrap[-1][0] + MINUTE)
        for kind in ('ma_cross', 'rsi'):
            with self.subTest(strategy=kind):
                settings = config(strategy=kind, fee_pct=Decimal('0.1'),
                                  slippage_pct=Decimal('0.05'))
                warmed = PaperRunner(settings)._strategy()
                with localcontext(PAPER_CALCULATION_CONTEXT):
                    for row in bootstrap:
                        warmed.on_candle(Candle(*row))
                    expected = await PaperRunner(settings, strategy_factory=lambda: warmed)._run(
                        future, return_state=True)
                state = self.engine.initial_state(settings, bootstrap)
                fills = []
                for index, row in enumerate(future):
                    state, fill = await self.engine.advance_state(state, row)
                    if fill is not None:
                        fills.append(fill)
                    if index % 127 == 0:
                        state = json.loads(json.dumps(state))
                    self.assertLessEqual(len(state['candles']), 500)
                self.assertGreaterEqual(len(fills), 10)
                self.assertEqual(fills, expected['exchange']['trades'])
                for name in ('next_index', 'pending_signal', 'risk_day', 'daily_start_equity',
                             'peak_equity', 'daily_loss_halted', 'drawdown_halted'):
                    self.assertEqual(state[name], expected[name], name)
                self.assertEqual(state['cash'], expected['exchange']['balances']['KRW'])
                self.assertEqual(state['holding'], expected['exchange']['balances']['BTC'])
                self.assertEqual(state['total_fees'], expected['exchange']['total_fees'])
                self.assertEqual(state['fill_count'], len(fills))
                self.assertEqual(state['candles'], future[-500:])
                if kind == 'ma_cross':
                    self.assertEqual(state['strategy_state']['prev_fast_above_slow'],
                                     warmed._prev_fast_above_slow)
                    self.assertEqual(state['strategy_state']['consecutive_bars'],
                                     warmed._consecutive_bars)
                else:
                    self.assertEqual(state['strategy_state']['prev_rsi'], str(warmed._prev_rsi))
                self.assertLess(len(json.dumps(state)), 100_000)

    async def test_daily_halt_blocks_new_buys_but_allows_the_exit(self):
        bootstrap = bars([100] * 50)
        future = bars([(100, 110), (110, 50), 50, 200, 200, 200],
                      start=bootstrap[-1][0] + MINUTE)
        state, fills = await self.advance_all(config(max_daily_loss_pct=Decimal('3')), bootstrap, future)
        self.assertEqual([fill['side'] for fill in fills], ['buy', 'sell'])
        self.assertTrue(state['daily_loss_halted'])
        self.assertFalse(state['drawdown_halted'])
        self.assertEqual(Decimal(state['holding']), 0)
        self.assertEqual(state['fill_count'], 2)
        self.assertEqual(fills[1]['amount'], fills[0]['amount'])

    async def test_utc_day_resets_daily_baseline_at_open_and_permits_new_buy(self):
        bootstrap = bars([100] * 50, start=START + (23 * 60 + 8) * MINUTE)
        future = bars([(100, 110), (110, 50), (60, 50), 200, 200, 200],
                      start=bootstrap[-1][0] + MINUTE)
        state = self.engine.initial_state(config(max_daily_loss_pct=Decimal('3')), bootstrap)
        for row in future[:2]:
            state, _ = await self.engine.advance_state(state, row)
        self.assertTrue(state['daily_loss_halted'])
        opening_equity = Decimal(state['cash']) + Decimal(state['holding']) * 60
        state, exit_fill = await self.engine.advance_state(state, future[2])
        self.assertEqual(exit_fill['side'], 'sell')
        self.assertEqual(state['risk_day'], '2026-10-02')
        self.assertEqual(Decimal(state['daily_start_equity']), opening_equity)
        self.assertFalse(state['daily_loss_halted'])
        fills = []
        for row in future[3:]:
            state, fill = await self.engine.advance_state(state, row)
            if fill:
                fills.append(fill)
        self.assertEqual([fill['side'] for fill in fills], ['buy'])

    async def test_drawdown_halt_survives_midnight_and_json_restart(self):
        bootstrap = bars([100] * 50, start=START + (23 * 60 + 8) * MINUTE)
        future = bars([(100, 110), (110, 50), 50, 200, 200, 200],
                      start=bootstrap[-1][0] + MINUTE)
        state = self.engine.initial_state(config(max_daily_loss_pct=Decimal('3'),
                                                  max_drawdown_pct=Decimal('10')), bootstrap)
        fills = []
        for index, row in enumerate(future):
            state, fill = await self.engine.advance_state(state, row)
            if fill is not None:
                fills.append(fill)
            if index == 1:
                self.assertTrue(state['drawdown_halted'])
                state = json.loads(json.dumps(state))
        self.assertEqual([fill['side'] for fill in fills], ['buy', 'sell'])
        self.assertTrue(state['drawdown_halted'])
        self.assertFalse(state['daily_loss_halted'])
        self.assertEqual(state['peak_equity'], '1000')

    async def test_transition_and_validation_never_mutate_the_input(self):
        state = self.engine.initial_state(config(), bars([100] * 50))
        before = copy.deepcopy(state)
        detached = self.engine.validate_state(state)
        detached['candles'][0][1] = '99'
        self.assertEqual(state, before)
        advanced, _ = await self.engine.advance_state(state, bars([110], start=state['last_open_ms'] + MINUTE)[0])
        self.assertEqual(state, before)
        advanced['candles'][0][1] = '98'
        self.assertEqual(state, before)

    async def test_bad_or_duplicate_candles_fail_before_any_state_change(self):
        state = self.engine.initial_state(config(), bars([100] * 50))
        good = bars([110], start=state['last_open_ms'] + MINUTE)[0]
        bad_rows = [list(good) for _ in range(12)]
        bad_rows[0][0] -= MINUTE
        bad_rows[1][0] += MINUTE
        bad_rows[2][0] = True
        bad_rows[3][0] += 1
        bad_rows[4][1] = 'NaN'
        bad_rows[5][2] = '1'
        bad_rows[6][5] = '-1'
        bad_rows[7][1] = '1E+18'
        bad_rows[8][1] = '110.0'
        bad_rows[9][1] = '0.0000000000000000001'
        bad_rows[10][5] = '1000000000000000001'
        bad_rows[11][1] = 110
        before = copy.deepcopy(state)
        for row in bad_rows + [tuple(good), good + ['extra']]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                await self.engine.advance_state(state, row)
            self.assertEqual(state, before)

    async def test_pending_source_and_restored_strategy_values_fail_closed(self):
        state = self.engine.initial_state(config(), bars([100] * 50))
        row = bars([110], start=state['last_open_ms'] + MINUTE)[0]
        state, _ = await self.engine.advance_state(state, row)
        for pending in ({'action': 'buy', 'source_index': 1},
                        {'action': 'buy', 'source_index': False},
                        {'action': 'hold', 'source_index': 0},
                        {'action': 'sell', 'source_index': 0, 'extra': True}):
            candidate = dict(copy.deepcopy(state), pending_signal=pending)
            before = copy.deepcopy(candidate)
            with self.subTest(pending=pending), self.assertRaises(ValueError):
                await self.engine.advance_state(candidate, bars([120], start=row[0] + MINUTE)[0])
            self.assertEqual(candidate, before)
        rsi = self.engine.initial_state(config(strategy='rsi'), bars([100] * 50))
        for saved in ({'kind': 'ma_cross', 'prev_rsi': '50'},
                      {'kind': 'rsi', 'prev_rsi': 'NaN'},
                      {'kind': 'rsi', 'prev_rsi': '101'},
                      {'kind': 'rsi', 'prev_rsi': '-0'},
                      {'kind': 'rsi', 'prev_rsi': '50', 'extra': True}):
            candidate = dict(copy.deepcopy(rsi), strategy_state=saved)
            with self.subTest(strategy=saved), self.assertRaises(ValueError):
                self.engine.validate_state(candidate)

    async def test_original_quantity_precision_failure_preserves_saved_state(self):
        tiny = '0.000000000000000001'
        bootstrap = bars([tiny] * 50)
        state = self.engine.initial_state(config(initial_balance=Decimal('1e18')), bootstrap)
        future = bars(['0.000000000000000002', '0.000000000000000003'],
                      start=bootstrap[-1][0] + MINUTE)
        state, fill = await self.engine.advance_state(state, future[0])
        self.assertIsNone(fill)
        self.assertEqual(state['pending_signal']['action'], 'buy')
        before = copy.deepcopy(state)
        # Original 28-digit accounting cannot quantize this enormous quantity
        # to eight fractional places; the stream rejects instead of rounding it.
        with self.assertRaises(ValueError):
            await self.engine.advance_state(state, future[1])
        self.assertEqual(state, before)

    def test_bootstrap_requires_complete_contiguous_strategy_history(self):
        for settings, count in [(config(), 49), (config(), 501),
                                (config(slow_period=80), 79),
                                (config(strategy='rsi', rsi_period=80), 80)]:
            with self.subTest(strategy=settings.strategy, count=count), self.assertRaises(ValueError):
                self.engine.initial_state(settings, bars([100] * count))
        for settings in (config(slow_period=500), config(strategy='rsi', rsi_period=499)):
            state = self.engine.initial_state(settings, bars([100] * 500))
            self.assertEqual(len(state['candles']), 500)
            self.assertIsNone(state['pending_signal'])
        missing = bars([100] * 51)
        del missing[25]
        with self.assertRaises(ValueError):
            self.engine.initial_state(config(), missing)

    def test_configuration_refuses_non_krw_or_unbounded_decimal_inputs(self):
        for settings in (config(symbol='BTC/USDT'), config(symbol='btc/KRW'),
                         config(initial_balance=Decimal('1e19')),
                         config(fee_pct=Decimal('1e-19')),
                         config(fee_pct=Decimal('0e19'))):
            with self.subTest(settings=settings.identity()), self.assertRaises(ValueError):
                self.engine.initial_state(settings, bars([100] * 50))

    def test_malformed_state_is_rejected_without_canonicalization_or_mutation(self):
        state = self.engine.initial_state(config(), bars([100] * 50))
        changes = [dict(next_index=True), dict(schema_version=True), dict(fill_count=1),
                   dict(bootstrap_count=49), dict(last_open_ms=state['last_open_ms'] + MINUTE),
                   dict(cash='NaN'), dict(cash='1e100'), dict(cash='1E+101'),
                   dict(cash='1E-101'), dict(cash='0.' + '0' * 100 + '1'),
                   dict(holding='-1'), dict(equity='1001'), dict(total_fees='1'),
                   dict(total_fees='0E+101'),
                   dict(daily_loss_halted=1), dict(drawdown_halted='false'),
                   dict(risk_day='2026-10-01'), dict(pending_signal={'action': 'buy', 'source_index': -1}),
                   dict(strategy_state={'kind': 'ma_cross', 'prev_fast_above_slow': 1,
                                        'consecutive_bars': 0}),
                   dict(strategy_state={'kind': 'ma_cross', 'prev_fast_above_slow': False,
                                        'consecutive_bars': 100_501}),
                   dict(extra='unexpected'), dict(candles=state['candles'][:-1])]
        for change in changes:
            candidate = dict(copy.deepcopy(state), **change)
            before = copy.deepcopy(candidate)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.engine.validate_state(candidate)
            self.assertEqual(candidate, before)
        for key in state:
            candidate = copy.deepcopy(state)
            del candidate[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.engine.validate_state(candidate)

    async def test_fixed_decimal_context_covers_bootstrap_restore_and_transition(self):
        bootstrap = bars([100] * 50)
        future = bars([110, (103, 85), 70, 130, 150, 170], start=bootstrap[-1][0] + MINUTE)
        for strategy in ('ma_cross', 'rsi'):
            settings = config(strategy=strategy, fee_pct=Decimal('0.1'), slippage_pct=Decimal('0.05'))
            expected = await self.advance_all(settings, bootstrap, future)
            with localcontext() as context:
                context.prec = 6
                context.rounding = ROUND_DOWN
                actual = await self.advance_all(settings, bootstrap, future)
            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
