import importlib.util
from pathlib import Path
import sys
import unittest
from decimal import Decimal as D

path = Path(__file__).resolve().parents[1] / "packages/core/order_lifecycle.py"
spec = importlib.util.spec_from_file_location("order_lifecycle", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
OrderState = module.OrderState
Status = module.OrderStatus


class OrderLifecycleTests(unittest.TestCase):
    def test_partial_and_full_fill(self):
        state = OrderState("order-1", D("10")).transition(Status.SUBMITTING).transition(Status.ACCEPTED)
        state = state.transition(Status.PARTIALLY_FILLED, D("3"))
        self.assertEqual(state.filled_quantity, D("3"))
        self.assertEqual(state.transition(Status.FILLED, D("10")).status, Status.FILLED)

    def test_timeout_remains_unknown(self):
        state = OrderState("order-2", D("2")).transition(Status.SUBMITTING).transition(Status.UNKNOWN)
        self.assertEqual(state.status, Status.UNKNOWN)
        self.assertEqual(state.transition(Status.ACCEPTED).status, Status.ACCEPTED)

    def test_no_reversal_or_overfill(self):
        state = OrderState("order-3", D("10")).transition(Status.SUBMITTING).transition(Status.PARTIALLY_FILLED, D("5"))
        with self.assertRaises(ValueError):
            state.transition(Status.PARTIALLY_FILLED, D("4"))
        with self.assertRaises(ValueError):
            state.transition(Status.FILLED, D("11"))

    def test_terminal_status(self):
        state = OrderState("order-4", D("1")).transition(Status.REJECTED)
        with self.assertRaises(ValueError):
            state.transition(Status.SUBMITTING)


if __name__ == "__main__":
    unittest.main()
