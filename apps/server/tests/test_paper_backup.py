"""Offline paper backup integration and tamper-refusal tests."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.paper_backup import backup_pair, verify_backup, main
from trading_bot.paper_runner import PaperRunConfig, PaperRunner


class PaperBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / "archive.json"
        self.checkpoint = self.root / "paper.json"
        self.backup = self.root / "backup-001"
        fixture = json.loads((ROOT / "config/examples/offline_fixture.json").read_text())
        self.archive.write_text(json.dumps(fixture), encoding="utf-8")
        asyncio.run(PaperRunner(PaperRunConfig()).run(
            fixture["candles"], checkpoint_path=self.checkpoint))

    def test_creates_verified_pair_without_modifying_sources(self):
        previous_archive, previous_checkpoint = self.archive.read_bytes(), self.checkpoint.read_bytes()
        result = backup_pair(self.archive, self.checkpoint, self.backup)
        self.assertEqual("offline_paper_pair_backup", result["type"])
        self.assertFalse(result["live_orders_enabled"])
        self.assertEqual(previous_archive, (self.backup / "archive.json").read_bytes())
        self.assertEqual(previous_checkpoint, (self.backup / "checkpoint.json").read_bytes())
        self.assertEqual(previous_archive, self.archive.read_bytes())
        self.assertEqual(previous_checkpoint, self.checkpoint.read_bytes())
        checked = verify_backup(self.backup)
        self.assertEqual("backup_bytes_verified", checked["status"])
        self.assertFalse(checked["economic_replay_verified"])

    def test_deep_verification_replays_economic_state_from_backup(self):
        backup_pair(self.archive, self.checkpoint, self.backup)
        report = verify_backup(self.backup, deep=True)
        self.assertEqual("backup_deep_verified", report["status"])
        self.assertTrue(report["economic_replay_verified"])
        self.assertEqual(90, report["processed_bars"])

    def test_deep_cli_does_not_modify_backup_files(self):
        backup_pair(self.archive, self.checkpoint, self.backup)
        before = (self.backup / "checkpoint.json").read_bytes()
        self.assertEqual(0, main(["verify", str(self.backup), "--deep"]))
        self.assertEqual(before, (self.backup / "checkpoint.json").read_bytes())

    def test_existing_backup_cannot_be_overwritten(self):
        backup_pair(self.archive, self.checkpoint, self.backup)
        original = (self.backup / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            backup_pair(self.archive, self.checkpoint, self.backup)
        self.assertEqual(original, (self.backup / "manifest.json").read_bytes())

    def test_corrupt_checkpoint_cannot_be_backed_up(self):
        state = json.loads(self.checkpoint.read_text())
        state["exchange"]["balances"]["USDT"] = "999999999"
        self.checkpoint.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            backup_pair(self.archive, self.checkpoint, self.backup)
        self.assertFalse(self.backup.exists())

    def test_backup_tampering_fails_hash_validation(self):
        backup_pair(self.archive, self.checkpoint, self.backup)
        (self.backup / "checkpoint.json").write_text("malicious edits")
        with self.assertRaises(ValueError):
            verify_backup(self.backup)

    def test_source_collision_rejected(self):
        with self.assertRaises(ValueError):
            backup_pair(self.archive, self.checkpoint, self.archive)

    def test_cli_missing_backup_refuses_with_nonzero_exit(self):
        self.assertEqual(2, main(["verify", str(self.root / "missing")]))


if __name__ == "__main__":
    unittest.main()
