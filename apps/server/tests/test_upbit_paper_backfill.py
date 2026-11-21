"""Offline-only bounded history backfill test vectors; no HTTP requests."""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.upbit_paper_archive import merge_archive
from tools.upbit_paper_backfill import plan_backfill
from tools.upbit_public_paper_snapshot import _request_candles

START = 1699920000000
HOUR = 3600000


def bars(indices):
    return [[START + index * HOUR, "100", "100", "100", "100", "10"] for index in indices]


def sample(indices):
    return {"mode": "paper", "symbol": "BTC/KRW", "timeframe": "60m", "candles": bars(indices)}


def raw(indices):
    return [
        {
            "market": "KRW-BTC",
            "unit": 60,
            "candle_date_time_utc": datetime.fromtimestamp(
                (START + index * HOUR) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": 100,
            "high_price": 100,
            "low_price": 100,
            "trade_price": 100,
            "candle_acc_trade_volume": 10,
        }
        for index in reversed(indices)
    ]


class PublicHistoryBackfillTests(unittest.TestCase):
    def setUp(self):
        self.archive, _ = merge_archive(None, sample([0, 1, 2, 3]))
        self.latest = sample([6, 7, 8])

    def test_two_historical_pages_recover_overlap_without_rewriting_old(self):
        requested = []

        def fetch(unit, market, count, to):
            requested.append(to)
            if len(requested) == 1:
                return raw([4, 5])
            return raw([2, 3])

        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            recovered = plan_backfill(
                self.archive, newest=self.latest, fetcher=fetch, count=3, pages=2
            )
        self.assertEqual(2, len(requested))
        self.assertEqual(5, recovered["backfill_review"]["appended_bars"])
        self.assertEqual(9, len(recovered["candles"]))
        self.assertEqual(self.archive["candles"], recovered["candles"][:4])
        self.assertTrue(recovered["backfill_review"]["manual_promotion_required"])
        self.assertFalse(recovered["backfill_review"]["live_orders_enabled"])

    def test_refuses_missing_overlap_and_exhausted_page_limit(self):
        with self.assertRaises(ValueError):
            plan_backfill(
                self.archive,
                newest=self.latest,
                pages=1,
                fetcher=lambda *args: raw([4, 5]),
                count=3,
            )

    def test_refuses_altered_saved_overlap(self):
        changed = raw([2, 3, 4, 5])
        changed[-1]["trade_price"] = 99
        changed[-1]["low_price"] = 99
        with self.assertRaises(ValueError):
            plan_backfill(
                self.archive, newest=self.latest, pages=1, fetcher=lambda *args: changed, count=4
            )

    def test_refuses_unfilled_interval_even_if_overlap_exists(self):
        with self.assertRaises(ValueError):
            plan_backfill(
                self.archive,
                newest=sample([3, 6, 7]),
                pages=1,
                fetcher=lambda *args: raw([3]),
                count=3,
            )

    def test_public_cutoff_request_is_exclusive_utc(self):
        captured = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return b"[]"

        def fake_open(request, timeout):
            captured.append(request.full_url)
            return Response()

        with patch("tools.upbit_public_paper_snapshot.urlopen", side_effect=fake_open):
            self.assertEqual([], _request_candles(60, "KRW-BTC", 2, to=START + 4 * HOUR))
        self.assertIn("to=", captured[0])
        self.assertIn("Z", captured[0])


if __name__ == "__main__":
    unittest.main()
