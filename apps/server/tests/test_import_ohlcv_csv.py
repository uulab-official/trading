"""Offline CSV provenance, gaps, and fail-closed validation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.import_ohlcv_csv import convert, main

HEADER = "timestamp,open,high,low,close,volume\n"
START = 1699920000000  # Exact 1h boundary


def line(i, *, timestamp=None, close="100"):
    stamp = START + 3600000 * i if timestamp is None else timestamp
    return f"{stamp},100,110,90,{close},1000\n"


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.csv = self.root / "history.csv"
        self.csv.write_text(HEADER + line(0) + line(1))

    def test_valid_fixture_is_paper_and_has_provenance(self):
        report = convert(self.csv, "BTC/USDT", "1h")
        self.assertEqual(report["mode"], "paper")
        self.assertEqual(report["import_metadata"]["bars"], 2)
        self.assertEqual(report["import_metadata"]["gap_count"], 0)
        self.assertEqual(report["candles"][1][4], "100")

    def test_gaps_require_explicit_acknowledgment(self):
        self.csv.write_text(HEADER + line(0) + line(3))
        with self.assertRaises(ValueError):
            convert(self.csv, "BTC/KRW", "1h")
        result = convert(self.csv, "BTC/KRW", "1h", allow_gaps=True)
        self.assertEqual(result["import_metadata"]["gap_count"], 1)
        self.assertEqual(result["symbol"], "BTC/KRW")

    def test_duplicates_out_of_order_and_bad_ohlcv_are_rejected(self):
        for content in (
            HEADER + line(0) + line(0),
            HEADER + line(1) + line(0),
            HEADER + line(0, close="150"),
            HEADER + line(0, timestamp=START + 1),
            HEADER + line(0, close="NaN"),
            HEADER + line(0) + "garbage\n",
        ):
            with self.subTest(content=content):
                self.csv.write_text(content)
                with self.assertRaises((ValueError, ArithmeticError)):
                    convert(self.csv, "BTC/USDT", "1h")

    def test_cli_writes_valid_fixture_without_replacing_csv(self):
        source = self.csv.read_bytes()
        output = self.root / "paper.json"
        self.assertEqual(
            0,
            main(
                [
                    str(self.csv),
                    "--symbol",
                    "BTC/USDT",
                    "--timeframe",
                    "1h",
                    "--output",
                    str(output),
                ]
            ),
        )
        self.assertEqual(source, self.csv.read_bytes())
        fixture = json.loads(output.read_text())
        self.assertEqual(fixture["mode"], "paper")

    def test_cli_rejects_input_output_collision(self):
        with self.assertRaises(SystemExit) as err:
            main(
                [
                    str(self.csv),
                    "--symbol",
                    "BTC/USDT",
                    "--timeframe",
                    "1h",
                    "--output",
                    str(self.csv),
                ]
            )
        self.assertEqual(err.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
