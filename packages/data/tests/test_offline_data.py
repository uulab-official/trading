"""Offline data contracts. These tests never start the bot or contact an exchange."""

import importlib
import importlib.util
import unittest
from decimal import Decimal, localcontext
from unittest.mock import patch


D = Decimal


class OfflineDataTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("trading_data"),
            "The required offline trading_data package is missing",
        )
        self.data = importlib.import_module("trading_data")

    def candle(self, timestamp=0, close="10", **overrides):
        values = dict(
            timestamp=timestamp, open=D("10"), high=D("12"), low=D("8"),
            close=close, volume=D("2"),
        )
        values.update(overrides)
        return self.data.Candle(**values)


class CandleTests(OfflineDataTestCase):
    def test_candle_preserves_decimal_fields_and_millisecond_timestamp(self):
        candle = self.candle(timestamp=1700000000123)
        self.assertEqual(candle.timestamp, 1700000000123)
        for field in ("open", "high", "low", "close", "volume"):
            self.assertIsInstance(getattr(candle, field), Decimal)

    def test_candle_normalizes_numeric_ohlcv_without_binary_float_artifacts(self):
        candle = self.candle(open="10.1", high=12, low=8.0, close=10.1, volume="0")
        self.assertEqual(candle.open, D("10.1"))
        self.assertEqual(candle.close, D("10.1"))
        self.assertEqual(candle.volume, D("0"))

    def test_timestamp_rejects_negative_fractional_boolean_and_string_values(self):
        for timestamp in (-1, 0.5, True, "0", D("1")):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.candle(timestamp=timestamp)

    def test_prices_reject_zero_negative_nonfinite_and_boolean_values(self):
        for field in ("open", "high", "low", "close"):
            for value in ("0", "-1", "NaN", "Infinity", "-Infinity", True, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.candle(**{field: value})

    def test_volume_rejects_negative_nonfinite_and_boolean_values(self):
        for volume in ("-1", "NaN", "Infinity", True, None):
            with self.subTest(volume=volume), self.assertRaises(ValueError):
                self.candle(volume=volume)

    def test_candle_rejects_inconsistent_ohlc_bounds(self):
        for overrides in ({"high": D("9")}, {"low": D("11")}, {"low": D("13")}, {"close": D("13")}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.candle(**overrides)

    def test_candle_is_immutable_after_validation(self):
        from dataclasses import FrozenInstanceError
        candle = self.candle()
        with self.assertRaises(FrozenInstanceError):
            candle.close = D("0")


class CandleBufferTests(OfflineDataTestCase):
    def test_duration_ms_exposes_validated_fixed_timeframe_and_is_read_only(self):
        for timeframe, duration in (("1ms", 1), ("2s", 2000), ("1m", 60000), ("1h", 3600000), ("1d", 86400000), ("1w", 604800000)):
            with self.subTest(timeframe=timeframe):
                buffer = self.data.CandleBuffer(timeframe=timeframe)
                self.assertEqual(getattr(buffer, "duration_ms", None), duration)
                with self.assertRaises(AttributeError):
                    buffer.duration_ms = 1

    def test_empty_buffer_has_no_current_and_empty_histories(self):
        buffer = self.data.CandleBuffer()
        self.assertEqual(buffer.max_size, 500)
        self.assertEqual(buffer.timeframe, "1h")
        self.assertEqual(buffer.count, 0)
        self.assertIsNone(buffer.current)
        for getter in (buffer.get_price_history, buffer.get_all_candles, buffer.get_highs, buffer.get_lows, buffer.get_volumes):
            self.assertEqual(getter(), [])

    def test_buffer_appends_candle_and_six_element_ohlcv_in_order(self):
        buffer = self.data.CandleBuffer()
        first = self.candle(timestamp=1000)
        self.assertTrue(buffer.add_candle(first))
        self.assertTrue(buffer.add_candle([2000, "11", "13", "9", "12", "3"]))
        self.assertEqual(buffer.count, 2)
        self.assertEqual(buffer.current.timestamp, 2000)
        self.assertEqual(buffer.get_price_history(), [D("10"), D("12")])
        self.assertEqual(buffer.get_highs(), [D("12"), D("13")])
        self.assertEqual(buffer.get_lows(), [D("8"), D("9")])
        self.assertEqual(buffer.get_volumes(), [D("2"), D("3")])
        self.assertEqual(buffer.get_all_candles()[0], first)

    def test_exact_retained_replays_are_idempotent(self):
        buffer = self.data.CandleBuffer()
        first, second = self.candle(timestamp=1000), self.candle(timestamp=2000)
        buffer.add_candle(first)
        buffer.add_candle(second)
        self.assertFalse(buffer.add_candle(first))
        self.assertFalse(buffer.add_candle(second))
        self.assertEqual(buffer.count, 2)
        self.assertEqual(buffer.current, second)

    def test_conflicting_replay_and_out_of_order_bars_do_not_mutate_buffer(self):
        buffer = self.data.CandleBuffer()
        buffer.add_candle(self.candle(timestamp=1000))
        buffer.add_candle(self.candle(timestamp=3000))
        expected = buffer.get_all_candles()
        for candle in (self.candle(timestamp=1000, close="11"), self.candle(timestamp=2000)):
            with self.subTest(candle=candle), self.assertRaises(ValueError):
                buffer.add_candle(candle)
            self.assertEqual(buffer.get_all_candles(), expected)

    def test_buffer_evicts_oldest_and_rejects_evicted_replays(self):
        buffer = self.data.CandleBuffer(max_size=2)
        for timestamp in (1000, 2000, 3000):
            buffer.add_candle(self.candle(timestamp=timestamp))
        self.assertEqual([c.timestamp for c in buffer.get_all_candles()], [2000, 3000])
        with self.assertRaises(ValueError):
            buffer.add_candle(self.candle(timestamp=1000))
        self.assertEqual(buffer.count, 2)

    def test_history_lists_are_defensive_copies(self):
        buffer = self.data.CandleBuffer()
        buffer.add_candle(self.candle())
        for getter in (buffer.get_price_history, buffer.get_all_candles, buffer.get_highs, buffer.get_lows, buffer.get_volumes):
            getter().clear()
            self.assertEqual(len(getter()), 1)

    def test_invalid_buffer_sizes_and_nonfixed_timeframes_are_rejected(self):
        for max_size in (0, -1, True, 1.5, "2"):
            with self.subTest(max_size=max_size), self.assertRaises(ValueError):
                self.data.CandleBuffer(max_size=max_size)
        for timeframe in ("", "0h", "-1h", "1M", "nonsense", 60, None):
            with self.subTest(timeframe=timeframe), self.assertRaises(ValueError):
                self.data.CandleBuffer(timeframe=timeframe)

    def test_malformed_ohlcv_inputs_are_rejected(self):
        buffer = self.data.CandleBuffer()
        for value in ([], [0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 0, 7], "candle", {}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                buffer.add_candle(value)
        self.assertEqual(buffer.count, 0)

    def test_tick_aggregation_returns_completed_candle_for_explicit_insertion(self):
        buffer = self.data.CandleBuffer(timeframe="1m")
        self.assertIsNone(buffer.add_tick(timestamp=1000, price=D("10"), volume=D("1")))
        self.assertIsNone(buffer.add_tick(timestamp=2000, price=D("12"), volume=D("2")))
        self.assertIsNone(buffer.add_tick(timestamp=3000, price=D("9"), volume=D("3")))
        self.assertIsNone(buffer.current)
        completed = buffer.add_tick(timestamp=61000, price=D("11"), volume=D("4"))
        self.assertEqual(completed, self.candle(timestamp=0, high=D("12"), low=D("9"), close="9", volume=D("6")))
        self.assertEqual(buffer.count, 0)
        buffer.add_candle(completed)
        self.assertEqual(buffer.current, completed)
        next_completed = buffer.add_tick(timestamp=121000, price=D("15"))
        self.assertEqual(next_completed.timestamp, 60000)
        self.assertEqual(next_completed.close, D("11"))
        self.assertEqual(next_completed.volume, D("4"))

    def test_ticks_skip_empty_intervals_instead_of_inventing_candles(self):
        buffer = self.data.CandleBuffer(timeframe="1h")
        buffer.add_tick(timestamp=0, price=D("10"))
        completed = buffer.add_tick(timestamp=10800000, price=D("12"))
        self.assertEqual(completed.timestamp, 0)
        self.assertEqual(buffer.count, 0)
        self.assertIsNone(buffer.add_tick(timestamp=10800001, price=D("11")))

    def test_tick_replay_is_idempotent_and_conflicting_or_late_ticks_fail(self):
        buffer = self.data.CandleBuffer(timeframe="1m")
        buffer.add_tick(timestamp=2000, price=D("10"), volume=D("1"))
        self.assertIsNone(buffer.add_tick(timestamp=2000, price=D("10"), volume=D("1")))
        for timestamp, price in ((2000, "11"), (1000, "10")):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                buffer.add_tick(timestamp=timestamp, price=D(price), volume=D("1"))
        completed = buffer.add_tick(timestamp=60000, price=D("12"))
        self.assertEqual(completed.volume, D("1"))
        self.assertEqual(completed.close, D("10"))

    def test_tick_does_not_reopen_already_completed_bucket(self):
        buffer = self.data.CandleBuffer(timeframe="1m")
        buffer.add_candle(self.candle(timestamp=60000))
        with self.assertRaises(ValueError):
            buffer.add_tick(timestamp=61000, price=D("10"))

    def test_invalid_tick_cannot_change_pending_candle(self):
        buffer = self.data.CandleBuffer(timeframe="1m")
        buffer.add_tick(timestamp=1000, price=D("10"), volume=D("1"))
        for values in ({"timestamp": -1, "price": D("10")}, {"timestamp": 2000, "price": D("0")}, {"timestamp": 2000, "price": D("10"), "volume": D("-1")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                buffer.add_tick(**values)
        completed = buffer.add_tick(timestamp=60000, price=D("12"))
        self.assertEqual(completed.volume, D("1"))
        self.assertEqual(completed.close, D("10"))

    def test_clear_resets_completed_and_pending_bars(self):
        buffer = self.data.CandleBuffer(timeframe="1m")
        buffer.add_candle(self.candle(timestamp=0))
        buffer.add_tick(timestamp=61000, price=D("11"))
        buffer.clear()
        self.assertEqual(buffer.count, 0)
        self.assertIsNone(buffer.current)
        self.assertIsNone(buffer.add_tick(timestamp=1000, price=D("10")))
        self.assertTrue(buffer.add_candle(self.candle(timestamp=0)))


class IndicatorTests(OfflineDataTestCase):
    def test_submodule_imports_match_top_level_exports(self):
        candle = importlib.import_module("trading_data.candle")
        indicator = importlib.import_module("trading_data.indicator")
        self.assertIs(candle.Candle, self.data.Candle)
        self.assertIs(candle.CandleBuffer, self.data.CandleBuffer)
        for name in ("SMA", "EMA", "RSI", "ATR"):
            self.assertIs(getattr(indicator, name), getattr(self.data, name))

    def test_indicator_period_must_be_a_positive_nonboolean_integer(self):
        for name in ("SMA", "EMA", "RSI", "ATR"):
            for period in (0, -1, True, 1.5, "14"):
                with self.subTest(name=name, period=period), self.assertRaises(ValueError):
                    getattr(self.data, name)(period)
            self.assertEqual(getattr(self.data, name)(3).period, 3)

    def test_sma_uses_latest_period_prices_without_mutating_input(self):
        prices = [D("1"), D("2"), D("4"), D("6")]
        self.assertEqual(self.data.SMA(3).calculate(prices), D("4"))
        self.assertEqual(prices, [D("1"), D("2"), D("4"), D("6")])

    def test_ema_seeds_with_first_period_sma_then_applies_smoothing(self):
        self.assertEqual(self.data.EMA(3).calculate([D("1"), D("2"), D("3"), D("8")]), D("5"))
        self.assertEqual(self.data.EMA(1).calculate([D("1"), D("8")]), D("8"))

    def test_price_indicators_reject_insufficient_or_invalid_inputs(self):
        for name, period, minimum in (("SMA", 3, 3), ("EMA", 3, 3), ("RSI", 3, 4)):
            indicator = getattr(self.data, name)(period)
            for prices in ([], [D("1")] * (minimum - 1), [D("NaN")] + [D("1")] * minimum, [D("0")] * minimum, [True] * minimum):
                with self.subTest(name=name, prices=prices), self.assertRaises(ValueError):
                    indicator.calculate(prices)

    def test_rsi_wilder_seed_and_recursive_average_match_known_small_series(self):
        rsi = self.data.RSI(3)
        self.assertAlmostEqual(rsi.calculate([D("1"), D("2"), D("3"), D("2")]), D("66.66666666666666666666666667"))
        self.assertAlmostEqual(rsi.calculate([D("1"), D("2"), D("3"), D("2"), D("4")]), D("83.33333333333333333333333333"))

    def test_rsi_flat_up_only_and_down_only_have_neutral_or_extreme_values(self):
        rsi = self.data.RSI(3)
        self.assertEqual(rsi.calculate([D("4")] * 8), D("50"))
        self.assertEqual(rsi.calculate([D("1"), D("2"), D("3"), D("4")]), D("100"))
        self.assertEqual(rsi.calculate([D("4"), D("3"), D("2"), D("1")]), D("0"))

    def test_rsi_thresholds_are_decimal_ordered_and_bounded(self):
        rsi = self.data.RSI(3, oversold=20, overbought=80)
        self.assertEqual(rsi.oversold, D("20"))
        self.assertEqual(rsi.overbought, D("80"))
        for oversold, overbought in ((-1, 70), (30, 101), (70, 30), (50, 50), ("NaN", 70), (True, 70)):
            with self.subTest(oversold=oversold, overbought=overbought), self.assertRaises(ValueError):
                self.data.RSI(3, oversold=oversold, overbought=overbought)

    def test_atr_accounts_for_price_gaps_and_wilder_smoothing(self):
        highs = [D("10"), D("13"), D("18")]
        lows = [D("8"), D("11"), D("16")]
        closes = [D("9"), D("12"), D("17")]
        self.assertEqual(self.data.ATR(2).calculate(highs[:2], lows[:2], closes[:2]), D("3"))
        self.assertEqual(self.data.ATR(2).calculate(highs, lows, closes), D("4.5"))
        self.assertEqual(self.data.ATR(1).calculate([D("10"), D("13")], [D("10"), D("13")], [D("10"), D("13")]), D("3"))

    def test_price_histories_reject_scalar_text_and_mapping_inputs(self):
        for name in ("SMA", "EMA", "RSI"):
            for prices in ("1234", b"1234", {1: 10, 2: 20, 3: 30, 4: 40}, None, D("1234")):
                with self.subTest(name=name, prices=prices), self.assertRaises(ValueError):
                    getattr(self.data, name)(3).calculate(prices)
        with self.assertRaises(ValueError):
            self.data.ATR(2).calculate("1234", "1234", "1234")

    def test_atr_rejects_mismatched_insufficient_nonfinite_and_inconsistent_bars(self):
        atr = self.data.ATR(2)
        for highs, lows, closes in (([], [], []), ([12], [8], [10]), ([12, 13], [8], [10, 11]), ([12, "NaN"], [8, 9], [10, 11]), ([7, 13], [8, 9], [10, 11]), ([12, 13], [8, 9], [14, 11]), ([12, 13], [0, 9], [10, 11])):
            with self.subTest(highs=highs, lows=lows, closes=closes), self.assertRaises(ValueError):
                atr.calculate(highs, lows, closes)

    def test_calculations_use_decimal_and_are_independent_of_caller_precision(self):
        prices = [D("1"), D("2"), D("3"), D("2"), D("4")]
        indicators = (self.data.SMA(3), self.data.EMA(3), self.data.RSI(3))
        expected = [indicator.calculate(prices) for indicator in indicators]
        expected_atr = self.data.ATR(3).calculate([D("12")] * 4, [D("9"), D("8"), D("7"), D("8")], [D("10")] * 4)
        with localcontext() as context:
            context.prec = 3
            for indicator, value in zip(indicators, expected):
                self.assertEqual(indicator.calculate(prices), value)
                self.assertIsInstance(value, Decimal)
            self.assertEqual(self.data.ATR(3).calculate([D("12")] * 4, [D("9"), D("8"), D("7"), D("8")], [D("10")] * 4), expected_atr)


class OfflineBoundaryTests(OfflineDataTestCase):
    def test_websocket_stub_fails_before_credentials_or_network_are_used(self):
        with patch("os.getenv", side_effect=AssertionError("credential access forbidden")), patch("socket.socket", side_effect=AssertionError("network forbidden")):
            with self.assertRaisesRegex(RuntimeError, "(?i)live market-data adapter.*unavailable.*offline"):
                self.data.BinanceWebSocket(testnet=True)

    def test_websocket_connect_also_fails_closed(self):
        import asyncio
        websocket = object.__new__(self.data.BinanceWebSocket)
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            with self.assertRaisesRegex(RuntimeError, "(?i)live market-data adapter.*unavailable.*offline"):
                asyncio.run(websocket.connect())


if __name__ == "__main__":
    unittest.main()
