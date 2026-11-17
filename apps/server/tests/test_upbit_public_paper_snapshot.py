"""Read-only public-candle snapshot guards; all tests use injected responses."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.upbit_public_paper_snapshot import build_snapshot

START = 1699920000000
HOUR = 3600000


def candle(index, *, market="KRW-BTC", unit=60, price="100"):
    stamp = START + index * HOUR
    return {
        "market": market, "unit": unit,
        "candle_date_time_utc": datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "opening_price": Decimal(price), "high_price": Decimal(price),
        "low_price": Decimal(price), "trade_price": Decimal(price),
        "candle_acc_trade_volume": Decimal("10"),
    }


class UpbitPublicPaperTests(unittest.TestCase):
    def test_latest_forming_bar_excluded_without_orders(self):
        fetched = [candle(3), candle(2), candle(1), candle(0)]
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests")):
            result = build_snapshot("KRW-BTC", 60, count=4,
                                    now_ms=START + 3 * HOUR + 10000,
                                    fetcher=lambda *args: fetched)
        self.assertEqual(result["symbol"], "BTC/KRW")
        self.assertEqual(len(result["candles"]), 3)
        self.assertEqual(result["candles"][-1][0], START + 2 * HOUR)
        self.assertFalse(result["market_data"]["live_orders_enabled"])

    def test_duplicate_or_wrong_market_is_rejected(self):
        for fetched in (
            [candle(1), candle(1)],
            [candle(1, market="KRW-ETH")],
            [candle(1, unit=30)],
        ):
            with self.subTest(fetched=fetched), self.assertRaises(ValueError):
                build_snapshot("KRW-BTC", 60, count=5,
                               now_ms=START + 2 * HOUR + 10000,
                               fetcher=lambda *args: fetched)

    def test_gap_requires_explicit_acknowledgment(self):
        fetched = [candle(3), candle(1)]
        now = START + 4 * HOUR + 10000
        with self.assertRaises(ValueError):
            build_snapshot("KRW-BTC", 60, count=4, now_ms=now,
                           fetcher=lambda *args: fetched)
        result = build_snapshot("KRW-BTC", 60, count=4, now_ms=now,
                                allow_gaps=True, fetcher=lambda *args: fetched)
        self.assertEqual(result["market_data"]["gap_count"], 1)

    def test_stale_candles_and_forming_only_fail_closed(self):
        with self.assertRaises(ValueError):
            build_snapshot("KRW-BTC", 60, now_ms=START + 10 * HOUR,
                           fetcher=lambda *args: [candle(0)])
        with self.assertRaises(ValueError):
            build_snapshot("KRW-BTC", 60, now_ms=START + 3 * HOUR + 10000,
                           fetcher=lambda *args: [candle(3)])

    def test_rejects_invalid_markets_and_count(self):
        for market in ("BTC/KRW", "KRW-btc", "USD-BTC", "KRW-../X"):
            with self.subTest(market=market), self.assertRaises(ValueError):
                build_snapshot(market, 60, now_ms=START + 2 * HOUR,
                               fetcher=lambda *args: [candle(0)])
        with self.assertRaises(ValueError):
            build_snapshot("KRW-BTC", 60, count=201, now_ms=START + 2 * HOUR,
                           fetcher=lambda *args: [candle(0)])


if __name__ == "__main__":
    unittest.main()
