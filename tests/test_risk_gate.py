import importlib.util
from pathlib import Path
import sys
import unittest
from decimal import Decimal as D

path = Path(__file__).resolve().parents[1] / "packages/core/risk_gate.py"
spec = importlib.util.spec_from_file_location("risk_gate", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class RiskGateTests(unittest.TestCase):
    def setUp(self):
        self.limits = module.RiskLimits(D("100"), D("500"), D("50"), 30)
        self.state = module.RiskSnapshot(D("1000"), D("1000"), D("200"), D("0"), 2)

    def test_accept_and_order_limit(self):
        self.assertTrue(module.check_buy(D("90"), self.limits, self.state).allowed)
        self.assertEqual(module.check_buy(D("101"), self.limits, self.state).reason, "order_limit")

    def test_stale_feed(self):
        state = module.RiskSnapshot(D("1000"), D("1000"), D("200"), D("0"), None)
        self.assertEqual(module.check_buy(D("10"), self.limits, state).reason, "stale_feed")

    def test_daily_loss_and_halt(self):
        state = module.RiskSnapshot(D("1000"), D("950"), D("200"), D("-50"), 2)
        self.assertEqual(module.check_buy(D("10"), self.limits, state).reason, "daily_loss_limit")
        state = module.RiskSnapshot(D("1000"), D("1000"), D("200"), D("0"), 2, True)
        self.assertEqual(module.check_buy(D("10"), self.limits, state).reason, "halted")

    def test_exposure_and_cash(self):
        self.assertEqual(module.check_buy(D("90"), module.RiskLimits(D("100"), D("250"), D("50")), self.state).reason, "exposure_limit")
        state = module.RiskSnapshot(D("5"), D("1000"), D("0"), D("0"), 2)
        self.assertEqual(module.check_buy(D("10"), self.limits, state).reason, "insufficient_cash")

    def test_non_finite(self):
        self.assertEqual(module.check_buy(D("NaN"), self.limits, self.state).reason, "non_finite")


if __name__ == "__main__":
    unittest.main()
