"""Sandbox broker fault cases run fully offline without actual orders."""

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.order_intent_journal import OrderIntentJournal
from trading_core.sandbox_order_emulator import (
    SandboxOrderEmulator,
    SimulatedLostAcknowledgment,
)


class SandboxOrderEmulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = OrderIntentJournal(Path(self.temp.name) / "journal.sqlite")
        self.broker = SandboxOrderEmulator()

    def test_partial_fills_accumulate_gross_and_fees_without_overfills(self):
        order = self.broker.submit_once("intent-001", "BTC/KRW", "buy", "2")
        self.assertEqual("open", order["status"])
        first = self.broker.fill("intent-001", "0.5", price="100", fee_pct="0.1")
        self.assertEqual("partial", first["status"])
        self.assertEqual(Decimal("0.5"), Decimal(first["filled"]))
        final = self.broker.fill("intent-001", "1.5", price="120", fee_pct="0.1")
        self.assertEqual("filled", final["status"])
        self.assertEqual(Decimal("230"), Decimal(final["gross_quote"]))
        self.assertEqual(Decimal("0.23"), Decimal(final["fees_quote"]))
        with self.assertRaises(ValueError):
            self.broker.fill("intent-001", "0.1", price="100")

    def test_ambiguous_ack_does_not_resubmit_and_blocks_other_intents(self):
        self.journal.reserve("intent-001", "BTC/KRW", "buy", "1")
        with self.assertRaises(SimulatedLostAcknowledgment):
            self.broker.submit_once("intent-001", "BTC/KRW", "buy", "1", lose_ack=True)
        self.journal.mark_uncertain("intent-001")
        self.assertEqual(1, len(self.broker.orders))
        with self.assertRaises(ValueError):
            self.broker.submit_once("intent-001", "BTC/KRW", "buy", "1")
        with self.assertRaises(ValueError):
            self.journal.reserve("intent-002", "BTC/KRW", "buy", "1")
        evidence = self.broker.query("intent-001")
        self.assertEqual("open", evidence["status"])
        self.assertEqual("uncertain", self.journal.get("intent-001")["status"])
        # An OPEN broker order cannot be declared 'filled' merely because
        # an ACK was found. Leave the journal uncertain pending full evidence.

    def test_cancel_after_partial_fill_retains_executed_quantity(self):
        self.broker.submit_once("intent-001", "ETH/KRW", "sell", "3")
        self.broker.fill("intent-001", "1", price="200")
        canceled = self.broker.cancel("intent-001")
        self.assertEqual("canceled", canceled["status"])
        self.assertEqual(Decimal("1"), Decimal(canceled["filled"]))
        with self.assertRaises(ValueError):
            self.broker.fill("intent-001", "1", price="200")

    def test_invalid_and_overfill_requests_rejected_before_mutation(self):
        self.broker.submit_once("intent-001", "BTC/KRW", "buy", "1")
        prior = self.broker.query("intent-001")
        for quantity in ("NaN", "-1", "0", "2"):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.broker.fill("intent-001", quantity, price=100)
            self.assertEqual(prior, self.broker.query("intent-001"))

    def test_sandbox_evidence_is_explicitly_not_broker_authenticated(self):
        self.broker.submit_once("intent-001", "BTC/KRW", "buy", "1")
        evidence = self.broker.query("intent-001")
        marker = self.broker.evidence(evidence)
        self.assertTrue(marker.startswith("sim-"))
        self.assertEqual(marker, self.broker.evidence(evidence))
        self.assertNotEqual(
            marker, self.broker.evidence(self.broker.fill("intent-001", "1", price="100"))
        )


if __name__ == "__main__":
    unittest.main()
