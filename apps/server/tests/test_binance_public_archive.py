"""Network-free regression tests of the public history archive."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools import binance_public_archive as archive

STEP = 60_000
START = 1_700_000_040_000


def raw(stamp, price="100.00"):
    return [stamp, price, price, price, price, "3.000", stamp + STEP - 1, "0", 1, "0", "0", "0"]


class Feed:
    def __init__(self, latest):
        self.latest = latest
        self.calls = []
        self.change = None

    def clock(self):
        return self.latest + STEP + archive.CLOSE_GRACE_MS

    def fetch(self, route, params):
        self.calls.append((route, params))
        if route == "time":
            return {"serverTime": self.clock()}
        rows = [raw(params["startTime"] + i * STEP) for i in range(params["limit"])]
        if self.change:
            rows = self.change(rows)
        return rows


class BinanceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite"
        archive.create(self.path, "BTCUSDT", "1m", START, 2)
        self.feed = Feed(START + 4 * STEP)

    def run_sync(self, **kwargs):
        return archive.sync_once(self.path, fetch=self.feed.fetch, clock=self.feed.clock, **kwargs)

    def state(self):
        return archive.status(self.path, clock=self.feed.clock)

    def test_bounded_multi_page_recovery_and_restart_are_idempotent(self):
        self.assertEqual(self.run_sync(pages=1, page_size=2), "catching_up")
        first = self.state()
        self.assertEqual(first["count"], 2)
        self.assertFalse(first["capture_current"])
        self.assertEqual(self.run_sync(pages=2, page_size=2), "caught_up")
        second = self.state()
        self.assertEqual(second["count"], 5)
        self.assertTrue(second["capture_current"])
        self.assertEqual(self.run_sync(), "caught_up")
        self.assertEqual(second, self.state())
        self.feed.latest += STEP
        self.assertEqual(self.run_sync(), "caught_up")
        self.assertEqual(self.state()["count"], 6)

    def test_overlap_conflict_latches_and_no_later_request_is_made(self):
        self.run_sync()
        before = self.state()
        self.feed.latest += STEP
        self.feed.change = lambda rows: [raw(rows[0][0], "101"), *rows[1:]]
        self.assertEqual(self.run_sync(), "halted")
        after = self.state()
        self.assertEqual(after["reason"], "saved_overlap_conflict")
        self.assertEqual(after["head_sha256"], before["head_sha256"])
        calls = len(self.feed.calls)
        self.assertEqual(self.run_sync(), "halted")
        self.assertEqual(len(self.feed.calls), calls)
        self.feed.change = None
        archive.resume(self.path, before["head_sha256"])
        self.assertEqual(self.run_sync(), "caught_up")
        self.assertEqual(self.state()["count"], 6)

    def test_bad_second_page_does_not_commit_first_page(self):
        calls = 0

        def change(rows):
            nonlocal calls
            calls += 1
            return rows if calls == 1 else rows[:-1]

        self.feed.change = change
        before = self.state()
        self.assertEqual(self.run_sync(pages=3, page_size=2), "halted")
        after = self.state()
        self.assertEqual(after["count"], 0)
        self.assertEqual(after["head_sha256"], before["head_sha256"])
        self.assertEqual(after["reason"], "incomplete_page")

    def test_failure_budget_is_persistent_and_stop_is_sticky(self):
        def failure(*args):
            raise archive.ArchiveError("transport_failure", permanent=False)

        for expected in ("degraded", "halted"):
            self.assertEqual(
                archive.sync_once(self.path, fetch=failure, clock=self.feed.clock), expected
            )
        current = self.state()
        self.assertEqual(current["consecutive_failures"], 2)
        archive.resume(self.path, current["head_sha256"])
        stop = Path(f"{self.path}.STOP")
        stop.touch()
        calls = len(self.feed.calls)
        self.assertEqual(self.run_sync(), "halted")
        self.assertEqual(len(self.feed.calls), calls)
        stop.unlink()
        self.assertEqual(self.run_sync(), "halted")

    def test_clock_skew_and_future_bar_are_refused(self):
        self.assertEqual(
            archive.sync_once(self.path, fetch=self.feed.fetch, clock=lambda: START),
            "halted",
        )
        self.assertEqual(self.state()["reason"], "clock_skew_or_regression")
        archive.resume(self.path, self.state()["head_sha256"])
        self.feed.change = lambda rows: [raw(rows[0][0] + STEP), *rows[1:]]
        self.assertEqual(self.run_sync(), "halted")
        self.assertEqual(self.state()["count"], 0)

    def test_saved_row_tampering_and_external_head_mismatch_refused(self):
        self.run_sync()
        with self.assertRaises(archive.ArchiveError):
            archive.status(self.path, expected_head="0" * 64)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE bars SET close='99', low='99' WHERE open_ms=?", (START,))
        calls = len(self.feed.calls)
        with self.assertRaises(archive.ArchiveError):
            self.run_sync()
        self.assertEqual(len(self.feed.calls), calls)
        with self.assertRaises(archive.ArchiveError):
            self.state()

    def test_backup_is_consistent_and_never_overwrites(self):
        self.run_sync()
        target = Path(self.temp.name) / "backup.sqlite"
        result = archive.backup(self.path, target)
        self.assertEqual(result["head_sha256"], self.state()["head_sha256"])
        self.assertEqual(archive.export_window(self.path, 3), archive.export_window(target, 3))
        with self.assertRaises(FileExistsError):
            archive.backup(self.path, target)
        with self.assertRaises(FileExistsError):
            archive.create(self.path, "BTCUSDT", "1m", START)

    def test_daily_backup_is_immutable_across_same_day_and_recovers_next_day(self):
        self.run_sync()
        target = Path(self.temp.name) / "backups"
        target.mkdir()
        first = archive.daily_backup(self.path, target, clock=self.feed.clock)
        self.feed.latest += STEP
        self.run_sync()
        second = archive.daily_backup(self.path, target, clock=self.feed.clock)
        self.assertEqual(first, second)
        self.assertEqual(len(list(target.glob("*.sqlite"))), 1)
        self.feed.latest += 86_400_000
        self.assertEqual(self.run_sync(pages=2), "caught_up")
        third = archive.daily_backup(self.path, target, clock=self.feed.clock)
        self.assertNotEqual(first["backup_path"], third["backup_path"])
        self.assertEqual(len(list(target.glob("*.sqlite"))), 2)
        self.assertEqual(third["head_sha256"], self.state()["head_sha256"])

    def test_backup_refuses_reserved_sidecar_paths(self):
        self.run_sync()
        for suffix in (".STOP", ".owner", "-wal", "-shm", "-journal"):
            with self.assertRaises(archive.ArchiveError):
                archive.backup(self.path, Path(f"{self.path}{suffix}"))
        self.assertFalse(Path(f"{self.path}.STOP").exists())
        target = Path(self.temp.name) / "new.sqlite"
        Path(f"{target}.STOP").touch()
        with self.assertRaises(archive.ArchiveError):
            archive.backup(self.path, target)
        self.assertFalse(target.exists())

    def test_export_preserves_exact_units_and_rejects_wrong_template(self):
        self.run_sync()
        template = {
            "version": 1,
            "market": {"base_asset": "BTC"},
            "account": {"quote_asset": "USDT"},
            "timeframe": "1m",
            "bars": [],
        }
        data = archive.export_window(self.path, 3, template=template)
        self.assertEqual(data["bars"][0]["open_ms"], START + 2 * STEP)
        self.assertEqual(data["bars"][0]["open"], "100")
        self.assertEqual(data["bars"][0]["volume"], "3")
        self.assertEqual(template["bars"], [])
        with self.assertRaises(archive.ArchiveError):
            archive.export_window(self.path, 3, template={**template, "timeframe": "5m"})
        with self.assertRaises(archive.ArchiveError):
            archive.export_window(self.path, 6)

    def forward_template(self):
        return {
            "version": 1,
            "market": {"base_asset": "BTC"},
            "account": {"quote_asset": "USDT"},
            "timeframe": "1m",
            "bars": [],
        }

    def test_forward_export_keeps_fixed_start_and_source_identity_as_archive_grows(self):
        self.run_sync()
        template = self.forward_template()
        start = START + STEP
        first = archive.export_forward(self.path, start, template, clock=self.feed.clock)
        self.assertEqual(first["source"], archive.SOURCE)
        self.assertEqual(first["archive_head_sha256"], self.state()["head_sha256"])
        self.assertEqual(first["scenario"]["bars"][0]["open_ms"], start)
        self.assertEqual(len(first["scenario"]["bars"]), 4)
        self.feed.latest += STEP
        self.run_sync()
        second = archive.export_forward(self.path, start, template, clock=self.feed.clock)
        self.assertEqual(second["source_id"], first["source_id"])
        self.assertNotEqual(second["archive_head_sha256"], first["archive_head_sha256"])
        self.assertEqual(second["scenario"]["bars"][:-1], first["scenario"]["bars"])
        self.assertEqual(template["bars"], [])
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(
                self.path,
                start,
                template,
                clock=self.feed.clock,
                expected_head=first["archive_head_sha256"],
            )

    def test_forward_export_rejects_noncurrent_or_oversize_history_without_sliding(self):
        self.run_sync(pages=1, page_size=2)
        template = self.forward_template()
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(self.path, START, template, clock=self.feed.clock)
        self.run_sync()
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(
                self.path, START, template, clock=lambda: self.feed.clock() + 4 * STEP
            )
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(self.path, START + 1, template, clock=self.feed.clock)
        stop = Path(f"{self.path}.STOP")
        stop.touch()
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(self.path, START, template, clock=self.feed.clock)
        stop.unlink()
        self.feed.latest = START + 240 * STEP
        self.run_sync()
        with self.assertRaises(archive.ArchiveError):
            archive.export_forward(self.path, START, template, clock=self.feed.clock)
        result = archive.export_forward(self.path, START + STEP, template, clock=self.feed.clock)
        self.assertEqual(len(result["scenario"]["bars"]), 240)

    def test_owner_lock_and_stale_capture(self):
        self.run_sync()
        with archive.owner(self.path):
            with self.assertRaises(archive.ArchiveError):
                with archive.owner(self.path):
                    self.fail("duplicate owner")
        stale = archive.status(self.path, clock=lambda: self.feed.clock() + 4 * STEP)
        self.assertFalse(stale["capture_current"])

    def test_decimal_and_page_shape_are_exact(self):
        self.assertEqual(archive.decimal_text("0001.2300"), "1.23")
        for value in ("-1", "NaN", "1e2", ".1", " 1", "1."):
            with self.assertRaises(archive.ArchiveError):
                archive.decimal_text(value)
        for rows in ([raw(START)[:-1]], [raw(START + STEP)], []):
            with self.assertRaises(archive.ArchiveError):
                archive.parse_page(rows, START, 1, STEP)

    def test_rate_limit_and_redirect_errors_never_retry(self):
        from urllib.error import HTTPError

        for code in (301, 418, 429, 503):
            with patch("tools.binance_public_archive.build_opener") as builder:
                opener = builder.return_value
                opener.open.side_effect = HTTPError("https://fixture.invalid", code, "", {}, None)
                with self.assertRaises(archive.ArchiveError) as caught:
                    archive.public_get("time", {})
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(caught.exception.permanent, code != 503)
                self.assertEqual(
                    opener.open.call_args.args[0].full_url, f"{archive.HOST}/api/v3/time"
                )
        with self.assertRaises(archive.ArchiveError):
            archive.public_get("order", {})

    def test_cli_status_is_machine_readable_without_network(self):
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            result = archive.main(["status", str(self.path)])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(output.getvalue())["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
