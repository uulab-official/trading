"""Toss 1-minute JSON candle normalization tests (no OAuth and no HTTP)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data.toss_stock_candles import parse_toss_minute_candles

KST = timezone(timedelta(hours=9))


def record(timestamp, *, close="72000", currency="KRW"):
    return {
        "timestamp": timestamp,
        "openPrice": "71000", "highPrice": "73000",
        "lowPrice": "70000", "closePrice": close,
        "volume": "123", "currency": currency,
    }


class TossCandleParserTests(unittest.TestCase):
    def setUp(self):
        self.observed = datetime(2026, 3, 25, 9, 32, 10, tzinfo=KST)
        self.payload = {"result": {"candles": [
            record("2026-03-25T09:32:00+09:00"),
            record("2026-03-25T09:31:00+09:00"),
            record("2026-03-25T09:30:00+09:00"),
        ]}}

    def parse(self, payload=None, **kwargs):
        return parse_toss_minute_candles(
            self.payload if payload is None else payload,
            symbol="005930", quote_currency="KRW",
            observed_at=self.observed, **kwargs,
        )

    def test_closing_timestamp_converts_to_open_and_sorts_ascending(self):
        result = self.parse()
        rows = result["candles"]
        expected_open = datetime(2026, 3, 25, 9, 29, tzinfo=KST)
        self.assertEqual(int(expected_open.timestamp() * 1000), rows[0][0])
        self.assertEqual(3, len(rows))
        self.assertEqual("source_close_to_utc_open", result["timestamp_semantics"])
        self.assertTrue(result["no_live_orders"])

    def test_forming_bar_excluded_using_safety_delay(self):
        self.observed = datetime(2026, 3, 25, 9, 32, 2, tzinfo=KST)
        result = self.parse()
        self.assertEqual(2, len(result["candles"]))

    def test_currency_and_ohlc_invalidity_fails(self):
        bad = {"result": {"candles": [record(
            "2026-03-25T09:30:00+09:00", currency="USD"
        )]}}
        with self.assertRaises(ValueError):
            self.parse(bad)
        for malformed in ("NaN", "95000", "-1"):
            with self.subTest(malformed=malformed):
                bad = {"result": {"candles": [record(
                    "2026-03-25T09:30:00+09:00", close=malformed
                )]}}
                with self.assertRaises(ValueError):
                    self.parse(bad)

    def test_rejects_missing_timezone_and_duplicate_minutes(self):
        for times in (
            ["2026-03-25T09:30:00"],
            ["2026-03-25T09:30:00+09:00"] * 2,
        ):
            with self.subTest(times=times):
                bad = {"result": {"candles": [record(time) for time in times]}}
                with self.assertRaises(ValueError):
                    self.parse(bad)

    def test_usd_candles_are_separate_from_krw(self):
        response = {"result": {"candles": [
            record("2026-03-25T09:30:00+09:00", currency="USD")
        ]}}
        result = parse_toss_minute_candles(
            response, symbol="AAPL", quote_currency="USD",
            observed_at=self.observed
        )
        self.assertEqual("USD", result["quote_currency"])


if __name__ == "__main__":
    unittest.main()
