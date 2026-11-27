"""Offline recovery bundle tests, with real SQLite and an injected audit runner."""

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools import binance_paper_forward_supervisor as supervisor
from tools import binance_paper_recovery as recovery
from tools import binance_public_archive as archive

STEP = 60_000
START = 1_700_000_040_000


class RecoveryBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "archive.sqlite"
        self.accounts = self.root / "accounts"
        self.accounts.mkdir()
        self.template = self.root / "template.json"
        self.template.write_text(
            json.dumps(
                {
                    "version": 1,
                    "timeframe": "1m",
                    "market": {"base_asset": "BTC"},
                    "account": {"quote_asset": "USDT"},
                    "strategies": [{"id": "ma"}],
                    "bars": [],
                }
            ),
            encoding="utf-8",
        )
        self.binary = self.root / "fixture-binary"
        self.binary.touch(mode=0o700)
        archive.create(self.path, "BTCUSDT", "1m", START)
        self.latest = START + STEP
        self.sync()
        self.session = supervisor.make_session(
            self.path, self.accounts, self.binary, self.template, START, 3
        )
        self.envelope = archive.export_forward(
            self.path, START, self.session.template, clock=self.clock
        )
        self.checkpoint = {
            "ledger_id": f"forward-{'a' * 64}-ma",
            "head": {"sequence": 5, "hash": "b" * 64},
        }
        self.report = {
            "mode": "offline_paper_forward",
            "live_orders_enabled": False,
            "source": self.envelope["source"],
            "source_id": self.envelope["source_id"],
            "archive_head_sha256": self.envelope["archive_head_sha256"],
            "input_bar_count": 2,
            "session_sha256": "a" * 64,
            "replay": {"results": [{"id": "ma"}]},
            "accounts": [
                {
                    "strategy_id": "ma",
                    "checkpoint": self.checkpoint,
                    "remaining_event_capacity": 2043,
                }
            ],
        }
        self.account_path = self.accounts / "forward-ma.sqlite"
        with sqlite3.connect(self.account_path) as db:
            db.execute("CREATE TABLE paper_meta (ledger_id TEXT, head_seq INT, head_hash TEXT)")
            db.execute(
                "INSERT INTO paper_meta VALUES (?, 5, ?)",
                (self.checkpoint["ledger_id"], self.checkpoint["head"]["hash"]),
            )
        supervisor.publish(
            self.session,
            supervisor.new_state(self.session),
            "paper_current",
            None,
            self.clock,
            last_success_ms=self.clock(),
            last_report=self.report,
        )
        self.audits = 0

    def clock(self):
        return self.latest + STEP + archive.CLOSE_GRACE_MS

    def fetch(self, route, params):
        if route == "time":
            return {"serverTime": self.clock()}
        return [
            [
                stamp,
                "100",
                "100",
                "100",
                "100",
                "10",
                stamp + STEP - 1,
                "0",
                1,
                "0",
                "0",
                "0",
            ]
            for stamp in range(params["startTime"], params["endTime"] + 1, STEP)
        ]

    def sync(self):
        self.assertEqual(
            archive.sync_once(self.path, fetch=self.fetch, clock=self.clock), "caught_up"
        )

    def audit_runner(self, session, envelope, initialize, *, audit_only=False):
        self.assertFalse(initialize)
        self.assertTrue(audit_only)
        self.assertEqual(envelope["trusted_checkpoints"], [self.checkpoint])
        self.assertEqual(envelope["scenario"]["bars"], self.envelope["scenario"]["bars"])
        with archive.connection(session.directory / "forward-ma.sqlite", readonly=True) as db:
            row = db.execute("SELECT * FROM paper_meta").fetchone()
            if row["head_hash"] != self.checkpoint["head"]["hash"]:
                raise supervisor.SupervisorError("fixture_account_mismatch")
        self.audits += 1
        return self.report

    def bundle(self):
        path = self.root / "backup.zip"
        result = recovery.capture(self.session, path, runner=self.audit_runner)
        return path, result

    def test_capture_newer_archive_and_restore_stopped_with_same_report(self):
        self.latest += STEP
        self.sync()
        bundle, receipt = self.bundle()
        self.assertNotEqual(receipt["archive_head_sha256"], self.report["archive_head_sha256"])
        verified = recovery.verify(
            bundle, receipt["bundle_sha256"], self.binary, runner=self.audit_runner
        )
        self.assertEqual(verified["report_sha256"], receipt["report_sha256"])
        destination = self.root / "candidate"
        result = recovery.restore(
            bundle,
            receipt["bundle_sha256"],
            self.binary,
            destination,
            runner=self.audit_runner,
            clock=self.clock,
        )
        self.assertTrue(result["operator_review_required"])
        restored = recovery.session_at(destination, self.binary, START, 3)
        state = supervisor.read_state(restored)
        self.assertEqual(state["status"], "halted")
        self.assertEqual(state["last_report"], self.report)
        self.assertTrue(restored.stop_path.exists())
        self.assertTrue(archive.stop_requested(restored.archive_path))
        self.assertTrue((destination / "REVIEW.json").exists())
        self.assertEqual(self.audits, 4)

    def test_tampering_or_wrong_digest_never_creates_destination(self):
        bundle, receipt = self.bundle()
        with bundle.open("ab") as handle:
            handle.write(b"tampered")
        destination = self.root / "candidate"
        with self.assertRaises(supervisor.SupervisorError):
            recovery.restore(
                bundle,
                receipt["bundle_sha256"],
                self.binary,
                destination,
                runner=self.audit_runner,
            )
        self.assertFalse(destination.exists())

    def test_no_overwrite_or_concurrent_session_backup(self):
        bundle, receipt = self.bundle()
        with self.assertRaises(FileExistsError):
            recovery.capture(self.session, bundle, runner=self.audit_runner)
        self.assertEqual(
            recovery.sha_file(bundle, recovery.MAX_BUNDLE)["sha256"], receipt["bundle_sha256"]
        )
        with supervisor.owner(self.session):
            with self.assertRaises(supervisor.SupervisorError):
                recovery.capture(self.session, self.root / "other.zip", runner=self.audit_runner)
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaises(supervisor.SupervisorError):
            recovery.restore(
                bundle, receipt["bundle_sha256"], self.binary, destination, runner=self.audit_runner
            )
        self.assertEqual(list(destination.iterdir()), [])

    def test_bad_account_and_missing_history_prevent_publication(self):
        with sqlite3.connect(self.account_path) as db:
            db.execute("UPDATE paper_meta SET head_hash='bad'")
        path = self.root / "bad.zip"
        with self.assertRaises(supervisor.SupervisorError):
            recovery.capture(self.session, path, runner=self.audit_runner)
        self.assertFalse(path.exists())
        self.account_path.unlink()
        with self.assertRaises(OSError):
            recovery.capture(self.session, path, runner=self.audit_runner)
        self.assertFalse(path.exists())

    def test_review_resume_requires_external_report_digest_and_removed_stop(self):
        bundle, receipt = self.bundle()
        destination = self.root / "candidate"
        recovery.restore(
            bundle,
            receipt["bundle_sha256"],
            self.binary,
            destination,
            runner=self.audit_runner,
            clock=self.clock,
        )
        restored = recovery.session_at(destination, self.binary, START, 3)
        with self.assertRaises(supervisor.SupervisorError):
            recovery.review_resume(restored, receipt["report_sha256"], runner=self.audit_runner)
        restored.stop_path.unlink()
        Path(f"{restored.archive_path}.STOP").unlink()
        with self.assertRaises(supervisor.SupervisorError):
            recovery.review_resume(restored, "0" * 64, runner=self.audit_runner)
        reviewed = recovery.review_resume(
            restored, receipt["report_sha256"], runner=self.audit_runner, clock=self.clock
        )
        self.assertEqual(reviewed["status"], "stopped")
        self.assertEqual(reviewed["last_report"], self.report)

    def test_daily_backup_requires_separate_receipt_and_is_immutable(self):
        backups = self.root / "backups"
        anchors = self.root / "checkpoints"
        backups.mkdir()
        anchors.mkdir()
        with supervisor.owner(self.session):
            with self.assertRaises(supervisor.SupervisorError):
                recovery.daily_backup_locked(
                    self.session, backups, backups, runner=self.audit_runner
                )
            first = recovery.daily_backup_locked(
                self.session, backups, anchors, runner=self.audit_runner
            )
            second = recovery.daily_backup_locked(
                self.session, backups, anchors, runner=self.audit_runner
            )
            self.assertEqual(first, second)
            self.assertEqual(len(list(backups.iterdir())), 1)
            receipt = next(anchors.iterdir())
            receipt.unlink()
            with self.assertRaises(supervisor.SupervisorError):
                recovery.daily_backup_locked(
                    self.session, backups, anchors, runner=self.audit_runner
                )
        self.assertEqual(len(list(backups.iterdir())), 1)

    def test_extra_zip_member_is_refused_even_with_matching_outer_digest(self):
        bundle, _ = self.bundle()
        with zipfile.ZipFile(bundle, "a") as handle:
            handle.writestr("../unexpected.txt", b"unexpected")
        digest = recovery.sha_file(bundle, recovery.MAX_BUNDLE)["sha256"]
        with self.assertRaises(supervisor.SupervisorError):
            recovery.verify(bundle, digest, self.binary, runner=self.audit_runner)
        self.assertFalse((self.root / "unexpected.txt").exists())


if __name__ == "__main__":
    unittest.main()
