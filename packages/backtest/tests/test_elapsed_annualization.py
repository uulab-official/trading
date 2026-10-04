"""Elapsed-time annualization contracts for the existing offline engine."""

from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from io import StringIO
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from trading_backtest import BacktestConfig, BacktestEngine
from trading_strategy import Signal, SignalType, Strategy, StrategyConfig


START = 1704067200000
YEAR_MS = 31557600000  # Explicit fixed 365.25-day year.


class TimedSchedule(Strategy):
    def __init__(self, timeframe="1m", step=60000, schedule=None):
        super().__init__(StrategyConfig("elapsed_fixture", "BTC/USDT", timeframe))
        self.step = step
        self.schedule = schedule or {49: SignalType.BUY, 51: SignalType.SELL}

    def analyze(self, candles):
        candle = candles.current
        index = (candle.timestamp - START) // self.step
        return Signal(self.schedule.get(index, SignalType.HOLD), self.symbol, candle.close,
                      datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc), confidence=1)


def candles(step=60000, size=55, exit_price=100):
    return pd.DataFrame([{"timestamp": START + index * step, "open": 100 if index < 52 else exit_price,
                         "high": 100 if index < 52 else exit_price,
                         "low": 100 if index < 52 else exit_price,
                         "close": 100 if index < 52 else exit_price, "volume": 2000}
                        for index in range(size)])


def run(step=60000, timeframe="1m", data=None, fee="1", slippage="0.5"):
    config = BacktestConfig(initial_balance=Decimal("1000"), position_size_pct=Decimal("10"),
                            commission_pct=Decimal(fee), slippage_pct=Decimal(slippage))
    engine = BacktestEngine(config)  # Config's default 1h must not override strategy timeframe.
    return engine, engine.run(TimedSchedule(timeframe, step), data if data is not None else candles(step))


