"""Fully offline durable stock strategy cursor, append, and tamper tests."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.stock_paper_ledger import StockPaperLedger

from tools.stock_paper_forward import audit_forward, replay_forward

START = 1774417800000 - (1774417800000 % 60000)


def candles(prices):
    return [
        [START + i * 60000, str(p), str(p), str(p), str(p), "100"] for i, p in enumerate(prices)
    ]


def fixture(prices):
    return {
        "mode": "paper",
        "interval": "1m",
        "no_live_orders": True,
        "timestamp_semantics": "source_close_to_utc_open",
        "symbol": "005930",
        "quote_currency": "KRW",
        "provider": "tossinvest_caller_supplied_json",
        "source": "operator_supplied_response_not_authenticated",
        "candles": candles(prices),
    }


class DurableStockForwardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "stocks.sqlite"
        self.kwargs = dict(initial_cash="1000", fee_pct="0", allocation_pct="50", fast=2, slow=3)
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

    def test_concurrent_append_commits_each_new_fill_once(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        appended = fixture(self.prices + [22, 23, 8, 7])
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(
                pool.map(lambda _: replay_forward(appended, self.path, **self.kwargs), range(2))
            )
        self.assertEqual([0, 1], sorted(row["new_simulated_fills"] for row in reports))
        self.assertEqual([0, 4], sorted(row["appended_bars"] for row in reports))
        audit = audit_forward(appended, self.path, **self.kwargs)
        self.assertEqual(2, audit["simulated_fills"])
        self.assertEqual({}, audit["holdings"])
        self.assertEqual(Decimal("678"), Decimal(audit["cash"]))

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
        appended = fixture(self.prices + [22, 23, 8, 7])
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
        self.assertEqual(4, recovered["appended_bars"])
        self.assertEqual(1, recovered["new_simulated_fills"])
        self.assertEqual(2, recovered["total_simulated_fills"])

    def test_cursor_write_failure_rolls_back_fills_and_preserves_resume(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        appended = fixture(self.prices + [22, 23, 8, 7])
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER fail_cursor_update
                BEFORE UPDATE ON paper_strategy_state BEGIN
                    SELECT RAISE(ABORT, 'injected cursor persistence failure');
                END""")
            before = list(db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            replay_forward(appended, self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before, list(db.iterdump()))
            db.execute("DROP TRIGGER fail_cursor_update")
        recovered = replay_forward(appended, self.path, **self.kwargs)
        self.assertEqual(1, recovered["new_simulated_fills"])
        self.assertEqual(4, recovered["appended_bars"])
        self.assertEqual(
            0, replay_forward(appended, self.path, **self.kwargs)["new_simulated_fills"]
        )

    def test_process_exit_after_cursor_update_rolls_back_entire_replay(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        appended = fixture(self.prices + [22, 23, 8, 7])
        with sqlite3.connect(self.path) as db:
            before = list(db.iterdump())
        child = """
import json
import os
import sqlite3
import sys
sys.path.insert(0, sys.argv[1])
from tools.stock_paper_forward import replay_forward

class ExitAfterCursorUpdate(sqlite3.Connection):
    def execute(self, statement, *args, **kwargs):
        result = super().execute(statement, *args, **kwargs)
        if statement.startswith("UPDATE paper_strategy_state SET"):
            os._exit(73)
        return result

original_connect = sqlite3.connect
def crashing_connect(*args, **kwargs):
    return original_connect(*args, factory=ExitAfterCursorUpdate, **kwargs)
sqlite3.connect = crashing_connect
payload = json.load(sys.stdin)
replay_forward(payload["fixture"], sys.argv[2], **payload["kwargs"])
"""
        process = subprocess.run(
            [sys.executable, "-c", child, str(ROOT), str(self.path)],
            input=json.dumps({"fixture": appended, "kwargs": self.kwargs}),
            text=True,
            capture_output=True,
            timeout=10,
            cwd=ROOT,
        )
        self.assertEqual(73, process.returncode, process.stderr)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before, list(db.iterdump()))
        recovered = replay_forward(appended, self.path, **self.kwargs)
        self.assertEqual(1, recovered["new_simulated_fills"])
        self.assertEqual(4, recovered["appended_bars"])
        self.assertEqual(2, audit_forward(appended, self.path, **self.kwargs)["simulated_fills"])

    def test_historical_candle_mutation_refused_before_new_fills(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        edited = fixture(self.prices + [22])
        edited["candles"][1][4] = "10"
        edited["candles"][1][2] = "10"
        with self.assertRaises(ValueError):
            replay_forward(edited, self.path, **self.kwargs)
        self.assertEqual(
            1,
            StockPaperLedger(
                self.path, quote_currency="KRW", initial_cash="1000", fee_pct="0"
            ).report(marks={"005930": "21"})["simulated_fills"],
        )

    def test_manual_order_cannot_contaminate_strategy_ledger(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        ledger = StockPaperLedger(self.path, quote_currency="KRW", initial_cash="1000", fee_pct="0")
        with self.assertRaises(ValueError):
            ledger.execute("manual-1", "005930", "sell", 1, price="21")

    def test_strategy_parameters_changed_after_restart_refused(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **dict(self.kwargs, allocation_pct="25"))

    def test_cannot_attach_strategy_to_manual_stock_portfolio(self):
        ledger = StockPaperLedger(self.path, quote_currency="KRW", initial_cash="1000", fee_pct="0")
        ledger.execute("manual-1", "005930", "buy", 1, price="10")
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **self.kwargs)

    def test_saved_fill_tampering_refuses_resume(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        with self.assertRaises(ValueError):
            replay_forward(self.fixture, self.path, **self.kwargs)

    def test_missing_committed_fills_refuses_replay_and_append_without_repair(self):
        completed = self.prices + [22, 23, 8, 7]
        for delete_from in (1, 2):
            for appended in ([], [6, 20, 21]):
                with self.subTest(delete_from=delete_from, appended=appended):
                    path = self.path.with_name(f"missing-{delete_from}-{len(appended)}.sqlite")
                    saved = replay_forward(fixture(completed), path, **self.kwargs)
                    self.assertEqual(2, saved["total_simulated_fills"])
                    with sqlite3.connect(path) as db:
                        db.execute("DELETE FROM paper_fills WHERE sequence >= ?", (delete_from,))
                    with sqlite3.connect(path) as db:
                        before = list(db.iterdump())
                    with self.assertRaises(ValueError):
                        replay_forward(fixture(completed + appended), path, **self.kwargs)
                    with sqlite3.connect(path) as db:
                        self.assertEqual(before, list(db.iterdump()))

    def test_fill_ahead_of_committed_cursor_refuses_resume(self):
        completed = self.prices + [22, 23, 8, 7]
        replay_forward(fixture(completed), self.path, **self.kwargs)
        future_fill = {
            "mode": "paper",
            "client_id": "ma-11-buy",
            "symbol": "005930",
            "side": "buy",
            "shares": 16,
            "price": "21",
            "gross": "336",
            "fee": "0",
            "quote_currency": "KRW",
            "status": "simulated_fill",
        }
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO paper_fills (client_id,symbol,side,shares,price,result_json) "
                "VALUES (?,?,?,?,?,?)",
                ("ma-11-buy", "005930", "buy", 16, "21", json.dumps(future_fill)),
            )
        with sqlite3.connect(self.path) as db:
            before = list(db.iterdump())
        with self.assertRaises(ValueError):
            replay_forward(fixture(completed + [6, 20, 21]), self.path, **self.kwargs)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(before, list(db.iterdump()))

    def test_invalid_committed_cursor_refuses_resume_without_mutation(self):
        for corrupted in (0, -1, 1.5, len(self.prices) + 1):
            with self.subTest(corrupted=corrupted):
                path = self.path.with_name(f"cursor-{corrupted}.sqlite")
                replay_forward(self.fixture, path, **self.kwargs)
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE paper_strategy_state SET bars=?", (corrupted,))
                with sqlite3.connect(path) as db:
                    before = list(db.iterdump())
                with self.assertRaises(ValueError):
                    replay_forward(self.fixture, path, **self.kwargs)
                with sqlite3.connect(path) as db:
                    self.assertEqual(before, list(db.iterdump()))

    def test_distinct_currency_cannot_reuse_one_database(self):
        replay_forward(self.fixture, self.path, **self.kwargs)
        with self.assertRaises(ValueError):
            StockPaperLedger(self.path, quote_currency="USD", initial_cash="1000", fee_pct="0")


if __name__ == "__main__":
    unittest.main()
