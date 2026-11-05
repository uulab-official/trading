import importlib.util
from pathlib import Path
from decimal import Decimal as D
import tempfile
import sys
import unittest

path = Path(__file__).resolve().parents[1] / "packages/core/order_journal.py"
spec = importlib.util.spec_from_file_location("order_journal", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "orders.sqlite")
        self.journal = module.OrderJournal(self.path)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def test_restart_and_unknown(self):
        self.assertEqual(self.journal.reserve("one", {"symbol": "ABC"}, D("90"), D("100")), "reserved")
        self.journal.mark_submitting("one")
        self.journal.mark_unknown("one")
        self.journal.close()
        self.journal = module.OrderJournal(self.path)
        self.assertEqual(self.journal.pending_reconciliation(), [("one", "unknown")])
        self.assertEqual(self.journal.reserved_total(), D("90"))
        with self.assertRaises(ValueError):
            self.journal.reserve("two", {"symbol": "XYZ"}, D("20"), D("100"))
        self.journal.confirm_cancelled("one")
        self.assertEqual(self.journal.reserved_total(), D("0"))

    def test_idempotency(self):
        self.journal.reserve("one", {"symbol": "ABC"}, D("10"), D("100"))
        self.assertEqual(self.journal.reserve("one", {"symbol": "ABC"}, D("10"), D("100")), "reserved")
        with self.assertRaises(ValueError):
            self.journal.reserve("one", {"symbol": "XYZ"}, D("10"), D("100"))

    def test_transaction_rollback(self):
        with self.assertRaises(ValueError):
            self.journal.reserve("bad", {"x": float("nan")}, D("10"), D("100"))
        self.assertEqual(self.journal.reserved_total(), D("0"))


if __name__ == "__main__":
    unittest.main()
