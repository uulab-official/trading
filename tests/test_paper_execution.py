import importlib.util
from pathlib import Path
from decimal import Decimal as D
import sys
import unittest

base = Path(__file__).resolve().parents[1] / "packages/core"
for name in ("broker_interface", "paper_broker", "risk_gate", "paper_execution"):
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
from broker_interface import Instrument, OrderRequest, Side
from risk_gate import RiskLimits, RiskSnapshot
from paper_execution import PaperOrderAdmission


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.engine = PaperOrderAdmission(RiskLimits(D("100"), D("150"), D("50")))
        self.state = RiskSnapshot(D("200"), D("200"), D("0"), D("0"), 1)
        self.instrument = Instrument("test", "ABC", "KRW", D("1"), D("1"))

    def order(self, key, side=Side.BUY):
        return OrderRequest(key, self.instrument, side, D("1"), D("90"))

    def test_reservations_block_second_order(self):
        first = self.engine.submit(self.order("one"), self.state)
        self.assertTrue(first.decision.allowed)
        self.assertEqual(self.engine.reserved_notional, D("90"))
        self.assertEqual(self.engine.submit(self.order("one"), self.state), first)
        self.assertEqual(self.engine.submit(self.order("two"), self.state).decision.reason, "exposure_limit")
        self.engine.cancel("one")
        self.assertEqual(self.engine.reserved_notional, D("0"))
        self.assertTrue(self.engine.submit(self.order("two"), self.state).decision.allowed)

    def test_cancelled_id_cannot_reopen(self):
        order = self.order("once")
        self.assertTrue(self.engine.submit(order, self.state).decision.allowed)
        self.engine.cancel("once")
        self.assertEqual(self.engine.submit(order, self.state).decision.reason, "previously_cancelled")
        self.assertEqual(self.engine.reserved_notional, D("0"))

    def test_sell_is_not_implemented(self):
        self.assertEqual(self.engine.submit(self.order("sell", Side.SELL), self.state).decision.reason, "sell_not_implemented")


if __name__ == "__main__":
    unittest.main()
