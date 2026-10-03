"""Completed OHLCV input must never create duplicate strategy signals."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
for package in ["data", "strategy"]:
    sys.path.insert(0, str(ROOT / "packages" / package))
from trading_data import Candle
from trading_strategy import Strategy, StrategyConfig, Signal, SignalType


class CountingStrategy(Strategy):
    def analyze(self, candles):
        return Signal(SignalType.BUY, self.symbol, candles.current.close, datetime.now(), confidence=1)


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


if __name__ == "__main__":
    unittest.main()
