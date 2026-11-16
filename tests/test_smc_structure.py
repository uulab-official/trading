"""Standard-library regression tests for causal SMC detection."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "packages/strategy/smc_structure.py"
spec = importlib.util.spec_from_file_location("smc_structure", path)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Candle = module.Candle
market_structure = module.market_structure


def c(i, high, low, close=None):
    close = (high + low) / 2 if close is None else close
    return Candle(i, close, high, low, close)


class StructureTests(unittest.TestCase):
    def test_swing_is_only_visible_after_right_bars(self):
        bars = [c(1, 8, 3), c(2, 12, 4), c(3, 9, 5), c(4, 8, 6)]
        self.assertFalse(any(e.kind == "swing_high" for e in market_structure(bars[:3], 1, 2)))
        events = market_structure(bars, 1, 2)
        self.assertEqual([(e.timestamp, e.pivot_timestamp) for e in events if e.kind == "swing_high"], [(4, 2)])

    def test_break_once_and_choch_after_downtrend(self):
        bars = [c(1, 9, 5), c(2, 12, 6), c(3, 10, 4),
                c(4, 9, 6), c(5, 8, 3, 3.5), c(6, 13, 3, 12.5)]
        events = market_structure(bars, 1, 1)
        breaks = [(e.kind, e.direction) for e in events if e.kind in ("bos", "choch")]
        self.assertIn(("bos", "down"), breaks)
        self.assertIn(("choch", "up"), breaks)

    def test_prefix_stability(self):
        bars = [c(i, 10 + (i % 4), 3 + (i % 3)) for i in range(1, 20)]
        all_events = market_structure(bars, 2, 2)
        for n in range(1, len(bars) + 1):
            self.assertEqual(market_structure(bars[:n], 2, 2), [e for e in all_events if e.timestamp <= bars[n-1].timestamp])

    def test_reject_invalid_data(self):
        with self.assertRaises(ValueError):
            Candle(1, 5, 4, 6, 5)
        with self.assertRaises(ValueError):
            market_structure([c(1, 5, 2), c(1, 6, 2)])
        with self.assertRaises(ValueError):
            market_structure([], 0, 2)


if __name__ == "__main__":
    unittest.main()
