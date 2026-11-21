"""Read-only offline stock strategy HTML report regression tests."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_forward import replay_forward
from tools.stock_paper_report_html import generate, main

START = 1774417800000 - (1774417800000 % 60000)


def fixture():
    prices = [10, 9, 8, 20, 21]
    return {
        "mode": "paper",
        "interval": "1m",
        "no_live_orders": True,
        "timestamp_semantics": "source_close_to_utc_open",
        "symbol": "005930",
        "quote_currency": "KRW",
        "candles": [
            [START + i * 60000, str(p), str(p), str(p), str(p), "100"] for i, p in enumerate(prices)
        ],
    }


class StockPaperReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "paper.sqlite"
        self.input = self.root / "candles.json"
        self.output = self.root / "report.html"
        self.data = fixture()
        self.input.write_text(json.dumps(self.data))
        self.opts = dict(initial_cash="1000", fee_pct="0", fast=2, slow=3, allocation_pct="50")
        replay_forward(self.data, self.db, **self.opts)

    def test_standalone_csp_report_contains_only_hypothetical_data(self):
        result = generate(self.data, self.db, **self.opts)
        self.assertIn("PAPER ONLY", result)
        self.assertIn("Content-Security-Policy", result)
        self.assertIn("실거래 아님", result)
        self.assertNotIn("<script", result.lower())
        self.assertNotIn("https://", result.lower())

    def test_existing_output_is_never_replaced(self):
        args = [
            str(self.input),
            "--database",
            str(self.db),
            "--output",
            str(self.output),
            "--initial-cash",
            "1000",
            "--fee-pct",
            "0",
            "--fast",
            "2",
            "--slow",
            "3",
            "--allocation-pct",
            "50",
        ]
        with redirect_stdout(StringIO()):
            self.assertEqual(0, main(args))
        original = self.output.read_bytes()
        with redirect_stderr(StringIO()):
            self.assertEqual(2, main(args))
        self.assertEqual(original, self.output.read_bytes())

    def test_tampering_fails_before_generating_html(self):
        altered = fixture()
        altered["candles"][0][4] = "11"
        altered["candles"][0][2] = "11"
        with self.assertRaises(ValueError):
            generate(altered, self.db, **self.opts)
        self.assertFalse(self.output.exists())

    def test_report_escapes_untrusted_symbol(self):
        from tools.stock_paper_report_html import _escape

        self.assertEqual("&lt;img src=x&gt;", _escape("<img src=x>"))


if __name__ == "__main__":
    unittest.main()
