"""Real SQLite regressions for durable paper admission; never contacts a broker."""
import importlib.util
from pathlib import Path
from decimal import Decimal as D
from contextlib import closing
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("journal_quality", ROOT / "packages/core/order_journal.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Journal = module.OrderJournal


class PaperOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper.sqlite"
        self.journal = Journal(str(self.path))
        self.payload = {"symbol": "SYNTHETIC", "currency": "KRW"}

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def reserve(self, key="one", amount="60"):
        return self.journal.reserve(key, self.payload, D(amount), D("100"))

    def events(self):
        return self.journal.db.execute("SELECT from_status,to_status FROM order_events ORDER BY id").fetchall()

    def test_cancelled_identity_keeps_original_notional(self):
        self.reserve()
        self.journal.confirm_cancelled("one")
        with self.assertRaises(ValueError):
            self.reserve(amount="70")
        self.assertEqual(self.reserve(), "cancelled")
        self.assertEqual(self.journal.reserved_total(), D("0"))

    def test_empty_and_whitespace_ids_rejected(self):
        for key in ["", "   ", "\n", None, 3]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.reserve(key, self.payload, D("1"), D("100"))

    def test_invalid_numbers_and_payloads_rejected(self):
        for value in [D("NaN"), D("sNaN"), D("Infinity"), D("0"), D("-1"), 1, True, "1"]:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.journal.reserve("bad", self.payload, value, D("100"))
        for payload in [[], None, {"bad": float("nan")}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.journal.reserve("bad", payload, D("1"), D("100"))
        self.assertEqual(self.journal.reserved_total(), D("0"))

    def test_same_reservation_is_idempotent(self):
        self.reserve()
        self.reserve()
        self.assertEqual(self.journal.reserved_total(), D("60"))
        self.assertEqual(self.events(), [(None, "reserved")])

    def test_restart_preserves_unknown_and_blocks_resubmission(self):
        self.reserve()
        self.journal.mark_submitting("one")
        self.journal.mark_unknown("one")
        self.journal.close()
        self.journal = Journal(str(self.path))
        self.assertEqual(self.journal.pending_reconciliation(), [("one", "unknown")])
        with self.assertRaises(ValueError):
            self.journal.mark_submitting("one")
        self.assertEqual(self.journal.reserved_total(), D("60"))
        self.assertEqual(len(self.events()), 3)

    def test_acknowledgement_and_confirmed_cancel_are_atomic_and_idempotent(self):
        self.reserve()
        self.journal.mark_submitting("one")
        self.journal.mark_unknown("one")
        self.assertTrue(hasattr(self.journal, "confirm_accepted"))
        self.journal.confirm_accepted("one", "paper:one")
        self.journal.confirm_accepted("one", "paper:one")
        with self.assertRaises(ValueError):
            self.journal.confirm_accepted("one", "paper:other")
        self.journal.request_cancel("one")
        self.journal.request_cancel("one")
        self.assertEqual(self.journal.reserved_total(), D("60"))
        self.journal.confirm_cancelled("one")
        self.journal.confirm_cancelled("one")
        self.assertEqual(self.journal.reserved_total(), D("0"))
        self.assertEqual(self.events(), [(None, "reserved"), ("reserved", "submitting"),
            ("submitting", "unknown"), ("unknown", "accepted"),
            ("accepted", "cancel_pending"), ("cancel_pending", "cancelled")])

    def test_confirmed_rejection_releases_reservation_without_reopening(self):
        self.reserve()
        self.journal.mark_submitting("one")
        self.assertTrue(hasattr(self.journal, "confirm_rejected"))
        self.journal.confirm_rejected("one")
        self.assertEqual(self.reserve(), "rejected")
        self.assertEqual(self.journal.reserved_total(), D("0"))
        with self.assertRaises(ValueError):
            self.journal.mark_submitting("one")

    def test_accepted_order_cannot_be_rejected_or_locally_resubmitted(self):
        self.reserve()
        self.journal.mark_submitting("one")
        self.assertTrue(hasattr(self.journal, "confirm_accepted"))
        self.journal.confirm_accepted("one", "paper:one")
        with self.assertRaises(ValueError):
            self.journal.confirm_rejected("one")
        with self.assertRaises(ValueError):
            self.journal.mark_submitting("one")

    def test_audit_failure_rolls_back_reservation_and_transition(self):
        self.reserve()
        self.assertTrue(self.journal.db.execute("SELECT 1 FROM sqlite_master WHERE name='order_events'").fetchone())
        self.journal.db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON order_events BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.mark_submitting("one")
        self.assertEqual(self.reserve(), "reserved")
        with self.assertRaises(sqlite3.IntegrityError):
            self.reserve("two", "30")
        self.assertEqual(self.journal.reserved_total(), D("60"))
        self.assertEqual(len(self.events()), 1)

    def test_two_connections_cannot_overspend(self):
        barrier = threading.Barrier(2)
        def contender(key):
            journal = Journal(str(self.path))
            try:
                barrier.wait(timeout=3)
                try:
                    journal.reserve(key, self.payload, D("60"), D("100"))
                    return True
                except ValueError:
                    return False
            finally:
                journal.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contender, ["one", "two"]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.journal.reserved_total(), D("60"))

    def test_corrupt_reserved_amount_fails_closed(self):
        self.reserve()
        self.journal.db.execute("UPDATE orders SET reserved='NaN'")
        with self.assertRaises(ValueError):
            self.reserve("two", "30")
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)

    def test_mixed_currency_capacity_is_not_added_together(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.journal.reserve("usd", {"currency":"USDT"}, D("1"), D("100"))

    def test_decimal_context_cannot_round_away_capacity(self):
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 2
            self.journal.reserve("first", {}, D("99.999999999999999999"), D("100"))
            with self.assertRaises(ValueError):
                self.journal.reserve("second", {}, D("0.000000000000000002"), D("100"))
            self.assertEqual(self.journal.reserved_total(), D("99.999999999999999999"))

    def test_legacy_acknowledgement_can_fill_missing_venue_id(self):
        self.reserve()
        self.journal.db.execute("UPDATE orders SET status='accepted',venue_id=NULL")
        self.journal.confirm_accepted("one", "paper:verified")
        self.assertEqual(self.journal.db.execute("SELECT venue_id FROM orders").fetchone()[0], "paper:verified")

    def test_corrupted_existing_status_is_not_successful_replay(self):
        self.reserve()
        self.journal.db.execute("UPDATE orders SET status='invalid'")
        with self.assertRaises(ValueError):
            self.reserve()

    def test_source_cannot_be_changed_on_reopen(self):
        with self.assertRaises(ValueError):
            Journal(str(self.path), source="synthetic_demo")
        self.assertEqual(self.journal.db.execute("SELECT value FROM order_journal_meta WHERE key='source'").fetchone()[0], "paper_journal")

    def test_legacy_cancelled_amount_is_not_guessed(self):
        self.journal.close()
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,reserved TEXT NOT NULL,venue_id TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("INSERT INTO orders(client_id,payload,status,reserved) VALUES('old','{}','cancelled','0')")
            db.execute("INSERT INTO orders(client_id,payload,status,reserved) VALUES('active','{}','reserved','20')")
        self.journal = Journal(str(self.path))
        with self.assertRaises(ValueError):
            self.journal.reserve("old", {}, D("99"), D("100"))
        self.assertEqual(self.journal.reserve("active", {}, D("20"), D("100")), "reserved")
        self.assertEqual(self.journal.reserved_total(), D("20"))


if __name__ == "__main__":
    unittest.main()
