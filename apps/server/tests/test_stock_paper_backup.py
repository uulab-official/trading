"""Stock paper WAL backup and restore-verification regression tests."""

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.stock_paper_backup import create_backup, main, verify_backup
from tools.stock_paper_forward import replay_forward

START = 1774417800000 - (1774417800000 % 60000)


def fixture():
    values = (10, 9, 8, 20, 21)
    return {
        "mode": "paper",
        "interval": "1m",
        "no_live_orders": True,
        "timestamp_semantics": "source_close_to_utc_open",
        "symbol": "005930",
        "quote_currency": "KRW",
        "candles": [
            [START + i * 60000, str(value), str(value), str(value), str(value), "100"]
            for i, value in enumerate(values)
        ],
    }


class StockPaperBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "data.json"
        self.db = self.root / "paper.sqlite"
        self.destination = self.root / "stock-backup-001"
        self.fixture = fixture()
        self.source.write_text(json.dumps(self.fixture))
        self.settings = dict(initial_cash="1000", fee_pct="0", fast=2, slow=3, allocation_pct="50")
        replay_forward(self.fixture, self.db, **self.settings)

    def test_consistent_deep_backup_and_no_original_modification(self):
        before_fixture = self.source.read_bytes()
        report = create_backup(self.source, self.db, self.destination, **self.settings)
        self.assertEqual("paper", report["mode"])
        self.assertFalse(report["live_orders_enabled"])
        self.assertEqual(before_fixture, self.source.read_bytes())
        self.assertEqual(
            {"candles.json", "stock.sqlite", "manifest.json"},
            {file.name for file in self.destination.iterdir()},
        )
        check = verify_backup(self.destination)
        self.assertEqual("stock_paper_backup_deep_verified", check["status"])
        self.assertTrue(check["economic_replay_verified"])
        self.assertEqual(
            {"candles.json", "stock.sqlite", "manifest.json"},
            {file.name for file in self.destination.iterdir()},
        )

    def test_existing_backup_cannot_be_replaced(self):
        create_backup(self.source, self.db, self.destination, **self.settings)
        original = (self.destination / "manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            create_backup(self.source, self.db, self.destination, **self.settings)
        self.assertEqual(original, (self.destination / "manifest.json").read_bytes())

    def test_changed_database_bytes_fail_sha_validation(self):
        create_backup(self.source, self.db, self.destination, **self.settings)
        with sqlite3.connect(self.destination / "stock.sqlite") as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        with self.assertRaises(ValueError):
            verify_backup(self.destination, deep=False)
        with self.assertRaises(ValueError):
            verify_backup(self.destination, deep=True)

    def test_deep_audit_rejects_recomputed_hash_after_fake_execution(self):
        import hashlib

        create_backup(self.source, self.db, self.destination, **self.settings)
        dbfile = self.destination / "stock.sqlite"
        with sqlite3.connect(dbfile) as db:
            db.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
        manifest_file = self.destination / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        manifest["database_sha256"] = hashlib.sha256(dbfile.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest))
        self.assertEqual(
            "stock_paper_backup_bytes_verified",
            verify_backup(self.destination, deep=False)["status"],
        )
        with self.assertRaises(ValueError):
            verify_backup(self.destination, deep=True)

    def test_backup_verification_rejects_unmanifested_wal_changes(self):
        import hashlib

        create_backup(self.source, self.db, self.destination, **self.settings)
        dbfile = self.destination / "stock.sqlite"
        manifest_file = self.destination / "manifest.json"
        # A committed WAL can change SQLite's effective contents without
        # changing the main database bytes covered by the manifest.
        with closing(sqlite3.connect(dbfile)) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            original_bytes = dbfile.read_bytes()
            manifest = json.loads(manifest_file.read_text())
            manifest["database_sha256"] = hashlib.sha256(original_bytes).hexdigest()
            manifest_file.write_text(json.dumps(manifest))
            database.execute("UPDATE paper_fills SET result_json='{}' WHERE sequence=1")
            database.commit()
            self.assertEqual(original_bytes, dbfile.read_bytes())
            self.assertGreater((self.destination / "stock.sqlite-wal").stat().st_size, 0)
            for deep in (False, True):
                with self.subTest(deep=deep), self.assertRaises(ValueError):
                    verify_backup(self.destination, deep=deep)

    def test_no_automatic_restore_or_live_order_mode(self):
        self.assertFalse(self.destination.exists())
        self.assertEqual(2, main(["verify", str(self.destination)]))
        self.assertFalse(self.destination.exists())
        with self.assertRaises(SystemExit):
            main(["restore", "--database", str(self.db)])


if __name__ == "__main__":
    unittest.main()
