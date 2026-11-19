"""One-shot public-data paper cycle regression without network or live orders."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.upbit_paper_cycle import run_cycle

START = 1699920000000
H = 3600000


def public_rows(indices):
    return [
        {
            "market": "KRW-BTC", "unit": 60,
            "candle_date_time_utc": datetime.fromtimestamp(
                (START + i * H) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": Decimal("100"), "high_price": Decimal("100"),
            "low_price": Decimal("100"), "trade_price": Decimal("100"),
            "candle_acc_trade_volume": Decimal("100"),
        }
        for i in reversed(indices)
    ]


class PublicPaperCycleTests(unittest.TestCase):
    def test_same_completed_snapshot_replays_with_no_duplicate_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "candles.json"
            checkpoint = Path(directory) / "checkpoint.json"
            kwargs = {"market": "KRW-BTC", "unit": 60, "count": 4,
                      "now_ms": START + 4 * H + 10000,
                      "fetcher": lambda *args: public_rows([0, 1, 2, 3])}
            with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
                first = run_cycle(archive, checkpoint, **kwargs)
                before = checkpoint.read_bytes()
                second = run_cycle(archive, checkpoint, **kwargs)
            self.assertEqual("paper", first["mode"])
            self.assertFalse(first["live_orders_enabled"])
            self.assertEqual(4, first["processed_bars"])
            self.assertEqual(0, second["appended_bars"])
            self.assertEqual(first["equity"], second["equity"])
            self.assertEqual(before, checkpoint.read_bytes())

    def test_different_checkpoint_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "candles.json"
            checkpoint = Path(directory) / "checkpoint.json"
            kwargs = {"count": 4, "now_ms": START + 4 * H + 10000,
                      "fetcher": lambda *args: public_rows([0, 1, 2, 3])}
            run_cycle(archive, checkpoint, **kwargs)
            before = checkpoint.read_bytes()
            before_archive = archive.read_bytes()
            with self.assertRaises(ValueError):
                run_cycle(archive, checkpoint, initial_balance=Decimal("2"), **kwargs)
            self.assertEqual(before, checkpoint.read_bytes())
            self.assertEqual(before_archive, archive.read_bytes())

    def test_forged_checkpoint_is_rejected_before_new_market_capture(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "candles.json"
            checkpoint = Path(directory) / "checkpoint.json"
            kwargs = {"count": 4, "now_ms": START + 4 * H + 10000,
                      "fetcher": lambda *args: public_rows([0, 1, 2, 3])}
            run_cycle(archive, checkpoint, **kwargs)
            state = json.loads(checkpoint.read_text())
            state["exchange"]["balances"]["KRW"] = "999999999"
            checkpoint.write_text(json.dumps(state))
            history = archive.read_bytes()
            corrupted = checkpoint.read_bytes()
            fetch_count = []
            def forbidden_fetch(*args):
                fetch_count.append(True)
                return public_rows([0, 1, 2, 3, 4])
            with self.assertRaises(ValueError):
                run_cycle(archive, checkpoint, count=5, now_ms=START + 5 * H + 10000,
                          fetcher=forbidden_fetch)
            self.assertEqual([], fetch_count)
            self.assertEqual(history, archive.read_bytes())
            self.assertEqual(corrupted, checkpoint.read_bytes())

    def test_archive_and_checkpoint_must_be_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.json"
            with self.assertRaises(ValueError):
                run_cycle(path, path, fetcher=lambda *args: public_rows([0]))


if __name__ == "__main__":
    unittest.main()
