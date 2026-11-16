"""Synthetic PAPER monitoring drill must remain isolated, deterministic and honest."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "tools" / "paper_monitor_drill.py"
SPEC = importlib.util.spec_from_file_location("paper_monitor_drill", TOOL)
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


class DrillTests(unittest.TestCase):
    def test_synthetic_snapshots_consistent(self):
        normal = drill._snapshot()
        failure = drill._snapshot(True)
        self.assertEqual(normal["status"], "healthy")
        self.assertEqual(normal["exit_code"], 0)
        self.assertEqual(failure["status"], "attention_required")
        self.assertEqual(failure["exit_code"], 1)
        self.assertFalse(failure["checks"]["capture"]["healthy"])

    def test_new_private_file_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = drill._write_snapshot(root, "capture.json", drill._snapshot())
            self.assertEqual(created.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                drill._write_snapshot(root, "capture.json", drill._snapshot(True))

    def test_reject_invalid_child_json(self):
        fake = subprocess.CompletedProcess([], 0, b"invalid", b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_unexpected_child_exit(self):
        fake = subprocess.CompletedProcess([], 1, b'{"exit_code":1}', b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_wrong_source_even_with_success_exit(self):
        bad = {
            "schema_version": 1, "source": "unrelated_tool", "exit_code": 0,
            "boundaries": {"production_ready": False},
        }
        fake = subprocess.CompletedProcess([], 0, json.dumps(bad).encode(), b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_false_ready_result(self):
        bad = {
            "schema_version": 1, "source": "paper_monitor_events", "exit_code": 0,
            "boundaries": {"production_ready": True},
        }
        fake = subprocess.CompletedProcess([], 0, json.dumps(bad).encode(), b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_duplicate_child_json_keys(self):
        raw = (b'{"schema_version":1,"source":"paper_monitor_events",'
               b'"exit_code":0,"exit_code":0,'
               b'"boundaries":{"production_ready":false}}')
        fake = subprocess.CompletedProcess([], 0, raw, b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_nested_duplicate_child_json_keys(self):
        raw = (b'{"schema_version":1,"source":"paper_monitor_events",'
               b'"exit_code":0,"boundaries":{'
               b'"production_ready":false,"production_ready":false}}')
        fake = subprocess.CompletedProcess([], 0, raw, b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_nonfinite_child_json_number(self):
        raw = (b'{"schema_version":1,"source":"paper_monitor_events",'
               b'"exit_code":0,"unexpected":NaN,'
               b'"boundaries":{"production_ready":false}}')
        fake = subprocess.CompletedProcess([], 0, raw, b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_reject_excessive_child_output(self):
        fake = subprocess.CompletedProcess([], 0, b"x" * 16385, b"")
        with mock.patch.object(drill.subprocess, "run", return_value=fake):
            with self.assertRaises(drill.DrillError):
                drill._call("paper_monitor_events.py", [], 0)

    def test_invalid_arguments_do_not_start_drill(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(TOOL),
                                 "--unknown"], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 2)
        body = json.loads(result.stdout)
        self.assertFalse(body["boundaries"]["broker_orders"])
        self.assertFalse(body["boundaries"]["production_ready"])

    def test_real_end_to_end_offline_acceptance(self):
        # No fakes: exercise v52 record, v53 sync/ack and v55 gate directly.
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", str(TOOL)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "verified_synthetic")
        self.assertEqual(report["events_verified"], 3)
        self.assertEqual(report["stages"], [
            "healthy_observed", "fault_detected", "recovery_unacknowledged",
            "operator_acknowledged", "journal_consistent"])
        for field in ("provider_requests", "broker_orders", "service_activated",
                      "external_notification_delivered", "production_ready"):
            self.assertFalse(report["boundaries"][field])


if __name__ == "__main__":
    unittest.main()
