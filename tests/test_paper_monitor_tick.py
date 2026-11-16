"""PAPER tick transactional journal + queue and explicit boundary regression."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("paper_monitor_tick", TOOLS / "paper_monitor_tick.py")
tick = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tick)


def snapshot(fault=False):
    checks = {
        "service": {"healthy": True, "exit_code": 0, "status": "running"},
        "capture": {"healthy": not fault, "exit_code": 1 if fault else 0,
                    "status": "stale" if fault else "current"},
        "account": {"healthy": True, "exit_code": 0, "status": "current"},
    }
    return {"schema_version": 1, "source": "paper_monitor",
            "status": "attention_required" if fault else "healthy",
            "exit_code": 1 if fault else 0, "checks": checks}


class TickTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "private"
        self.root.mkdir(mode=0o700)
        self.args = SimpleNamespace(service="/private/service",
                                    capture="/private/capture",
                                    account="/private/account",
                                    directory=str(self.root), timeout_seconds=10)

    def inspect(self, value):
        with mock.patch.object(tick.paper_monitor, "inspect", return_value=value):
            return tick._operate(self.args)

    def test_healthy_initial_atomic_record_queue(self):
        result = self.inspect(snapshot())
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(result["pending_acknowledgements"], 1)
        self.assertEqual(result["event_sequence"], 1)
        self.assertFalse(result["boundaries"]["production_ready"])
        db = self.root / tick.paper_alert_queue.DB_NAME
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert_queue").fetchone()[0], 1)

    def test_repeated_healthy_does_not_duplicate(self):
        self.inspect(snapshot())
        result = self.inspect(snapshot())
        self.assertFalse(result["event_created"])
        self.assertEqual(result["observation_count"], 2)
        self.assertEqual(result["pending_acknowledgements"], 1)

    def test_fault_and_recovery(self):
        self.inspect(snapshot())
        fault = self.inspect(snapshot(True))
        self.assertEqual(fault["exit_code"], 1)
        self.assertEqual(fault["event_sequence"], 2)
        self.assertEqual(fault["pending_acknowledgements"], 2)
        recovered = self.inspect(snapshot())
        self.assertEqual(recovered["event_sequence"], 3)
        self.assertEqual(recovered["exit_code"], 0)

    def test_sync_failure_rolls_back_observation(self):
        self.inspect(snapshot())
        db = self.root / tick.paper_alert_queue.DB_NAME
        with mock.patch.object(tick.paper_alert_queue, "_sync",
                               side_effect=tick.paper_alert_queue.Refused("source_changed")):
            with self.assertRaises(tick.paper_alert_queue.Refused):
                self.inspect(snapshot(True))
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM state").fetchone()[0], "healthy")

    def test_missing_source_refused_without_database(self):
        bad = snapshot()
        bad["checks"].pop("capture")
        with self.assertRaises(tick.paper_monitor_events.Invalid):
            self.inspect(bad)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_false_healthy_refused(self):
        bad = snapshot(True)
        bad["status"] = "healthy"
        with self.assertRaises(tick.paper_monitor_events.Invalid):
            self.inspect(bad)

    def test_cli_requires_all_three_paths(self):
        with self.assertRaises(ValueError):
            tick._args(["--service", "/private/service", "--directory", str(self.root)])

    def test_cli_refuses_relative_directory(self):
        with self.assertRaises(ValueError):
            tick._args(["--service", "/private/service",
                        "--capture", "/private/capture",
                        "--account", "/private/account", "--directory", "relative"])

    def test_real_native_missing_store_probes_record_degraded(self):
        import subprocess
        script = TOOLS / "paper_monitor_tick.py"
        missing = self.root.parent / "missing-store"
        command = [
            sys.executable, "-I", "-S", "-B", str(script),
            "--service", str(missing / "service"),
            "--capture", str(missing / "capture"),
            "--account", str(missing / "account"),
            "--directory", str(self.root), "--timeout-seconds", "3",
        ]
        first = subprocess.run(command, capture_output=True, text=True, timeout=25)
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        observed = json.loads(first.stdout)
        self.assertEqual(observed["status"], "attention_required")
        self.assertEqual(observed["pending_acknowledgements"], 1)
        self.assertFalse(observed["boundaries"]["external_notification_delivered"])
        second = subprocess.run(command, capture_output=True, text=True, timeout=25)
        self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
        self.assertFalse(json.loads(second.stdout)["event_created"])

    def test_main_no_activation_on_invalid_arguments(self):
        with mock.patch.object(tick.paper_monitor, "inspect") as probe, \
             mock.patch("builtins.print") as emit:
            code = tick.main([])
        self.assertEqual(code, 2)
        probe.assert_not_called()
        self.assertEqual(json.loads(emit.call_args.args[0])["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
