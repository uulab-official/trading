"""Causal stock paper backtest: next-open fills and malformed-bar refusal."""

import sys
import unittest
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_backtest import run_backtest

START = 1774417800000 - (1774417800000 % 60000)


def fixture(prices, opening_override=None):
    rows = []
    for index, close in enumerate(prices):
        opening = (
            close
            if opening_override is None or index not in opening_override
            else opening_override[index]
        )
        rows.append(
            [
                START + index * 60000,
                str(opening),
                str(max(opening, close)),
                str(min(opening, close)),
                str(close),
                "100",
            ]
        )
    return {
        "mode": "paper",
        "interval": "1m",
        "no_live_orders": True,
        "timestamp_semantics": "source_close_to_utc_open",
        "quote_currency": "KRW",
        "symbol": "005930",
        "candles": rows,
    }


class StockPaperBacktestTests(unittest.TestCase):
    def test_buy_fills_only_next_open_not_signal_close(self):
        data = fixture([10, 9, 8, 20, 21], {4: 30})
        result = run_backtest(
            data, initial_cash=1000, fee_pct="0", allocation_pct=50, fast=2, slow=3
        )
        self.assertFalse(result["live_orders_enabled"])
        self.assertEqual(1, len(result["simulated_fills"]))
        fill = result["simulated_fills"][0]
        self.assertEqual("30", fill["price"])
        self.assertEqual(16, fill["shares"])
        self.assertEqual(Decimal("856"), Decimal(result["marked_equity"]))

    def test_repeatable_under_different_caller_decimal_precision(self):
        data = fixture([10, 9, 8, 20, 21, 25, 10, 8, 7, 5])
        reference = run_backtest(data, initial_cash=1000, fee_pct="0.1", fast=2, slow=3)
        with localcontext() as context:
            context.prec = 9
            actual = run_backtest(data, initial_cash=1000, fee_pct="0.1", fast=2, slow=3)
        self.assertEqual(reference, actual)

    def test_rejects_nonpaper_data_or_future_lookahead_shortcuts(self):
        good = fixture([10, 9, 8, 20, 21])
        bad = dict(good, mode="live")
        with self.assertRaises(ValueError):
            run_backtest(bad, initial_cash=1000, fast=2, slow=3)
        bad = dict(good, timestamp_semantics="source_open")
        with self.assertRaises(ValueError):
            run_backtest(bad, initial_cash=1000, fast=2, slow=3)
        bad = fixture([10, 9, 8, 20, 21])
        bad["candles"][4][0] = bad["candles"][3][0]
        with self.assertRaises(ValueError):
            run_backtest(bad, initial_cash=1000, fast=2, slow=3)

    def test_fees_and_allocation_are_bounded(self):
        data = fixture([10, 9, 8, 20, 21])
        with self.assertRaises(ValueError):
            run_backtest(data, initial_cash=1000, fast=2, slow=3, allocation_pct=200)
        with self.assertRaises(ValueError):
            run_backtest(data, initial_cash=1000, fast=2, slow=3, fee_pct=100)


if __name__ == "__main__":
    unittest.main()
