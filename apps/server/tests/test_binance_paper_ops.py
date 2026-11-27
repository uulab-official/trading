"""Local dashboard status and exact integer reporting tests; no network."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools import binance_paper_ops as ops
from tools import binance_public_archive as archive


class OperatorDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.session = SimpleNamespace(
            archive_path=directory / "archive.sqlite",
            directory=directory,
            start_ms=60_000,
            stop_path=directory / "STOP",
            template={
                "market": {"base_asset": "BTC"},
                "account": {"quote_asset": "USDT"},
                "quote_scale": 2,
                "strategies": [{"id": "ma"}],
            },
        )
        self.identity = {"symbol": "BTCUSDT", "interval": "1m"}
        self.now = 200_000
        self.capture = {
            "identity": self.identity,
            "status": "caught_up",
            "capture_current": True,
            "count": 2,
            "last_open_ms": 120_000,
            "last_success_ms": self.now,
            "head_sha256": "a" * 64,
            "consecutive_failures": 0,
        }
        self.checkpoint = {
            "ledger_id": f"forward-{'b' * 64}-ma",
            "head": {"sequence": 5, "hash": "c" * 64},
        }
        self.report = {
            "mode": "offline_paper_forward",
            "live_orders_enabled": False,
            "source": archive.SOURCE,
            "source_id": archive.digest("paper-forward-source-v1", self.identity),
            "archive_head_sha256": self.capture["head_sha256"],
            "session_sha256": "b" * 64,
            "input_bar_count": 2,
            "max_bar_count": 2048,
            "max_event_count": 16384,
            "replay": {
                "mode": "offline_paper_forward_reconstruction",
                "scenario_sha256": "d" * 64,
                "evaluation_end_ms": 179_999,
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "quote_scale": 2,
                "results": [
                    {
                        "id": "ma",
                        "initial_cash_atoms": 10_000,
                        "final_equity_atoms": 10_123,
                        "final_cash_atoms": 9_000,
                        "realized_pnl_atoms": -10,
                        "unrealized_pnl_atoms": 133,
                        "return_bps": 123,
                        "max_sampled_drawdown_bps": 250,
                        "fees_atoms": 5,
                        "fills": 2,
                        "remaining_lots": 3,
                        "buys_halted": False,
                        "journal_events": 5,
                    }
                ],
            },
            "accounts": [
                {
                    "strategy_id": "ma",
                    "checkpoint": self.checkpoint,
                    "remaining_event_capacity": 16379,
                }
            ],
        }
        self.state = {
            "status": "paper_current",
            "reason": None,
            "last_success_ms": self.now,
            "updated_at_ms": self.now,
            "consecutive_failures": 0,
            "last_report": self.report,
        }
        self.path = directory / "forward-ma.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE paper_meta (ledger_id TEXT, head_seq INTEGER, "
                "head_hash TEXT, schema_version INTEGER)"
            )
            db.execute(
                "INSERT INTO paper_meta VALUES (?, ?, ?, 1)",
                (self.checkpoint["ledger_id"], 5, "c" * 64),
            )
            db.execute("CREATE TABLE paper_events (seq INTEGER PRIMARY KEY)")
            db.executemany("INSERT INTO paper_events VALUES (?)", [(i,) for i in range(1, 6)])

    def snapshot(self, **kwargs):
        with (
            patch.object(ops.archive, "status", return_value=self.capture),
            patch.object(ops.supervisor, "read_state", return_value=self.state),
        ):
            return ops.collect(self.session, clock=lambda: self.now, **kwargs)

    def test_current_head_match_and_exact_decimal_display(self):
        data = self.snapshot()
        self.assertTrue(data["ready"])
        self.assertEqual(data["status"], "reported_current")
        self.assertEqual(data["verification"], "persisted_head_match_only")
        self.assertFalse(data["live_orders_enabled"])
        self.assertEqual(data["strategies"][0]["equity"], "101.23")
        self.assertEqual(data["strategies"][0]["realized_pnl"], "-0.10")
        self.assertEqual(data["strategies"][0]["return_pct"], "1.23%")
        self.assertEqual(ops.format_atoms(2**64 - 1, 18), "18.446744073709551615")
        page = ops.page(data)
        self.assertIn("모의매매 운영 센터", page)
        self.assertIn("101.23", page)
        self.assertNotIn("<script", page)

    def test_status_distinguishes_stale_archive_report_lag_risk_and_halt(self):
        self.capture["capture_current"] = False
        self.assertEqual(self.snapshot()["status"], "archive_not_current")
        self.capture["capture_current"] = True
        self.capture["head_sha256"] = "e" * 64
        self.assertEqual(self.snapshot()["status"], "awaiting_paper_update")
        self.capture["head_sha256"] = "a" * 64
        self.state["last_success_ms"] = self.now - 190_000
        self.assertEqual(self.snapshot()["status"], "report_stale")
        self.state["last_success_ms"] = self.now
        self.report["replay"]["results"][0]["buys_halted"] = True
        self.assertEqual(self.snapshot()["status"], "risk_blocked")
        self.session.stop_path.touch()
        self.assertEqual(self.snapshot()["status"], "halted")
        self.assertFalse(self.snapshot()["ready"])

    def test_missing_or_changed_ledger_refuses_success_without_repair(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE paper_meta SET head_hash=?", ("0" * 64,))
        self.assertEqual(self.snapshot()["status"], "audit_or_state_refused")
        self.path.unlink()
        self.assertFalse(self.snapshot()["ready"])
        self.assertFalse(self.path.exists())

    def test_bad_or_inconsistent_reports_are_not_displayed(self):
        self.report["replay"]["results"][0]["final_equity_atoms"] += 1
        data = self.snapshot()
        self.assertEqual(data["strategies"], [])
        self.assertFalse(data["ready"])
        self.report["replay"]["results"][0]["final_equity_atoms"] -= 1
        self.report["live_orders_enabled"] = True
        self.assertEqual(self.snapshot()["status"], "audit_or_state_refused")

    def test_page_escapes_text_and_does_not_offer_order_controls(self):
        data = self.snapshot()
        data["status"] = "<script>alert(1)</script>"
        markup = ops.page(data, refresh=True)
        self.assertIn("&lt;script&gt;", markup)
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<form", markup)
        self.assertIn('content="15"', markup)

    def test_default_status_never_invokes_binary(self):
        with patch.object(ops.supervisor, "invoke_paper_binary") as invoke:
            self.snapshot()
        invoke.assert_not_called()

    def test_deep_audit_uses_read_only_command_and_exact_checkpoints(self):
        envelope = {
            "source": self.report["source"],
            "source_id": self.report["source_id"],
            "archive_head_sha256": self.report["archive_head_sha256"],
            "scenario": {"bars": [{}, {}], "strategies": [{"id": "ma"}]},
        }
        with (
            patch.object(ops.archive, "export_forward", return_value=envelope),
            patch.object(ops.supervisor, "invoke_paper_binary", return_value=self.report) as invoke,
        ):
            data = self.snapshot(deep_audit=True)
        self.assertEqual(data["verification"], "independently_replayed_at_request")
        self.assertTrue(invoke.call_args.kwargs["audit_only"])
        self.assertEqual(envelope["trusted_checkpoints"], [self.checkpoint])


if __name__ == "__main__":
    unittest.main()
