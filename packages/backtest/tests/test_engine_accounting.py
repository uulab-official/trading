"""Causal accounting regressions against the existing offline BacktestEngine."""

from datetime import datetime, timezone
from decimal import Decimal, localcontext
import unittest
from unittest.mock import patch

import pandas as pd

from trading_backtest import BacktestConfig, BacktestEngine
from trading_strategy import MACrossStrategy, RSIStrategy, Signal, SignalType, Strategy, StrategyConfig


START = 1704067200000
HOUR = 3600000


def fixture(size=55):
    rows = []
    for index in range(size):
        price = 20 if index < 50 else 40
        rows.append({"timestamp": START + index * HOUR, "open": price,
                     "high": price, "low": price, "close": price, "volume": 2000})
    rows[49].update(open=10, high=20, low=10, close=20)
    if size > 50:
        rows[50].update(open=30, high=40, low=30, close=40)
    return pd.DataFrame(rows)


class ScheduledStrategy(Strategy):
    """A test strategy that still runs through the real completed-candle flow."""

    def __init__(self, schedule=None):
        super().__init__(StrategyConfig(name="causal_fixture", symbol="BTC/USDT", timeframe="1h"))
        self.schedule = schedule or {49: SignalType.BUY}

    def analyze(self, candles):
        candle = candles.current
        index = (candle.timestamp - START) // HOUR
        return Signal(self.schedule.get(index, SignalType.HOLD), self.symbol, candle.close,
                      datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc), confidence=1)


def engine(**overrides):
    settings = dict(initial_balance=Decimal("100"), position_size_pct=Decimal("50"),
                    commission_pct=Decimal("0"), slippage_pct=Decimal("0"))
    settings.update(overrides)
    return BacktestEngine(BacktestConfig(**settings))


