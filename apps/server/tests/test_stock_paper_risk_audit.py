"""No-network historical stock paper drawdown audit regression."""
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_forward import replay_forward
from tools.stock_paper_risk_audit import stock_risk_audit

START = 1774417800000 - (1774417800000 % 60000)


def make_fixture(prices):
    return {
        "mode": "paper", "no_live_orders": True,
        "interval": "1m", "timestamp_semantics": "source_close_to_utc_open",
        "symbol": "005930", "quote_currency": "KRW",
        "candles": [[START + index * 60000, str(price), str(price),
                     str(price), str(price), "100"]
                    for index, price in enumerate(prices)],
    }


class StockPaperRiskAuditTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.db = Path(t.name) / "stock.sqlite"
        self.data = make_fixture([10, 9, 8, 20, 21, 17, 13, 10, 8, 6])
        self.params = {
            "initial_cash": "1000", "fee_pct": "0",
            "fast": 2, "slow": 3, "allocation_pct": "50",
        }
        replay_forward(self.data, self.db, **self.params)

    def test_reconciles_bar_by_bar_and_reports_nonnegative_drawdown(self):
        result = stock_risk_audit(self.data, self.db, **self.params)
        self.assertEqual("stock_paper_risk_metrics_audited", result["status"])
        self.assertFalse(result["live_orders_enabled"])
        self.assertEqual(10, result["bars"])
        self.assertGreaterEqual(Decimal(result["max_drawdown_pct"]), Decimal("0"))
        self.assertLessEqual(Decimal(result["max_drawdown_pct"]), Decimal("100"))
        self.assertGreaterEqual(Decimal(result["peak_bar_close_equity"]),
                                Decimal(result["final_equity"]))

    def test_tampered_stock_fill_cannot_produce_valid_risk_report(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        with self.assertRaises(ValueError):
            stock_risk_audit(self.data, self.db, **self.params)

    def test_change_in_historical_price_fails_before_reporting_drawdown(self):
        altered = json.loads(json.dumps(self.data))
        altered["candles"][0][4] = "11"
        altered["candles"][0][2] = "11"
        with self.assertRaises(ValueError):
            stock_risk_audit(altered, self.db, **self.params)

    def test_different_strategy_parameters_fail_closed(self):
        with self.assertRaises(ValueError):
            stock_risk_audit(self.data, self.db, **dict(
                self.params, allocation_pct="10",
            ))


if __name__ == "__main__":
    unittest.main()
