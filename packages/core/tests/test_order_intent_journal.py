"""Offline-only durable intent journal tests: no broker, socket or API."""
from contextlib import closing
from decimal import Decimal, localcontext
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.order_intent_journal import OrderIntentJournal


class OrderIntentJournalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "intents.sqlite"
        self.store = OrderIntentJournal(self.path)

    def test_intent_is_durable_and_exact_duplicate_does_not_reissue(self):
        original = self.store.reserve("client-001", "BTC/KRW", "buy", Decimal("0.001"))
        self.assertEqual("reserved", original["status"])
        reopened = OrderIntentJournal(self.path)
        repeated = reopened.reserve("client-001", "BTC/KRW", "buy", "0.0010")
        self.assertEqual(original, repeated)
        self.assertEqual(1, len(reopened.unresolved()))

    def test_quantity_keeps_all_digits_under_low_decimal_precision(self):
        quantity = "0.123456789012345678901234567890123456789"
        with localcontext() as context:
            context.prec = 8
            original = self.store.reserve("precise-001", "BTC/KRW", "buy", quantity)
        self.assertEqual(quantity, original["quantity"])
        self.assertEqual(quantity, OrderIntentJournal(self.path).get("precise-001")["quantity"])

    def test_duplicate_and_audit_survive_decimal_context_changes(self):
        quantity = "12345678901234567890.1234567890123456789"
        with localcontext() as context:
            context.prec = 80
            original = self.store.reserve("precise-001", "BTC/KRW", "buy", quantity)
        reopened = OrderIntentJournal(self.path)
        with localcontext() as context:
            context.prec = 6
            context.Emax = 2
            context.Emin = -2
            context.capitals = 0
            repeated = reopened.reserve("precise-001", "BTC/KRW", "buy", quantity + "00")
            self.assertEqual(original, repeated)
            self.assertEqual(1, reopened.audit_integrity()["unresolved_count"])

    def test_precise_quantity_conflict_is_not_hidden_by_context_rounding(self):
        with localcontext() as context:
            context.prec = 8
            self.store.reserve("precise-001", "BTC/KRW", "buy", "0.12345678901234567891")
            with self.assertRaisesRegex(ValueError, "different order details"):
                self.store.reserve("precise-001", "BTC/KRW", "buy", "0.12345678901234567892")
        self.assertEqual("0.12345678901234567891", self.store.get("precise-001")["quantity"])

    def test_ordinary_quantity_encodings_remain_canonical(self):
        examples = (
            ("1.2300", "1.23"),
            ("1000.00", "1E+3"),
            ("0.0010", "0.001"),
            ("0.0000010", "0.000001"),
            ("0.00000010", "1E-7"),
        )
        with localcontext() as context:
            context.capitals = 0
            for index, (value, expected) in enumerate(examples):
                with self.subTest(value=value):
                    client_id = "ordinary-" + str(index)
                    row = self.store.reserve(client_id, "BTC/KRW", "buy", value)
                    self.store.reconcile(client_id, outcome="rejected", evidence_ref="evidence")
                    self.assertEqual(expected, row["quantity"])
            self.assertEqual(len(examples), self.store.audit_integrity()["intent_count"])

    def test_readonly_view_refuses_mutations_before_connecting(self):
        actions = (
            ("reserve", lambda view: view.reserve("client-001", "BTC/KRW", "buy", "0.001")),
            ("mark_uncertain", lambda view: view.mark_uncertain("client-001")),
            (
                "reconcile",
                lambda view: view.reconcile(
                    "client-001", outcome="rejected", evidence_ref="broker-response"
                ),
            ),
        )
        for name, action in actions:
            with self.subTest(method=name):
                path = self.path.with_name(name + ".sqlite")
                writable = OrderIntentJournal(path)
                if name != "reserve":
                    writable.reserve("client-001", "BTC/KRW", "buy", "0.001")
                view = OrderIntentJournal.open_existing_readonly(path)
                before = path.read_bytes()
                with patch.object(view, "_connect", wraps=view._connect) as connect:
                    with self.assertRaisesRegex(PermissionError, "[Rr]ead.only"):
                        action(view)
                    connect.assert_not_called()
                self.assertEqual(before, path.read_bytes())

    def test_readers_do_not_change_journal_mode_or_database_bytes(self):
        original = self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        readonly = OrderIntentJournal.open_existing_readonly(self.path)
        for journal in (self.store, readonly):
            for name in ("get", "unresolved", "audit_integrity"):
                with self.subTest(view=journal is readonly, method=name):
                    with closing(sqlite3.connect(self.path)) as db:
                        mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                        self.assertEqual("delete", mode)
                    before = self.path.read_bytes()
                    if name == "get":
                        self.assertEqual(original, journal.get("client-001"))
                    elif name == "unresolved":
                        self.assertEqual([original], journal.unresolved())
                    else:
                        self.assertEqual(1, journal.audit_integrity()["unresolved_count"])
                    self.assertEqual(before, self.path.read_bytes())
                    with closing(sqlite3.connect(self.path)) as db:
                        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                        self.assertEqual("delete", mode)

    def test_unknown_submission_blocks_new_intents_after_restart(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        self.store.mark_uncertain("client-001")
        reopened = OrderIntentJournal(self.path)
        self.assertEqual("uncertain", reopened.get("client-001")["status"])
        with self.assertRaises(ValueError):
            reopened.reserve("client-002", "BTC/KRW", "buy", "0.01")
        self.assertEqual(["client-001"], [row["client_id"] for row in reopened.unresolved()])

    def test_final_reconciliation_requires_independent_reference_and_is_immutable(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        with self.assertRaises(ValueError):
            self.store.reconcile("client-001", outcome="filled", evidence_ref="audit-123")
        self.store.mark_uncertain("client-001")
        result = self.store.reconcile("client-001", outcome="filled", evidence_ref="audit-123",
                                      broker_order_id="exchange-789")
        self.assertEqual("reconciled", result["status"])
        self.assertEqual(result, self.store.reconcile(
            "client-001", outcome="filled", evidence_ref="audit-123",
            broker_order_id="exchange-789"
        ))
        with self.assertRaises(ValueError):
            self.store.reconcile("client-001", outcome="canceled", evidence_ref="audit-123")
        self.assertEqual([], self.store.unresolved())
        self.assertEqual("reserved", self.store.reserve(
            "client-002", "BTC/KRW", "sell", "0.01"
        )["status"])

    def test_conflicting_duplicate_does_not_change_persisted_intent(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        with self.assertRaises(ValueError):
            self.store.reserve("client-001", "ETH/KRW", "sell", "0.01")
        self.assertEqual("BTC/KRW", self.store.get("client-001")["symbol"])
        self.assertEqual(1, len(self.store.unresolved()))

    def test_competing_writers_cannot_reserve_two_pending_orders(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def attempt(identity):
            barrier.wait(timeout=5)
            try:
                return ("ok", self.store.reserve(identity, "BTC/KRW", "buy", "0.001"))
            except ValueError:
                return ("blocked", identity)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(attempt, key) for key in ("client-001", "client-002")]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(1, sum(status == "ok" for status, _ in results))
        self.assertEqual(1, sum(status == "blocked" for status, _ in results))
        self.assertEqual(1, len(self.store.unresolved()))

    def test_invalid_values_and_unverified_outcomes_rejected(self):
        for qty in (0, "-1", "NaN", "Infinity"):
            with self.subTest(qty=qty), self.assertRaises(ValueError):
                self.store.reserve("intent-q", "BTC/KRW", "buy", qty)
        with self.assertRaises(ValueError):
            self.store.reserve("bad-id;drop table", "BTC/KRW", "buy", "1")
        with self.assertRaises(ValueError):
            self.store.reserve("id", "BTC/BTC", "buy", "1")
        for pair in ("KRW/KRW", "USDT/USDT"):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                self.store.reserve("self-quote", pair, "buy", "1")
        with self.assertRaises(ValueError):
            self.store.reconcile("missing", outcome="filled", evidence_ref="note",
                                 broker_order_id="order-1")

    def test_audit_integrity_reports_unresolved_without_approving_orders(self):
        initial = self.store.audit_integrity()
        self.assertEqual(0, initial["intent_count"])
        self.assertFalse(initial["live_orders_approved"])
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        blocked = OrderIntentJournal(self.path).audit_integrity()
        self.assertEqual(1, blocked["unresolved_count"])
        self.assertFalse(blocked["new_intents_permitted"])
        self.store.mark_uncertain("client-001")
        self.assertFalse(self.store.audit_integrity()["new_intents_permitted"])
        self.store.reconcile("client-001", outcome="filled",
                             evidence_ref="broker-evidence-001",
                             broker_order_id="upbit-001")
        checked = self.store.audit_integrity()
        self.assertEqual(0, checked["unresolved_count"])
        self.assertEqual(1, checked["intent_count"])
        self.assertFalse(checked["live_orders_approved"])

    def test_one_broker_order_cannot_reconcile_multiple_client_ids(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        self.store.reconcile("client-001", outcome="filled",
                             evidence_ref="evidence-001",
                             broker_order_id="upbit-001")
        self.store.reserve("client-002", "BTC/KRW", "sell", "0.001")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.reconcile("client-002", outcome="filled",
                                 evidence_ref="evidence-002",
                                 broker_order_id="upbit-001")
        self.assertEqual("reserved", self.store.get("client-002")["status"])
        self.assertEqual(1, self.store.audit_integrity()["unresolved_count"])

    def test_manual_invalid_journal_rows_are_detected_by_preflight(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE intents SET outcome='filled' WHERE client_id=?",
                       ("client-001",))
        with self.assertRaises(ValueError):
            self.store.audit_integrity()

    def test_rejected_submission_can_be_reconciled_without_order_id(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        result = self.store.reconcile("client-001", outcome="rejected",
                                      evidence_ref="broker-response-42")
        self.assertEqual("rejected", result["outcome"])
        self.assertEqual([], self.store.unresolved())


if __name__ == "__main__":
    unittest.main()