class TestElapsedAnnualization(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Annualization checks forbid network"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_subday_fees_and_slippage_loss_has_nonzero_annualized_loss(self):
        _, result = run()
        self.assertEqual(result.final_equity, Decimal("997.0000"))
        self.assertEqual(result.total_return_pct, Decimal("-0.3000"))
        expected = math.expm1(math.log(0.997) * YEAR_MS / (55 * 60000)) * 100
        self.assertAlmostEqual(float(result.cagr), expected, places=12)
        self.assertLess(result.cagr, 0)
        self.assertEqual(result.annualized_return, result.cagr)

    def test_fractional_day_is_not_truncated_and_last_close_is_included(self):
        _, result = run(3600000, "1h")
        expected = math.expm1(math.log(0.997) * YEAR_MS / (55 * 3600000)) * 100
        self.assertAlmostEqual(float(result.cagr), expected, places=10)
        metadata = result.to_dict()["annualization"]
        self.assertEqual(metadata["start_timestamp_ms"], START)
        self.assertEqual(metadata["end_timestamp_ms"], START + 55 * 3600000)
        self.assertEqual(metadata["elapsed_ms"], 55 * 3600000)
        self.assertEqual(metadata["year_days"], "365.25")
        self.assertEqual(metadata["return_unit"], "percent")

    def test_elapsed_period_includes_supplied_gaps(self):
        data = candles()
        data.loc[53:, "timestamp"] += 3 * 60000
        _, result = run(data=data)
        expected = math.expm1(math.log(0.997) * YEAR_MS / (58 * 60000)) * 100
        self.assertAlmostEqual(float(result.cagr), expected, places=12)
        self.assertEqual(result.annualization["elapsed_ms"], 58 * 60000)

    def test_more_than_one_year_uses_elapsed_seconds(self):
        _, result = run(10 * 86400000, "1d", fee="0", slippage="0",
                        data=candles(10 * 86400000, exit_price=110))
        elapsed_ms = (54 * 10 + 1) * 86400000  # Last bar spans 1d despite 10d gaps.
        expected = math.expm1(math.log(1.01) * YEAR_MS / elapsed_ms) * 100
        self.assertAlmostEqual(float(result.cagr), expected, places=10)
        self.assertEqual(result.annualization["elapsed_ms"], elapsed_ms)

    def test_open_holdings_annualize_marked_equity_instead_of_cash(self):
        engine = BacktestEngine(BacktestConfig(initial_balance=Decimal("1000"),
                                position_size_pct=Decimal("10"), commission_pct=Decimal("0"),
                                slippage_pct=Decimal("0")))
        strategy = TimedSchedule("1d", 86400000, {49: SignalType.BUY})
        result = engine.run(strategy, candles(86400000, exit_price=110))
        self.assertEqual(result.final_balance, Decimal("900"))
        self.assertEqual(result.final_equity, Decimal("1010"))
        expected = math.expm1(math.log(1.01) * YEAR_MS / (55 * 86400000)) * 100
        self.assertAlmostEqual(float(result.cagr), expected, places=10)

    def annualize(self, initial, final, duration):
        self.assertTrue(hasattr(BacktestEngine, "_annualized_return"),
                        "The result needs a finite/undefined elapsed-time calculation")
        return BacktestEngine._annualized_return(Decimal(initial), Decimal(final), duration)

    def test_exact_one_year_returns_net_growth_percent(self):
        value, reason = self.annualize("1000", "1100", YEAR_MS)
        self.assertEqual(value, Decimal("10"))
        self.assertIsNone(reason)

    def test_zero_equity_is_defined_total_loss(self):
        value, reason = self.annualize("1000", "0", YEAR_MS // 2)
        self.assertEqual(value, Decimal("-100"))
        self.assertIsNone(reason)

    def test_finite_long_period_growth_survives_intermediate_ratio_overflow(self):
        value, reason = self.annualize("1e-900000", "1e900000", YEAR_MS * 1800000)
        self.assertIsNone(reason)
        self.assertAlmostEqual(float(value), 900, places=12)

    def test_finite_long_period_loss_survives_intermediate_ratio_underflow(self):
        value, reason = self.annualize("1e900000", "1e-900000", YEAR_MS * 1800000)
        self.assertIsNone(reason)
        self.assertAlmostEqual(float(value), -90, places=12)

    def test_tiny_two_year_gain_does_not_cancel_to_zero(self):
        value, reason = self.annualize("1", "1.000000000000000000000000001", YEAR_MS * 2)
        self.assertIsNone(reason)
        with localcontext() as context:
            context.prec = 80
            expected = (Decimal("1.000000000000000000000000001").sqrt() - 1) * 100
        self.assertLess(abs(value - expected), Decimal("1e-52"))
        self.assertGreater(value, 0)

    def test_nonzero_subnormal_ratio_does_not_distort_finite_long_period_loss(self):
        initial, final = Decimal("1e500013"), Decimal("6e-500014")
        value, reason = self.annualize(str(initial), str(final), YEAR_MS * 1000000)
        self.assertIsNone(reason)
        with localcontext() as context:
            context.prec = 80
            context.Emax = 2000000
            context.Emin = -2000000
            expected = ((final / initial) ** (Decimal(1) / 1000000) - 1) * 100
        self.assertLess(abs(value - expected), Decimal("1e-24"))

    def test_exact_year_growth_does_not_overflow_intermediate_equity_difference(self):
        value, reason = self.annualize("1e1000000", "2e1000000", YEAR_MS)
        self.assertIsNone(reason)
        self.assertEqual(value, Decimal("100"))

    def test_near_unity_equity_ratio_preserves_exact_year_relative_gain(self):
        value, reason = self.annualize("3", "3.000000000000000000000000001", YEAR_MS)
        self.assertIsNone(reason)
        self.assertEqual(value, Decimal("1e-27") / 3 * 100)

    def test_near_unity_equity_ratio_preserves_two_year_relative_gain(self):
        value, reason = self.annualize("3", "3.000000000000000000000000001", YEAR_MS * 2)
        self.assertIsNone(reason)
        with localcontext() as context:
            context.prec = 80
            expected = ((Decimal("3.000000000000000000000000001") / 3).sqrt() - 1) * 100
        self.assertLess(abs(value - expected), Decimal("1e-52"))
        self.assertGreater(value, 0)

    def test_invalid_durations_and_equities_are_explicitly_undefined(self):
        for initial, final, duration in (("1000", "1000", 0), ("1000", "1000", -1),
                                         ("1000", "-1", YEAR_MS), ("0", "1000", YEAR_MS),
                                         ("-1", "1000", YEAR_MS), ("NaN", "1", YEAR_MS),
                                         ("1", "Infinity", YEAR_MS)):
            with self.subTest(initial=initial, final=final, duration=duration):
                value, reason = self.annualize(initial, final, duration)
                self.assertIsNone(value)
                self.assertTrue(reason)

    def test_short_gain_outside_numeric_range_is_null_with_reason(self):
        _, result = run(data=candles(exit_price=200), fee="0", slippage="0")
        self.assertEqual(result.total_return_pct, Decimal("10"))
        self.assertIsNone(result.cagr)
        self.assertIsNone(result.annualized_return)
        payload = result.to_dict()
        self.assertIsNone(payload["cagr"])
        self.assertIsNone(payload["annualized_return"])
        self.assertEqual(payload["annualization"]["status"], "undefined")
        self.assertIn("range", payload["annualization"]["undefined_reason"])
        json.dumps({key: payload[key] for key in ("cagr", "annualized_return", "annualization")}, allow_nan=False)

    def test_short_period_warns_about_extrapolation_and_not_future_returns(self):
        _, result = run()
        self.assertIn("less than one year", result.annualization["warning"])
        self.assertIn("future returns", result.annualization["warning"])

    def test_repeated_runs_and_external_decimal_context_do_not_change_result(self):
        engine, first = run(3600000, "1h")
        with localcontext() as context:
            context.prec = 6
            second = engine.run(TimedSchedule("1h", 3600000), candles(3600000))
        self.assertEqual(second.cagr, first.cagr)
        self.assertEqual(second.annualization, first.annualization)

    def test_cli_formats_undefined_cagr_and_prints_duration_and_warning(self):
        from trading_backtest.cli import run_backtest
        args = Namespace(symbol="BTC/USDT", strategy="ma_cross", timeframe="1m", data_dir="unused",
                         fast_period=3, slow_period=5, balance=1000, commission=0, slippage=0,
                         position_size=10, output=None, plot=False)
        # The existing real MA strategy crosses on this supplied local shape.
        from test_engine_accounting import fixture
        data = fixture(55)
        data.loc[:, "timestamp"] = START + data.index * 60000
        data.loc[54, ["open", "high", "low", "close"]] = 80
        output = StringIO()
        with patch("trading_backtest.cli.HistoricalDataLoader.load_from_csv", return_value=data), \
             patch("trading_backtest.cli.HistoricalDataLoader.fetch_from_exchange", side_effect=AssertionError("No exchange")), \
             redirect_stdout(output):
            run_backtest(args)
        text = output.getvalue()
        self.assertIn("CAGR:            N/A", text)
        self.assertIn("Elapsed:", text)
        self.assertIn("future returns", text)

    def test_offline_report_includes_exact_annualization_metadata_and_warning(self):
        path = Path(__file__).resolve().parents[3] / "tools/offline_trade_report.py"
        spec = importlib.util.spec_from_file_location("elapsed_report", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload = json.loads(json.dumps(module.build_fixture_report(module.DEFAULT_FIXTURE), allow_nan=False))
        self.assertIn("annualization", payload)
        self.assertIn("cagr_pct", payload)
        self.assertIn("future returns", payload["annualization"]["warning"])


if __name__ == "__main__":
    unittest.main()
