import importlib.util
from pathlib import Path
import sys
import unittest
from decimal import Decimal

base = Path(__file__).resolve().parents[1] / "packages/core"
for name in ("broker_interface", "paper_broker"):
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
from broker_interface import Instrument, OrderRequest, Side, Environment, require_paper, LiveTradingDisabled
from paper_broker import PaperBroker


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.instrument = Instrument("test", "ABC", "KRW", Decimal("1"), Decimal("1"))

    def test_idempotency_and_cancel(self):
        broker = PaperBroker()
        request = OrderRequest("abc-1", self.instrument, Side.BUY, Decimal("2"), Decimal("100"))
        receipt = broker.submit_limit(request)
        self.assertEqual(receipt, broker.submit_limit(request))
        self.assertEqual(broker.cancel(receipt.venue_order_id).status, "cancelled")

    def test_reject_bad_precision(self):
        with self.assertRaises(ValueError):
            OrderRequest("x", self.instrument, Side.BUY, Decimal("0.5"), Decimal("100"))

    def test_live_disabled(self):
        with self.assertRaises(LiveTradingDisabled):
            require_paper(Environment.LIVE)

    def test_reused_key_different_order(self):
        broker = PaperBroker()
        broker.submit_limit(OrderRequest("x", self.instrument, Side.BUY, Decimal("1"), Decimal("100")))
        with self.assertRaises(ValueError):
            broker.submit_limit(OrderRequest("x", self.instrument, Side.SELL, Decimal("1"), Decimal("100")))


if __name__ == "__main__":
    unittest.main()
