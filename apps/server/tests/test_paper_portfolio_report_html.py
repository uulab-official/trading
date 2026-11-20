"""Offline static multi-asset PAPER dashboard security and escaping tests."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.paper_portfolio_report_html import render, main


def sample():
    return {
        "mode": "paper", "live_orders_enabled": False,
        "status": "audited_offline_only",
        "generated_at_utc": "2026-11-20T00:00:00+00:00",
        "cross_portfolio_total": None,
        "portfolios": [
            {
                "type": "crypto", "status": "verified_offline_paper",
                "symbol": "BTC/USDT", "timeframe": "1h",
                "processed_bars": 55, "trade_count": 2,
                "daily_loss_halted": False, "drawdown_halted": False,
            },
            {
                "type": "stock", "status": "audited_stock_paper",
                "symbol": "005930", "quote_currency": "KRW",
                "processed_bars": 55, "trade_count": 2,
                "cash": "900000", "marked_equity": "1010000",
                "total_return_pct": "1.0",
            },
        ],
    }


class MultiAssetPaperHtmlTests(unittest.TestCase):
    def test_all_data_is_separate_and_html_has_no_executable_js(self):
        page = render(sample())
        self.assertIn("BTC/USDT", page)
        self.assertIn("005930", page)
        self.assertIn("KRW", page)
        self.assertIn("USDT", page)
        self.assertIn("실거래 아님", page)
        self.assertIn("cross", json.dumps(sample()))
        self.assertIn("default-src 'none'", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("https://", page.lower())

    def test_text_is_escaped_to_prevent_html_injection(self):
        data = sample()
        data["portfolios"][1]["symbol"] = "<img src=x onerror=alert(1)>"
        page = render(data)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)

    def test_forged_live_or_unverified_status_is_rejected(self):
        for changed in (
            dict(sample(), mode="live"),
            dict(sample(), live_orders_enabled=True),
            dict(sample(), cross_portfolio_total="1000000"),
        ):
            with self.assertRaises(ValueError):
                render(changed)
        changed = sample()
        changed["portfolios"][1]["status"] = "healthy"
        with self.assertRaises(ValueError):
            render(changed)

    def test_cli_never_overwrites_original_evidence_or_previous_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "audit.json"
            target = root / "dashboard.html"
            source.write_text(json.dumps(sample()))
            previous = source.read_bytes()
            with redirect_stdout(StringIO()):
                self.assertEqual(0, main([str(source), "--output", str(target)]))
            saved = target.read_bytes()
            with redirect_stderr(StringIO()):
                self.assertEqual(2, main([str(source), "--output", str(target)]))
            self.assertEqual(previous, source.read_bytes())
            self.assertEqual(saved, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
