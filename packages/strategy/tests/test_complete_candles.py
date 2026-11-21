"""Completed OHLCV input must never create duplicate strategy signals."""

import sys
import unittest
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for package in ["data", "strategy"]:
    sys.path.insert(0, str(ROOT / "packages" / package))
from trading_data import Candle
from trading_data.candle import CandleBuffer
from trading_strategy import Signal, SignalType, Strategy, StrategyConfig
from trading_strategy.breakout_strategy import (
    BreakoutStrategy,
    SupportResistanceStrategy,
    VolatilityBreakoutStrategy,
)
from trading_strategy.ma_strategy import MACrossStrategy, MAStrategy, TripleMAStrategy
from trading_strategy.rsi_strategy import RSIDivergenceStrategy, RSIStrategy


class CountingStrategy(Strategy):
    def analyze(self, candles):
        return Signal(
            SignalType.BUY, self.symbol, candles.current.close, datetime.now(), confidence=1
        )


class CompletedCandleTests(unittest.TestCase):
    def test_latest_and_older_retained_replays_do_not_emit_duplicate_signals(self):
        strategy = CountingStrategy(StrategyConfig("test", "BTC/USDT", "1h"))
        candles = [Candle(i * 3600000, 100, 110, 90, 105, 1) for i in range(50)]
        for candle in candles:
            strategy.on_candle(candle)
        self.assertEqual(strategy.total_signals, 1)
        self.assertIsNone(strategy.on_candle(candles[-1]))
        self.assertIsNone(strategy.on_candle(candles[10]))
        self.assertEqual(strategy.total_signals, 1)
        self.assertEqual(strategy.candles.current.high, Decimal("110"))
        self.assertEqual(
            strategy.last_signal.timestamp,
            datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc),
        )
        self.assertEqual(strategy.last_signal.timestamp.tzinfo, timezone.utc)

    def test_direct_analysis_preserves_naive_utc_without_deprecated_clock_calls(self):
        strategies = (
            MAStrategy,
            MACrossStrategy,
            TripleMAStrategy,
            RSIStrategy,
            RSIDivergenceStrategy,
            BreakoutStrategy,
            VolatilityBreakoutStrategy,
            SupportResistanceStrategy,
        )
        populated = CandleBuffer(max_size=500, timeframe="1h")
        for index in range(300):
            price = 100 + index % 20
            populated.add_candle(
                Candle(index * 3_600_000, price, price + 2, price - 2, price + 1, 1)
            )

        for strategy_type in strategies:
            for candles in (CandleBuffer(), populated):
                with self.subTest(strategy=strategy_type.__name__, candles=candles.count):
                    strategy = strategy_type(StrategyConfig("utc", "BTC/USDT", "1h"))
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", DeprecationWarning)
                        before = datetime.now(timezone.utc).replace(tzinfo=None)
                        signal = strategy.analyze(candles)
                        after = datetime.now(timezone.utc).replace(tzinfo=None)
                    self.assertIsNone(signal.timestamp.tzinfo)
                    self.assertLessEqual(before, signal.timestamp)
                    self.assertLessEqual(signal.timestamp, after)
                    self.assertEqual(signal.to_dict()["timestamp"], signal.timestamp.isoformat())


if __name__ == "__main__":
    unittest.main()
