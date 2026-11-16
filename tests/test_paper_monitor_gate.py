"""Read-only operational gate rejects stale snapshots and hidden pending faults."""
import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("paper_monitor_gate", TOOLS / "paper_monitor_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

BASE = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)


class GateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "private"
        self.folder.mkdir(mode=0o700)
        self.db = self.folder / gate.queue.DB_NAME
        self.db.touch(mode=0o600)
        stamp = BASE.isoformat(timespec="microseconds")
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE state(singleton INTEGER PRIMARY KEY, status TEXT,
                                   signature TEXT, observed_at TEXT, observations INTEGER);
                CREATE TABLE events(sequence INTEGER PRIMARY KEY,
                                    observed_at TEXT, status TEXT, signature TEXT);
            """)
            gate.queue._init(conn)
            conn.execute("INSERT INTO events VALUES(1,?,?,?)", (stamp, "healthy", '{"x":1}'))
            conn.execute("INSERT INTO state VALUES(1,?,?,?,1)", ("healthy", '{"x":1}', stamp))
            conn.execute("INSERT INTO alert_queue(sequence,status,signature,observed_at) VALUES(1,?,?,?)",
                         ("healthy", '{"x":1}', stamp))

    def check(self, offset=60, max_age=120):
        return gate.inspect(str(self.folder), max_age, now=BASE + timedelta(seconds=offset))

    def append(self, status, signature, *, at_seconds=60):
        timestamp = (BASE + timedelta(seconds=at_seconds)).isoformat(timespec="microseconds")
        with sqlite3.connect(self.db) as conn:
            seq = conn.execute("SELECT MAX(sequence)+1 FROM events").fetchone()[0]
            conn.execute("INSERT INTO events VALUES(?,?,?,?)",
                         (seq, timestamp, status, signature))
            conn.execute("INSERT INTO alert_queue(sequence,status,signature,observed_at) VALUES(?,?,?,?)",
                         (seq, status, signature, timestamp))
            conn.execute("UPDATE state SET status=?,signature=?,observed_at=?,observations=observations+1",
                         (status, signature, timestamp))
        return seq

    def test_fresh_healthy_snapshot(self):
        result = self.check()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["pending_acknowledgements"], 1)
        self.assertFalse(result["boundaries"]["production_ready"])

    def test_age_equal_to_limit_is_healthy(self):
        self.assertEqual(self.check(offset=120)["exit_code"], 0)

    def test_stale_snapshot_fails_closed(self):
        result = self.check(offset=121)
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("stale_observation", result["reasons"])

    def test_degraded_current_status_blocks_healthy(self):
        self.append("attention_required", '{"x":2}')
        result = self.check(offset=80)
        self.assertIn("current_degraded", result["reasons"])
        self.assertIn("unacknowledged_fault", result["reasons"])
        self.assertEqual(result["exit_code"], 1)

    def test_recovered_but_unacknowledged_older_fault_still_blocks(self):
        fault = self.append("attention_required", '{"x":2}')
        self.append("healthy", '{"x":3}', at_seconds=70)
        result = self.check(offset=80)
        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(result["unacknowledged_faults"], 1)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE alert_queue SET acknowledged_at=?,reason=? WHERE sequence=?",
                         ((BASE + timedelta(seconds=75)).isoformat(), "reviewed failure", fault))
        passed = self.check(offset=80)
        self.assertEqual(passed["exit_code"], 0)
        self.assertEqual(passed["unacknowledged_faults"], 0)

    def test_future_observation_refused(self):
        with self.assertRaises(gate.GateUnavailable):
            self.check(offset=-1)

    def test_unsynced_queue_refused(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM alert_queue")
        with self.assertRaises(gate.queue.Refused):
            self.check()

    def test_mismatched_saved_event_refused(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE events SET signature='tampered' WHERE sequence=1")
        with self.assertRaises(gate.queue.Refused):
            self.check()

    def test_missing_database_refused_without_creating(self):
        self.db.unlink()
        process = subprocess.run([sys.executable, "-I", "-S", "-B",
                                  str(TOOLS / "paper_monitor_gate.py"),
                                  "--directory", str(self.folder)],
                                 capture_output=True, text=True, timeout=8)
        self.assertEqual(process.returncode, 2)
        self.assertFalse(self.db.exists())
        self.assertEqual(json.loads(process.stdout)["status"], "unavailable")

    def test_private_directory_required(self):
        self.folder.chmod(0o755)
        with self.assertRaises(gate.queue.Refused):
            self.check()

    def test_bad_max_age_refused(self):
        for invalid in ("0", "29", "86401", "not-a-number"):
            with self.subTest(invalid=invalid), self.assertRaises(
                (gate.GateUnavailable, ValueError)
            ):
                gate._parse(["--directory", str(self.folder),
                             "--max-age-seconds", invalid])

    def test_isolated_cli_cannot_claim_unverified_future_health(self):
        process = subprocess.run([sys.executable, "-I", "-S", "-B",
                                  str(TOOLS / "paper_monitor_gate.py"),
                                  "--directory", str(self.folder)],
                                 capture_output=True, text=True, timeout=8)
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertIn("stale_observation", report["reasons"])
        self.assertFalse(report["boundaries"]["external_notification_delivered"])

    def test_special_characters_in_directory_uri(self):
        special = self.folder.parent / "private?mode=memory"
        special.mkdir(mode=0o700)
        (special / gate.queue.DB_NAME).write_bytes(self.db.read_bytes())
        (special / gate.queue.DB_NAME).chmod(0o600)
        self.assertEqual(gate.inspect(str(special), 120, now=BASE + timedelta(seconds=60))["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
