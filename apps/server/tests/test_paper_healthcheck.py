"""Network-free heartbeat checks with time skew, staleness and halt policy."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.paper_healthcheck import inspect, main

NOW = 1700000000


class PaperHealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "status.json"
        self.data = {
            "mode": "paper",
            "live_orders_enabled": False,
            "market": "KRW-BTC",
            "status": "healthy",
            "last_attempt_utc": datetime.fromtimestamp(NOW - 10, tz=timezone.utc).isoformat(),
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def test_recent_healthy_heartbeat_is_accepted(self):
        result = inspect(self.path, now=NOW, max_age_seconds=60)
        self.assertEqual("healthy", result["status"])
        self.assertFalse(result["operator_action_required"])

    def test_aged_heartbeat_is_stale_and_not_silent_success(self):
        result = inspect(self.path, now=NOW + 100, max_age_seconds=60)
        self.assertEqual("stale", result["status"])
        self.assertTrue(result["operator_action_required"])

    def test_halted_is_not_healthy_even_if_fresh(self):
        self.data["status"] = "halted"
        self.save()
        result = inspect(self.path, now=NOW)
        self.assertEqual("halted", result["status"])
        self.assertTrue(result["operator_action_required"])

    def test_risk_blocked_heartbeat_requires_operator_action(self):
        self.data["status"] = "risk_blocked"
        self.save()
        report = inspect(self.path, now=NOW)
        self.assertEqual("risk_blocked", report["status"])
        self.assertTrue(report["operator_action_required"])

    def test_graceful_stop_does_not_look_healthy(self):
        self.data["status"] = "stopped"
        self.save()
        result = inspect(self.path, now=NOW)
        self.assertEqual("stopped", result["status"])
        self.assertTrue(result["operator_action_required"])

    def test_future_heartbeat_and_live_marker_rejected(self):
        self.data["last_attempt_utc"] = datetime.fromtimestamp(
            NOW + 1000, tz=timezone.utc
        ).isoformat()
        self.save()
        with self.assertRaises(ValueError):
            inspect(self.path, now=NOW)
        self.data["last_attempt_utc"] = datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat()
        self.data["live_orders_enabled"] = True
        self.save()
        with self.assertRaises(ValueError):
            inspect(self.path, now=NOW)

    def test_invalid_file_does_not_crash_operator_cli(self):
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(2, main([str(self.path)]))
        self.path.unlink()
        self.assertEqual(4, main([str(self.path)]))


if __name__ == "__main__":
    unittest.main()
