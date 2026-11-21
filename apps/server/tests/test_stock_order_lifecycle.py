"""Actual offline CLI and synthetic stock-order lifecycle integration."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
JOURNAL = ROOT / "tools/stock_order_journal.py"
SMOKE = ROOT / "tools/stock_order_lifecycle_smoke.py"
FIXTURE = ROOT / "config/examples/synthetic_stock_order_events.json"
GUARD = """
import runpy, socket, sys
def refused(*args, **kwargs):
    raise AssertionError('Offline lifecycle forbids network and DNS')
socket.socket.connect = refused
socket.create_connection = refused
socket.getaddrinfo = refused
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class StockOrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "orders.sqlite"

    def run_cli(self, script, arguments=()):
        self.assertTrue(script.is_file(), "Expected implemented offline entrypoint")
        environment = dict(os.environ)
        environment.update(
            {
                "KIS_VTS_ACCESS_TOKEN": "EXAMPLE-SECRET-TOKEN-NEVER-PRINT",
                "KIS_VTS_APPKEY": "EXAMPLE-SECRET-APPKEY-NEVER-PRINT",
                "KIS_VTS_APPSECRET": "EXAMPLE-SECRET-APPSECRET-NEVER-PRINT",
                "KIS_VTS_CANO": "12345678",
                "KIS_VTS_ACNT_PRDT_CD": "01",
                "HTTPS_PROXY": "https://EXAMPLE-SECRET-PROXY.invalid",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", GUARD, str(script), *arguments],
            cwd=self.directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("EXAMPLE-SECRET", result.stdout + result.stderr)
        self.assertNotIn("12345678", result.stdout + result.stderr)
        return result

    def journal_cli(self, command, *extra, account="demo-stock-orders", database=None):
        return self.run_cli(
            JOURNAL,
            [
                "--mode",
                "paper",
                command,
                "--database",
                str(database or self.database),
                "--account-ref",
                account,
                *extra,
            ],
        )

    def event_file(self, index):
        events = json.loads(FIXTURE.read_text())["events"]
        path = self.directory / ("event-" + str(index) + ".json")
        path.write_text(json.dumps(events[index]))
        return path

    def test_default_smoke_restarts_partial_fills_cancel_races_and_duplicates(self):
        result = self.run_cli(SMOKE)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("synthetic_stock_order_lifecycle_verified", value["status"])
        self.assertEqual("paper", value["mode"])
        self.assertFalse(value["live_orders_enabled"])
        self.assertEqual(14, value["recorded_events"])
        self.assertEqual(4, value["executions"])
        self.assertEqual("5285", value["cash_delta_krw"])
        self.assertEqual({}, value["position_deltas"])
        self.assertEqual(
            {"stock-buy-001": "canceled", "stock-sell-001": "filled"}, value["order_states"]
        )
        self.assertTrue(all(value["checks"].values()))
        self.assertEqual(0, value["actual_broker_requests"])
        self.assertFalse(value["artifacts_retained"])
        self.assertEqual([], list(self.directory.iterdir()))

    def test_retained_demo_is_private_and_readonly_auditable(self):
        output = self.directory / "demo"
        result = self.run_cli(SMOKE, ["--output-dir", str(output)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)["artifacts_retained"])
        report = json.loads((output / "report.json").read_text())
        snapshot = report["snapshot"]
        self.assertEqual(14, len(report["trace"]))
        self.assertFalse(snapshot["blocked"])
        self.assertEqual(5, snapshot["orders"][0]["filled_quantity"])
        self.assertEqual(5, snapshot["orders"][0]["unfilled_quantity"])
        self.assertEqual(0, snapshot["orders"][0]["working_quantity"])
        self.assertEqual("49700", snapshot["orders"][0]["gross_krw"])
        self.assertEqual("50", snapshot["orders"][0]["fee_krw"])
        self.assertEqual("110", snapshot["orders"][1]["tax_krw"])
        self.assertEqual(0o700, stat.S_IMODE(output.stat().st_mode))
        for name in ("report.json", "journal.sqlite"):
            self.assertEqual(0o600, stat.S_IMODE((output / name).stat().st_mode))
            self.assertNotIn(b"EXAMPLE-SECRET", (output / name).read_bytes())
        audited = self.journal_cli("status", database=output / "journal.sqlite")
        self.assertEqual(0, audited.returncode, audited.stderr)
        self.assertEqual(snapshot, json.loads(audited.stdout))

    def test_demo_preserves_existing_directory_file_and_dangling_symlink(self):
        output = self.directory / "existing"
        output.mkdir()
        sentinel = output / "sentinel.txt"
        sentinel.write_text("keep")
        regular = self.directory / "existing-file"
        regular.write_text("keep")
        dangling = self.directory / "dangling"
        destination = self.directory / "missing-target"
        dangling.symlink_to(destination)
        for path in (output, regular, dangling):
            with self.subTest(path=path.name):
                result = self.run_cli(SMOKE, ["--output-dir", str(path)])
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
        self.assertEqual("keep", sentinel.read_text())
        self.assertEqual("keep", regular.read_text())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(destination.exists())

    def test_actual_journal_cli_blocks_uncertain_and_replays_same_event_once(self):
        initialized = self.journal_cli("init")
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        self.assertEqual(0, json.loads(initialized.stdout)["event_count"])
        for index in (0, 1, 1):
            result = self.journal_cli("record", "--event", str(self.event_file(index)))
            self.assertEqual(0, result.returncode, result.stderr)
        result = self.journal_cli("status")
        self.assertEqual(3, result.returncode)
        snapshot = json.loads(result.stdout)
        self.assertEqual(2, snapshot["event_count"])
        self.assertTrue(snapshot["blocked"])
        self.assertEqual(["stock-buy-001"], snapshot["pending_client_order_ids"])
        self.assertEqual("uncertain", snapshot["orders"][0]["status"])

    def test_status_never_creates_missing_database_and_wrong_alias_refuses(self):
        missing = self.directory / "missing" / "orders.sqlite"
        result = self.journal_cli("status", database=missing)
        self.assertEqual(2, result.returncode)
        self.assertFalse(missing.parent.exists())
        self.assertEqual(0, self.journal_cli("init").returncode)
        result = self.journal_cli("status", account="another-account")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(0, json.loads(self.journal_cli("status").stdout)["event_count"])

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_oversized_input(self):
        self.assertEqual(0, self.journal_cli("init").returncode)
        invalid = self.directory / "invalid.json"
        for text in (
            '{"event_id":"a","event_id":"b"}',
            '{"quantity":NaN}',
            '{"quantity":Infinity}',
            '"' + "x" * 65536 + '"',
        ):
            with self.subTest(length=len(text)):
                invalid.write_text(text)
                result = self.journal_cli("record", "--event", str(invalid))
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
        self.assertEqual(0, json.loads(self.journal_cli("status").stdout)["event_count"])

    def test_invalid_cli_values_are_not_echoed_or_persisted(self):
        result = self.run_cli(
            JOURNAL,
            [
                "--mode",
                "EXAMPLE-SECRET-INVALID-MODE",
                "init",
                "--database",
                str(self.database),
                "--account-ref",
                "demo-stock-orders",
            ],
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertFalse(self.database.exists())

    def test_exclusive_init_preserves_existing_journal(self):
        self.assertEqual(0, self.journal_cli("init").returncode)
        before = json.loads(self.journal_cli("status").stdout)
        self.assertEqual(2, self.journal_cli("init").returncode)
        self.assertEqual(before, json.loads(self.journal_cli("status").stdout))

    def test_report_write_failure_removes_only_new_partial_file(self):
        self.assertTrue(JOURNAL.is_file(), "Expected implemented JSON output helper")
        from tools.stock_order_journal import write_new_json

        target = self.directory / "report.json"
        with patch("os.fsync", side_effect=OSError("EXAMPLE-SECRET-IO-ERROR")):
            with self.assertRaises(OSError):
                write_new_json(target, {"mode": "paper"})
        self.assertFalse(target.exists())
        target.write_text("existing evidence")
        with self.assertRaises(FileExistsError):
            write_new_json(target, {"mode": "paper"})
        self.assertEqual("existing evidence", target.read_text())

    def test_report_replacement_during_fsync_is_preserved_and_refused(self):
        from tools.stock_order_journal import write_new_json

        target = self.directory / "report.json"

        def replace_output(_descriptor):
            target.unlink()
            target.write_text("replacement evidence")

        with patch("os.fsync", side_effect=replace_output):
            with self.assertRaises(ValueError):
                write_new_json(target, {"mode": "paper"})
        self.assertEqual("replacement evidence", target.read_text())


if __name__ == "__main__":
    unittest.main()
