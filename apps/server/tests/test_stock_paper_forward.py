"""Fully offline durable stock strategy cursor, append, and tamper tests."""
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core"))
from tools.stock_paper_forward import replay_forward, audit_forward
from trading_core.stock_paper_ledger import StockPaperLedger


START = 1774417800000 - (1774417800000 % 60000)


def candles(prices):
    return [[START + i * 60000, str(p), str(p), str(p), str(p), "100"]
            for i, p in enumerate(prices)]


def fixture(prices):
    return {"mode": "paper", "interval": "1m", "no_live_orders": True,
            "timestamp_semantics": "source_close_to_utc_open",
            "symbol": "005930", "quote_currency": "KRW",
            "provider": "tossinvest_caller_supplied_json",
            "source": "operator_supplied_response_not_authenticated",
            "candles": candles(prices)}


class DurableStockForwardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "stocks.sqlite"
        self.kwargs = dict(initial_cash="1000", fee_pct="0",
                           allocation_pct="50", fast=2, slow=3)
        self.prices = [10, 9, 8, 20, 21]
        self.fixture = fixture(self.prices)

    def test_restart_same_prefix_no_duplicate_fills(self):
        first = replay_forward(self.fixture, self.path, **self.kwargs)
        self.assertEqual("paper_replayed", first["status"])
        self.assertEqual(1, first["new_simulated_fills"])
        again = replay_forward(self.fixture, self.path, **self.kwargs)
        self.assertEqual(0, again["appended_bars"])
        self.assertEqual(0, again["new_simulated_fills"])
        self.assertEqual(1, again["total_simulated_fills"])
        assert again["live_orders_enabled"] is False

    def test_append_completed_bars_advances_cursor_without_reexecuting_orders(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        longer = fixture(self.prices + [22, 23])
        resumed = replay_forward(longer, self.path, **self.kwargs)
        self.assertEqual(2, resumed["appended_bars"])
        self.assertEqual(0, resumed["new_simulated_fills"])
        self.assertEqual(1, resumed["total_simulated_fills"])

    def test_readonly_audit_checks_cursor_fills_and_valuation(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        original_size = self.path.stat().st_size
        report = audit_forward(self.fixture, self.path, **self.kwargs)
        self.assertEqual("audited_stock_paper", report["status"])
        self.assertFalse(report["live_orders_enabled"])
        self.assertEqual(1, report["simulated_fills"])
        self.assertEqual(original_size, self.path.stat().st_size)

    def test_readonly_audit_rejects_truncated_history_and_modified_fills(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with self.assertRaises(ValueError):
            audit_forward(fixture(self.prices[:-1]), self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        with self.assertRaises(ValueError):
            audit_forward(self.fixture, self.path, **self.kwargs)

    def test_equity_mismatch_rolls_back_new_fills_and_cursor_atomically(self):
        from unittest.mock import patch
        replay_forward(self.fixture, self.path, **self.kwargs)
        appended = fixture(self.prices + [22, 23])
        with sqlite3.connect(self.path) as db:
            before = db.execute(
                "SELECT bars,prefix_sha256 FROM paper_strategy_state WHERE id=1"
            ).fetchone()
            before_fills = db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        original = StockPaperLedger.apply_strategy_replay

        def incorrect_equity(ledger, payload, **kwargs):
            kwargs["expected_equity"] = "999999999"
            return original(ledger, payload, **kwargs)

        with patch.object(StockPaperLedger, "apply_strategy_replay", incorrect_equity):
            with self.assertRaises(ValueError):
                replay_forward(appended, self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            after = db.execute(
                "SELECT bars,prefix_sha256 FROM paper_strategy_state WHERE id=1"
            ).fetchone()
            after_fills = db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(before_fills, after_fills)
        # Recovery after a failed transaction must remain possible.
        recovered = replay_forward(appended, self.path, **self.kwargs)
        self.assertEqual(2, recovered["appended_bars"])

    def test_historical_candle_mutation_refused_before_new_fills(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        edited = fixture(self.prices + [22])
        edited["candles"][1][4] = "10"
        edited["candles"][1][2] = "10"
        with self.assertRaises(ValueError):
            replay_forward(edited, self.path, **self.kwargs)
        self.assertEqual(1, StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="1000",
            fee_pct="0"
        ).report(marks={"005930": "21"})["simulated_fills"])

    def test_manual_order_cannot_contaminate_strategy_ledger(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        ledger = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="1000", fee_pct="0"
        )
        with self.assertRaises(ValueError):
            ledger.execute("manual-1", "005930", "sell", 1, price="21")

    def test_strategy_parameters_changed_after_restart_refused(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **dict(
                self.kwargs, allocation_pct="25"
            ))

    def test_cannot_attach_strategy_to_manual_stock_portfolio(self):
        ledger = StockPaperLedger(self.path, quote_currency="KRW",
                                  initial_cash="1000", fee_pct="0")
        ledger.execute("manual-1", "005930", "buy", 1, price="10")
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **self.kwargs)

    def test_saved_fill_tampering_refuses_resume(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **self.kwargs)

    def test_distinct_currency_cannot_reuse_one_database(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with self.assertRaises(ValueError):
            StockPaperLedger(self.path, quote_currency="USD",
                             initial_cash="1000", fee_pct="0")


if __name__ == "__main__":
    unittest.main()
