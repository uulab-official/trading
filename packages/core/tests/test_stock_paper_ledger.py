"""Durable stock paper ledger: restart, concurrency, tampering, and valuation."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.stock_paper_ledger import StockPaperLedger


class StockPaperLedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "stocks.sqlite"
        self.ledger = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="100000", fee_pct="0.1"
        )

    def test_restart_and_duplicate_do_not_add_second_fill(self):
        buy = self.ledger.execute("buy-1", "005930", "buy", 2, price="10000")
        reopened = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="100000", fee_pct="0.1"
        )
        self.assertEqual(buy, reopened.execute("buy-1", "005930", "buy", 2, price="10000"))
        self.assertEqual(1, reopened.report(marks={"005930": "12000"})["simulated_fills"])
        self.assertEqual("103980.0", str(Decimal(
            reopened.report(marks={"005930": "12000"})["marked_equity"])))

    def test_restarted_sell_keeps_cash_and_holdings_exact(self):
        self.ledger.execute("b1", "005930", "buy", 2, price="10000")
        reopened = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="100000", fee_pct="0.1"
        )
        reopened.execute("s1", "005930", "sell", 2, price="11000")
        result = reopened.report()
        self.assertEqual("101958.0", str(Decimal(result["cash"])))
        self.assertEqual({}, result["holdings"])
        self.assertEqual(2, result["simulated_fills"])

    def test_failed_buy_or_oversell_never_persist_partial_transaction(self):
        with self.assertRaises(ValueError):
            self.ledger.execute("bad", "005930", "buy", 20, price="10000")
        with self.assertRaises(ValueError):
            self.ledger.execute("bad2", "005930", "sell", 1, price="10000")
        self.assertEqual(0, self.ledger.report()["simulated_fills"])

    def test_tampering_in_json_fill_is_rejected(self):
        self.ledger.execute("buy-1", "005930", "buy", 1, price="10000")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE client_id='buy-1'")
        with self.assertRaises(ValueError):
            self.ledger.report(marks={"005930": "12000"})
        with self.assertRaises(ValueError):
            self.ledger.execute("buy-2", "005930", "buy", 1, price="11000")

    def test_configuration_change_on_reopen_is_rejected(self):
        self.ledger.execute("buy-1", "005930", "buy", 1, price="10000")
        for currency, balance, fee in (
            ("USD", "100000", "0.1"),
            ("KRW", "200000", "0.1"),
            ("KRW", "100000", "0.2"),
        ):
            with self.assertRaises(ValueError):
                StockPaperLedger(self.path, quote_currency=currency,
                                 initial_cash=balance, fee_pct=fee)

    def test_two_racing_order_ids_cannot_overspend_shared_cash(self):
        def attempt(client):
            try:
                self.ledger.execute(client, "005930", "buy", 7, price="10000")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("buy-1", "buy-2")))
        self.assertEqual(1, sum(results))
        state = self.ledger.report(marks={"005930": "10000"})
        self.assertEqual(1, state["simulated_fills"])
        self.assertEqual(7, state["holdings"]["005930"])

    def test_missing_mark_for_open_position_fails_closed(self):
        self.ledger.execute("buy-1", "005930", "buy", 1, price="10000")
        with self.assertRaises(ValueError):
            self.ledger.report()


if __name__ == "__main__":
    unittest.main()
