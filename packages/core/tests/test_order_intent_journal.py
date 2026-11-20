"""Offline-only durable intent journal tests: no broker, socket or API."""
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

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
        with self.assertRaises(ValueError):
            self.store.reconcile("missing", outcome="filled", evidence_ref="note",
                                 broker_order_id="order-1")

    def test_rejected_submission_can_be_reconciled_without_order_id(self):
        self.store.reserve("client-001", "BTC/KRW", "buy", "0.001")
        result = self.store.reconcile("client-001", outcome="rejected",
                                      evidence_ref="broker-response-42")
        self.assertEqual("rejected", result["outcome"])
        self.assertEqual([], self.store.unresolved())


if __name__ == "__main__":
    unittest.main()