class TestEngineAccounting(unittest.TestCase):
    def setUp(self):
        self.no_socket = patch("socket.socket.connect", side_effect=AssertionError("No network"))
        self.no_dns = patch("socket.getaddrinfo", side_effect=AssertionError("No DNS"))
        self.no_socket.start()
        self.no_dns.start()
        self.addCleanup(self.no_socket.stop)
        self.addCleanup(self.no_dns.stop)

    def test_closed_bar_buy_fills_only_at_next_available_open(self):
        result = engine().run(ScheduledStrategy(), fixture())
        trade = result.trades[0]
        self.assertEqual(trade["timestamp"], START + 50 * HOUR)
        self.assertEqual(trade["price"], 30)
        self.assertEqual(trade["amount"], 2.5)
        self.assertEqual(result.equity_curve.iloc[49]["position"], 0)

    def test_sell_fills_next_open_after_position_exists(self):
        data = fixture()
        data.loc[52, ["open", "high", "low", "close"]] = 50
        result = engine().run(ScheduledStrategy({49: SignalType.BUY, 51: SignalType.SELL}), data)
        self.assertEqual([t["timestamp"] for t in result.trades], [START + 50 * HOUR, START + 52 * HOUR])
        self.assertEqual(result.trades[1]["price"], 50)
        self.assertEqual(result.final_balance, Decimal("150"))

    def test_close_signal_exits_at_next_open(self):
        result = engine().run(ScheduledStrategy({49: SignalType.BUY, 51: SignalType.CLOSE}), fixture())
        self.assertEqual([trade["side"] for trade in result.trades], ["buy", "sell"])
        self.assertEqual(result.trades[1]["timestamp"], START + 52 * HOUR)

    def test_invalid_accounting_config_fails_before_a_run(self):
        bad = {"initial_balance": [0, -1, "NaN", "Infinity", True],
               "commission_pct": [-1, 100, "NaN"], "slippage_pct": [-1, 100, "Infinity"],
               "position_size_pct": [0, -1, 101, "NaN"], "max_positions": [0, 1.5, True]}
        for field, values in bad.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        BacktestConfig(**{field: value})

    def test_final_bar_signal_stays_pending_without_future_fill(self):
        runner = engine()
        result = runner.run(ScheduledStrategy(), fixture(50))
        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.final_balance, Decimal("100"))
        self.assertEqual(len(runner.broker.pending_orders), 1)

    def test_final_return_marks_open_holdings_separately_from_cash(self):
        runner = engine()
        result = runner.run(ScheduledStrategy(), fixture())
        self.assertEqual(result.final_balance, Decimal("25"))
        self.assertEqual(result.final_equity, Decimal("125"))
        self.assertEqual(result.total_return_pct, Decimal("25"))
        self.assertEqual(result.equity_curve.iloc[-1]["equity"], 125)
        self.assertEqual(result.to_dict()["final_equity"], 125)

    def test_open_fill_does_not_consult_later_bar_range_or_volume(self):
        regular = fixture()
        adversarial = fixture()
        adversarial.loc[50, ["high", "low", "volume"]] = [10000, 1, 0]
        config = dict(slippage_pct=Decimal("0.2"))
        a = engine(**config).run(ScheduledStrategy(), regular)
        b = engine(**config).run(ScheduledStrategy(), adversarial)
        self.assertEqual(a.trades[0]["timestamp"], START + 50 * HOUR)
        self.assertEqual(a.trades[0]["price"], 30.06)
        self.assertEqual(a.trades[0]["price"], b.trades[0]["price"])
        self.assertEqual(a.trades[0]["amount"], b.trades[0]["amount"])

    def test_repeated_run_resets_engine_and_strategy_state(self):
        runner = engine()
        strategy = ScheduledStrategy()
        first = runner.run(strategy, fixture())
        try:
            second = runner.run(strategy, fixture())
        except ValueError as error:
            self.fail(f"A new offline run reused old candle state: {error}")
        self.assertEqual(len(second.equity_curve), 55)
        self.assertEqual(second.final_balance, first.final_balance)
        self.assertEqual(second.total_trades, first.total_trades)
        self.assertEqual(len(runner.signals), 1)

    def test_repeated_real_ma_runs_reset_crossing_state(self):
        data = fixture()
        for i in range(len(data)):
            data.loc[i, ["open", "high", "low", "close"]] = 100 + i
        strategy = MACrossStrategy(StrategyConfig("repeat_ma", "BTC/USDT", "1h"),
                                   fast_period=3, slow_period=5)
        runner = engine()
        first = runner.run(strategy, data)
        second = runner.run(strategy, data)
        self.assertGreater(first.total_trades, 0)
        self.assertEqual(second.total_trades, first.total_trades)
        self.assertEqual(second.final_equity, first.final_equity)

    def test_repeated_real_rsi_runs_reset_confidence_crossing_state(self):
        data = fixture()
        for i in range(len(data)):
            data.loc[i, ["open", "high", "low", "close"]] = 100 - i
        strategy = RSIStrategy(StrategyConfig("repeat_rsi", "BTC/USDT", "1h", min_confidence=0.85))
        runner = engine()
        first = runner.run(strategy, data)
        second = runner.run(strategy, data)
        self.assertGreater(first.total_trades, 0)
        self.assertEqual(second.total_trades, first.total_trades)
        self.assertEqual(second.final_equity, first.final_equity)

    def test_overlapping_or_duplicate_bars_reject_before_simulated_fills(self):
        for timestamp in [START + 49 * HOUR, START + 49 * HOUR + 1]:
            with self.subTest(timestamp=timestamp):
                data = fixture()
                data.loc[50, "timestamp"] = timestamp
                runner = engine()
                with self.assertRaisesRegex(ValueError, "overlap|chronolog|duplicate|conflicting"):
                    runner.run(ScheduledStrategy(), data)
                self.assertTrue(runner.broker is None or not runner.broker.trades)

    def test_accounting_is_independent_of_ambient_decimal_precision(self):
        regular = engine(commission_pct=Decimal("0.1"), slippage_pct=Decimal("0.05"))
        expected = regular.run(ScheduledStrategy(), fixture())
        with localcontext() as context:
            context.prec = 6
            actual = engine(commission_pct=Decimal("0.1"), slippage_pct=Decimal("0.05")).run(ScheduledStrategy(), fixture())
        self.assertEqual(actual.final_balance, expected.final_balance)
        self.assertEqual(actual.total_return_pct, expected.total_return_pct)


if __name__ == "__main__":
    unittest.main()
