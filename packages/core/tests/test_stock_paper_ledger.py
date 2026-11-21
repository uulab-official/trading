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
        self.assertEqual(Decimal("103980"), Decimal(
            reopened.report(marks={"005930": "12000"})["marked_equity"]))

    def test_restarted_sell_keeps_cash_and_holdings_exact(self):
        self.ledger.execute("b1", "005930", "buy", 2, price="10000")
        reopened = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="100000", fee_pct="0.1"
        )
        reopened.execute("s1", "005930", "sell", 2, price="11000")
        result = reopened.report()
        self.assertEqual(Decimal("101958"), Decimal(result["cash"]))
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

    def test_open_instance_refuses_metadata_mutation_after_initialization(self):
        self.ledger.execute("b1", "005930", "buy", 1, price="10000")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_meta SET value='USD' WHERE key='quote_currency'")
        with self.assertRaises(ValueError):
            self.ledger.execute("b2", "005930", "buy", 1, price="10000")
        with self.assertRaises(ValueError):
            self.ledger.report(marks={"005930": "10000"})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0])

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

    def test_strategy_prefix_replay_preserves_opaque_client_ids_across_restart(self):
        buy = {
            "mode": "paper", "client_id": "request:alpha", "symbol": "005930",
            "side": "buy", "shares": 1, "price": "10000", "gross": "10000",
            "fee": "10.0", "quote_currency": "KRW", "status": "simulated_fill",
        }
        sell = {
            "mode": "paper", "client_id": "request:omega", "symbol": "005930",
            "side": "sell", "shares": 1, "price": "12000", "gross": "12000",
            "fee": "12.0", "quote_currency": "KRW", "status": "simulated_fill",
        }
        first_bar = [0, "10000", "10000", "10000", "10000", "100"]
        next_bar = [60000, "12000", "12000", "12000", "12000", "100"]
        committed_fills = {1: [buy], 2: [buy, sell]}
        identity = {"strategy": "opaque-order-identifiers"}
        first = self.ledger.apply_strategy_replay(
            {"mode": "paper", "candles": [first_bar]}, identity=identity,
            expected_fills=[buy], expected_fills_for_prefix=committed_fills.__getitem__,
            marks={"005930": "10000"}, expected_equity="99990",
        )
        self.assertEqual(1, first["new_simulated_fills"])
        full_fixture = {"mode": "paper", "candles": [first_bar, next_bar]}
        appended = self.ledger.apply_strategy_replay(
            full_fixture, identity=identity, expected_fills=[buy, sell],
            expected_fills_for_prefix=committed_fills.__getitem__,
            marks={"005930": "12000"}, expected_equity="101978",
        )
        self.assertEqual(1, appended["new_simulated_fills"])
        reopened = StockPaperLedger(
            self.path, quote_currency="KRW", initial_cash="100000", fee_pct="0.1"
        )
        repeated = reopened.apply_strategy_replay(
            full_fixture, identity=identity, expected_fills=[buy, sell],
            expected_fills_for_prefix=committed_fills.__getitem__,
            marks={"005930": "12000"}, expected_equity="101978",
        )
        self.assertEqual(0, repeated["new_simulated_fills"])
        self.assertEqual(0, repeated["appended_bars"])
        self.assertEqual(2, repeated["total_simulated_fills"])


if __name__ == "__main__":
    unittest.main()
