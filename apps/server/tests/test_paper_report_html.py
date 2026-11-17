"""Static HTML dashboard safety and accounting regression tests."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.paper_report_html import generate, main
from trading_bot.paper_runner import PaperRunner, PaperRunConfig


class PaperHtmlReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = self.root / "fixture.json"
        self.checkpoint = self.root / "checkpoint.json"
        source = json.loads((ROOT / "config/examples/offline_fixture.json").read_text())
        self.fixture.write_text(json.dumps(source))
        asyncio.run(PaperRunner(PaperRunConfig()).run(source["candles"], checkpoint_path=self.checkpoint))

    def test_html_report_never_modifies_checkpoint_and_has_no_network_assets(self):
        before = self.checkpoint.read_bytes()
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            html = generate(self.fixture, self.checkpoint)
        self.assertEqual(before, self.checkpoint.read_bytes())
        self.assertIn("Offline Replay Audit", html)
        self.assertIn("Simulated fills (2)", html)
        self.assertIn("NOT LIVE TRADING", html)
        self.assertIn(hashlib.sha256(before).hexdigest(), html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("https://", html.lower())

    def test_corrupt_economic_state_refuses_dashboard(self):
        state = json.loads(self.checkpoint.read_text())
        state["exchange"]["balances"]["USDT"] = "900000"
        changed = json.dumps(state).encode()
        self.checkpoint.write_bytes(changed)
        with self.assertRaises(ValueError):
            generate(self.fixture, self.checkpoint)
        self.assertEqual(self.checkpoint.read_bytes(), changed)

    def test_html_escapes_untrusted_order_ids(self):
        # Even if untrusted text somehow enters a verified report, it cannot
        # become active markup. Keep the chart/view static and script-free.
        from tools.paper_report_html import _text
        self.assertEqual("&lt;script&gt;&amp;&quot;", _text('<script>&"'))

    def test_output_cannot_replace_input(self):
        with self.assertRaises(SystemExit) as error:
            main([str(self.fixture), str(self.checkpoint), "--output", str(self.checkpoint)])
        self.assertEqual(2, error.exception.code)

    def test_html_written_with_no_secret_integration(self):
        output = self.root / "reports" / "paper.html"
        self.assertEqual(0, main([str(self.fixture), str(self.checkpoint), "--output", str(output)]))
        page = output.read_text()
        self.assertIn("Live trading approved: NO", page)
        self.assertIn("Content-Security-Policy", page)


if __name__ == "__main__":
    unittest.main()
