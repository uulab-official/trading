"""Historical requests use UTC instants regardless of the host's time zone."""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from trading_backtest.data_loader import HistoricalDataLoader

INSTANT = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return INSTANT.astimezone().replace(tzinfo=None)
        return INSTANT.astimezone(tz)

    @classmethod
    def utcnow(cls):
        # Supports the old implementation so the regression exposes its offset.
        return INSTANT.replace(tzinfo=None)


class HistoricalRequestUtcTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX time-zone selection")
    def test_paginated_request_window_is_the_same_in_every_host_timezone(self):
        start = int((INSTANT - timedelta(days=1)).timestamp() * 1000)
        end = int(INSTANT.timestamp() * 1000)
        hour = 3_600_000

        for host_timezone in ("UTC0", "UTC-9", "UTC+5"):
            with self.subTest(timezone=host_timezone):
                calls = []
                configurations = []
                pages = [
                    [[start, 10, 12, 9, 11, 5], [start + hour, 11, 13, 10, 12, 6]],
                    [[end - hour, 12, 14, 11, 13, 7]],
                ]

                class OfflineExchange:
                    def __init__(self, config):
                        configurations.append(config)

                    def fetch_ohlcv(self, symbol, timeframe, since, limit):
                        calls.append((symbol, timeframe, since, limit))
                        return pages.pop(0) if pages else []

                try:
                    with (
                        patch.dict(os.environ, {"TZ": host_timezone}),
                        patch.dict(sys.modules, {"ccxt": SimpleNamespace(offline=OfflineExchange)}),
                        patch("trading_backtest.data_loader.datetime", FrozenClock),
                        tempfile.TemporaryDirectory() as directory,
                    ):
                        time.tzset()
                        rows = HistoricalDataLoader(directory).fetch_from_exchange(
                            "offline", "BTC/USDT", "1h", days=1
                        )
                finally:
                    time.tzset()

                self.assertEqual(configurations, [{"enableRateLimit": True}])
                self.assertEqual(
                    calls,
                    [
                        ("BTC/USDT", "1h", start, 1000),
                        ("BTC/USDT", "1h", start + 2 * hour, 1000),
                    ],
                )
                self.assertEqual(rows["timestamp"].tolist(), [start, start + hour, end - hour])


if __name__ == "__main__":
    unittest.main()
