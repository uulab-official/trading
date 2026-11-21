"""Append-only archive contracts without any real networking."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for source in (ROOT, ROOT / "packages/data", ROOT / "apps/server"):
    sys.path.insert(0, str(source))
from tools.upbit_paper_archive import capture_once, merge_archive

START = 1699920000000
H = 3600000


def make_snapshot(indices, *, price="100"):
    return {
        "mode": "paper",
        "symbol": "BTC/KRW",
        "timeframe": "60m",
        "candles": [[START + i * H, price, price, price, price, "100"] for i in indices],
    }


class PaperArchiveTests(unittest.TestCase):
    def test_identical_overlap_appends_without_mutating_history(self):
        initial, count = merge_archive(None, make_snapshot([0, 1, 2]))
        self.assertEqual(3, count)
        merged, added = merge_archive(initial, make_snapshot([1, 2, 3, 4]))
        self.assertEqual(2, added)
        self.assertEqual(5, len(merged["candles"]))
        self.assertEqual(initial["candles"], merged["candles"][:3])
        self.assertEqual(2, merged["archive_metadata"]["snapshots"])
        repeated, added_again = merge_archive(merged, make_snapshot([3, 4]))
        self.assertEqual(0, added_again)
        self.assertEqual(repeated, merged)

    def test_revision_and_unknown_gap_refused(self):
        archive, _ = merge_archive(None, make_snapshot([0, 1, 2]))
        revised = make_snapshot([1, 2, 3])
        revised["candles"][0][4] = "99"
        with self.assertRaises(ValueError):
            merge_archive(archive, revised)
        with self.assertRaises(ValueError):
            merge_archive(archive, make_snapshot([5, 6]))
        with self.assertRaises(ValueError):
            merge_archive(archive, make_snapshot([2, 4]))
        allowed, added = merge_archive(archive, make_snapshot([2, 4]), allow_gaps=True)
        self.assertEqual(1, added)
        self.assertEqual(4, len(allowed["candles"]))

    def test_archive_currency_and_paper_mode_cannot_change(self):
        archive, _ = merge_archive(None, make_snapshot([0, 1]))
        wrong = make_snapshot([1, 2])
        wrong["symbol"] = "ETH/KRW"
        with self.assertRaises(ValueError):
            merge_archive(archive, wrong)
        wrong = make_snapshot([1, 2])
        wrong["mode"] = "live"
        with self.assertRaises(ValueError):
            merge_archive(archive, wrong)

    def test_live_quote_capture_is_one_shot_and_resumable(self):
        from datetime import datetime, timezone

        def response(*args):
            return [
                {
                    "market": "KRW-BTC",
                    "unit": 60,
                    "candle_date_time_utc": datetime.fromtimestamp(
                        (START + i * H) / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%S"),
                    "opening_price": 100,
                    "high_price": 100,
                    "low_price": 100,
                    "trade_price": 100,
                    "candle_acc_trade_volume": 10,
                }
                for i in (2, 1, 0)
            ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            kwargs = {
                "market": "KRW-BTC",
                "unit": 60,
                "count": 3,
                "now_ms": START + 3 * H + 10000,
                "fetcher": response,
            }
            first = capture_once(path, **kwargs)
            before = path.read_bytes()
            second = capture_once(path, **kwargs)
            self.assertEqual(3, first["appended_bars"])
            self.assertEqual(0, second["appended_bars"])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual("paper", json.loads(before)["mode"])


if __name__ == "__main__":
    unittest.main()
