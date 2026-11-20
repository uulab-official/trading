"""End-to-end synthetic paper smoke needs neither network nor credentials."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_smoke import run_smoke


class SyntheticStockPaperSmokeTests(unittest.TestCase):
    def test_synthetic_pipeline_including_backup_deep_verification(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            outcome = run_smoke()
        self.assertEqual("synthetic_stock_paper_smoke_verified", outcome["status"])
        self.assertEqual("paper", outcome["mode"])
        self.assertFalse(outcome["live_orders_enabled"])
        self.assertEqual("synthetic_not_real_market_data", outcome["fixture"])
        self.assertEqual(55, outcome["bars"])
        self.assertTrue(outcome["backup_verified"])
        self.assertTrue(outcome["static_html_verified"])
        self.assertTrue(outcome["duplicate_replay_prevented"])


if __name__ == "__main__":
    unittest.main()
