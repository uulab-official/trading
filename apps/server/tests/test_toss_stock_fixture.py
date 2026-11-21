"""Saved Toss response conversion is local-only and refuses output collisions."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.toss_stock_fixture import main


class TossStockFixtureCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "api-response.json"
        self.destination = self.folder / "paper-candles.json"
        self.source.write_text(
            json.dumps(
                {
                    "result": {
                        "candles": [
                            {
                                "timestamp": "2026-03-25T09:30:00+09:00",
                                "openPrice": "100",
                                "highPrice": "110",
                                "lowPrice": "90",
                                "closePrice": "105",
                                "volume": "500",
                                "currency": "KRW",
                            }
                        ]
                    }
                }
            )
        )
        self.args = [
            "--input",
            str(self.source),
            "--output",
            str(self.destination),
            "--symbol",
            "005930",
            "--currency",
            "KRW",
            "--interval",
            "1m",
            "--observed-at",
            "2026-03-25T09:31:00+09:00",
        ]

    def test_saved_response_is_converted_without_account_credentials(self):
        original = self.source.read_bytes()
        self.assertEqual(0, main(self.args))
        converted = json.loads(self.destination.read_text())
        self.assertEqual("paper", converted["mode"])
        self.assertEqual("source_close_to_utc_open", converted["timestamp_semantics"])
        self.assertTrue(converted["no_live_orders"])
        self.assertEqual(original, self.source.read_bytes())
        with redirect_stderr(StringIO()):
            self.assertEqual(2, main(self.args))
        self.assertEqual(1, len(converted["candles"]))

    def test_rejects_daily_interval_before_creating_output(self):
        invalid = self.args.copy()
        invalid[invalid.index("1m")] = "1d"
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main(invalid)
        self.assertEqual(2, error.exception.code)
        self.assertFalse(self.destination.exists())

    def test_missing_timezone_is_rejected(self):
        invalid = self.args.copy()
        invalid[-1] = "2026-03-25T09:31:00"
        with redirect_stderr(StringIO()):
            self.assertEqual(2, main(invalid))
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
