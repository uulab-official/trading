"""End-to-end synthetic paper smoke needs neither network nor credentials."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_backup import verify_backup
from tools.stock_paper_forward import audit_forward, replay_forward
from tools.stock_paper_smoke import main, run_smoke

SETTINGS = {
    "KRW": {
        "initial_cash": "1000000",
        "fee_pct": "0.1",
        "fast": 5,
        "slow": 20,
        "allocation_pct": "20",
    },
    "USD": {
        "initial_cash": "10000",
        "fee_pct": "0.1",
        "fast": 5,
        "slow": 20,
        "allocation_pct": "20",
    },
}


class SyntheticStockPaperSmokeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_synthetic_pipeline_including_backup_deep_verification(self):
        with (
            patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
            patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")),
        ):
            outcome = run_smoke()
        self.assertEqual("synthetic_stock_paper_smoke_verified", outcome["status"])
        self.assertEqual("paper", outcome["mode"])
        self.assertFalse(outcome["live_orders_enabled"])
        self.assertEqual("synthetic_not_real_market_data", outcome["fixture"])
        self.assertEqual(55, outcome["bars"])
        self.assertTrue(outcome["backup_verified"])
        self.assertTrue(outcome["static_html_verified"])
        self.assertTrue(outcome["duplicate_replay_prevented"])
        self.assertEqual("KRW", outcome["quote_currency"])
        self.assertEqual("005930", outcome["symbol"])
        self.assertEqual(SETTINGS["KRW"], outcome["settings"])
        self.assertFalse(outcome["artifacts_retained"])

    def test_default_run_removes_its_actual_temporary_artifacts(self):
        locations = []

        def temporary_directory(*args, **kwargs):
            directory = TemporaryDirectory(*args, dir=self.root, **kwargs)
            locations.append(Path(directory.name))
            return directory

        with patch("tools.stock_paper_smoke.TemporaryDirectory", temporary_directory):
            outcome = run_smoke()
        self.assertFalse(outcome["artifacts_retained"])
        self.assertEqual(1, len(locations))
        self.assertFalse(locations[0].exists())
        self.assertEqual([], list(self.root.iterdir()))

    def test_retained_currency_ledgers_reports_and_backups_are_independently_auditable(self):
        for currency, symbol in (("KRW", "005930"), ("USD", "DEMO")):
            with self.subTest(currency=currency):
                destination = self.root / currency
                with (
                    patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
                    patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")),
                ):
                    outcome = run_smoke(destination, currency=currency)
                self.assertTrue(outcome["artifacts_retained"])
                self.assertEqual(currency, outcome["quote_currency"])
                self.assertEqual(symbol, outcome["symbol"])
                self.assertEqual(SETTINGS[currency], outcome["settings"])
                self.assertEqual(
                    {
                        "candles.json",
                        "strategy.sqlite",
                        "audit.json",
                        "report.html",
                        "smoke.json",
                        "backup",
                    },
                    {item.name for item in destination.iterdir()},
                )
                fixture = json.loads((destination / "candles.json").read_text())
                self.assertEqual("synthetic_fixture", fixture["provider"])
                self.assertEqual(currency, fixture["quote_currency"])
                self.assertEqual(symbol, fixture["symbol"])
                self.assertEqual(55, len(fixture["candles"]))
                if currency == "USD":
                    self.assertEqual(
                        [1795132800000, "202.5", "202.55", "202.375", "202.425", "600"],
                        fixture["candles"][0],
                    )
                    self.assertIn("not FX", fixture["warning"])
                    self.assertIn("fictional", fixture["warning"])
                dbpath = destination / "strategy.sqlite"
                audited = audit_forward(fixture, dbpath, **SETTINGS[currency])
                self.assertEqual(currency, audited["quote_currency"])
                self.assertEqual(symbol, audited["symbol"])
                self.assertEqual(55, audited["bars"])
                self.assertGreater(audited["simulated_fills"], 0)
                self.assertEqual(outcome["simulated_fills"], audited["simulated_fills"])
                self.assertEqual(audited, json.loads((destination / "audit.json").read_text()))
                self.assertEqual(outcome, json.loads((destination / "smoke.json").read_text()))
                with closing(sqlite3.connect(dbpath)) as database:
                    metadata = dict(database.execute("SELECT key,value FROM paper_meta"))
                self.assertEqual(currency, metadata["quote_currency"])
                self.assertEqual(SETTINGS[currency]["initial_cash"], metadata["initial_cash"])
                backup = destination / "backup"
                self.assertTrue(verify_backup(backup, deep=True)["economic_replay_verified"])
                manifest = json.loads((backup / "manifest.json").read_text())
                self.assertEqual(currency, manifest["quote_currency"])
                self.assertEqual(symbol, manifest["market"])
                self.assertEqual(SETTINGS[currency], manifest["strategy_parameters"])
                self.assertEqual(
                    audited,
                    audit_forward(fixture, backup / "stock.sqlite", **SETTINGS[currency]),
                )
                page = (destination / "report.html").read_text(encoding="utf-8")
                self.assertIn("PAPER ONLY", page)
                self.assertIn(currency, page)
                self.assertIn(symbol, page)
                self.assertIn("합성 데이터", page)
                self.assertIn("Content-Security-Policy", page)
                self.assertNotIn("<script", page.lower())
                repeated = replay_forward(fixture, dbpath, **SETTINGS[currency])
                self.assertEqual(0, repeated["appended_bars"])
                self.assertEqual(0, repeated["new_simulated_fills"])
                self.assertEqual(audited, audit_forward(fixture, dbpath, **SETTINGS[currency]))

    @unittest.skipUnless(os.name == "posix", "POSIX permissions are required")
    def test_retained_artifacts_are_private(self):
        destination = self.root / "private"
        run_smoke(destination, currency="USD")
        self.assertEqual(0o700, stat.S_IMODE(destination.stat().st_mode))
        for item in destination.rglob("*"):
            self.assertEqual(
                0o700 if item.is_dir() else 0o600, stat.S_IMODE(item.stat().st_mode), str(item)
            )

    def test_existing_files_directories_and_symlinks_are_never_replaced(self):
        existing_file = self.root / "existing.json"
        existing_file.write_bytes(b"preserve-file")
        existing_directory = self.root / "existing-directory"
        existing_directory.mkdir()
        marker = existing_directory / "marker.txt"
        marker.write_bytes(b"preserve-directory")
        symlink = self.root / "existing-link"
        symlink.symlink_to(existing_directory, target_is_directory=True)
        dangling = self.root / "dangling-link"
        dangling_target = self.root / "absent-target"
        dangling.symlink_to(dangling_target, target_is_directory=True)
        before = {path.name for path in self.root.iterdir()}
        for destination in (existing_file, existing_directory, symlink, dangling):
            with (
                self.subTest(destination=destination.name),
                self.assertRaises((ValueError, OSError)),
            ):
                run_smoke(destination, currency="USD")
        self.assertEqual(b"preserve-file", existing_file.read_bytes())
        self.assertEqual(b"preserve-directory", marker.read_bytes())
        self.assertEqual({"marker.txt"}, {item.name for item in existing_directory.iterdir()})
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(dangling_target.exists())
        self.assertEqual(before, {path.name for path in self.root.iterdir()})

    def test_missing_parent_is_not_created(self):
        destination = self.root / "absent-parent" / "run"
        with self.assertRaises(OSError):
            run_smoke(destination)
        self.assertFalse(destination.parent.exists())

    def test_invalid_currency_is_rejected_before_output_creation(self):
        destination = self.root / "invalid-currency"
        for currency in ("krw", "usd", "EUR", "", None, []):
            with self.subTest(currency=currency), self.assertRaises(ValueError):
                run_smoke(destination, currency=currency)
            self.assertFalse(destination.exists())

    def test_cli_retains_usd_evidence_and_refuses_repeated_destination(self):
        destination = self.root / "cli-usd"
        command = [
            sys.executable,
            str(ROOT / "tools/stock_paper_smoke.py"),
            "--currency",
            "USD",
            "--output-dir",
            str(destination),
        ]
        first = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual("", first.stderr)
        outcome = json.loads(first.stdout)
        self.assertEqual("USD", outcome["quote_currency"])
        self.assertTrue(outcome["artifacts_retained"])
        self.assertEqual(outcome, json.loads((destination / "smoke.json").read_text()))
        self.assertTrue(
            verify_backup(destination / "backup", deep=True)["economic_replay_verified"]
        )
        saved_summary = (destination / "smoke.json").read_bytes()
        repeated = subprocess.run(
            command, cwd=self.root, capture_output=True, text=True, timeout=20
        )
        self.assertEqual(2, repeated.returncode)
        self.assertEqual("", repeated.stdout)
        self.assertNotIn(str(destination), repeated.stderr)
        self.assertEqual(saved_summary, (destination / "smoke.json").read_bytes())

    def test_cli_failure_keeps_partial_evidence_without_success_summary(self):
        destination = self.root / "partial"
        output, errors = StringIO(), StringIO()
        with (
            patch("tools.stock_paper_smoke.generate", side_effect=RuntimeError("PRIVATE-FAILURE")),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = main(["--output-dir", str(destination), "--currency", "USD"])
        self.assertEqual(2, status)
        self.assertEqual("", output.getvalue())
        self.assertNotIn("PRIVATE-FAILURE", errors.getvalue())
        self.assertTrue((destination / "strategy.sqlite").is_file())
        self.assertTrue((destination / "candles.json").is_file())
        self.assertFalse((destination / "smoke.json").exists())

    def test_cli_invalid_arguments_do_not_echo_arbitrary_values_or_create_output(self):
        destination = self.root / "invalid-cli"
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = main(["--currency", "PRIVATE-INVALID", "--output-dir", str(destination)])
        self.assertEqual(2, status)
        self.assertEqual("", output.getvalue())
        self.assertNotIn("PRIVATE-INVALID", errors.getvalue())
        self.assertFalse(destination.exists())

    def test_summary_sync_failure_does_not_leave_a_completed_run_marker(self):
        destination = self.root / "summary-sync-failure"
        completion = destination / "smoke.json"
        real_fsync = os.fsync

        def fail_completion_sync(descriptor):
            if completion.exists():
                opened, marker = os.fstat(descriptor), completion.stat()
                if (opened.st_dev, opened.st_ino) == (marker.st_dev, marker.st_ino):
                    raise OSError("PRIVATE-SYNC-FAILURE")
            return real_fsync(descriptor)

        output, errors = StringIO(), StringIO()
        with (
            patch("tools.stock_paper_smoke.os.fsync", fail_completion_sync),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = main(["--output-dir", str(destination)])
        self.assertEqual(2, status)
        self.assertEqual("", output.getvalue())
        self.assertNotIn("PRIVATE-SYNC-FAILURE", errors.getvalue())
        self.assertTrue((destination / "report.html").is_file())
        self.assertFalse(completion.exists())


if __name__ == "__main__":
    unittest.main()
