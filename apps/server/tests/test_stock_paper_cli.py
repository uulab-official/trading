"""Operator stock paper CLI is explicitly paper-only, never a live broker."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_cli import main


class StockPaperCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "stock-paper.sqlite"
        self.base = [
            "--mode",
            "paper",
            "--database",
            str(self.db),
            "--currency",
            "KRW",
            "--initial-cash",
            "100000",
            "--fee-pct",
            "0.1",
        ]

    def _run(self, extra):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            code = main(self.base + extra)
        return code, json.loads(output.getvalue()) if output.getvalue().strip() else None

    def test_trade_resume_and_report(self):
        command = [
            "trade",
            "--client-id",
            "stock-buy-001",
            "--symbol",
            "005930",
            "--side",
            "buy",
            "--shares",
            "2",
            "--price",
            "10000",
        ]
        code, result = self._run(command)
        self.assertEqual(0, code)
        self.assertEqual("simulated_fill", result["status"])
        self.assertEqual(0, self._run(command)[0])
        code, summary = self._run(["report", "--mark", "005930=11000"])
        self.assertEqual(0, code)
        self.assertEqual(1, summary["simulated_fills"])
        self.assertEqual(Decimal("101980"), Decimal(summary["marked_equity"]))
        self.assertFalse(summary["live_orders_enabled"])

    def test_live_mode_is_rejected_before_creating_database(self):
        with self.assertRaises(SystemExit) as error, redirect_stderr(StringIO()):
            main(
                [
                    "--mode",
                    "live",
                    "--database",
                    str(self.db),
                    "--currency",
                    "KRW",
                    "--initial-cash",
                    "100000",
                    "report",
                ]
            )
        self.assertEqual(2, error.exception.code)
        self.assertFalse(self.db.exists())

    def test_unmarked_position_refuses_to_report(self):
        self._run(
            [
                "trade",
                "--client-id",
                "buy",
                "--symbol",
                "005930",
                "--side",
                "buy",
                "--shares",
                "1",
                "--price",
                "10000",
            ]
        )
        code, summary = self._run(["report"])
        self.assertEqual(2, code)
        self.assertIsNone(summary)


if __name__ == "__main__":
    unittest.main()
