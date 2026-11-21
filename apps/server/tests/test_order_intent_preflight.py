"""Read-only CLI tests: no account, API, or live orders."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core"))

from trading_core.order_intent_journal import OrderIntentJournal

from tools.order_intent_preflight import main


class IntentPreflightCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "intents.sqlite"
        self.journal = OrderIntentJournal(self.path)

    def test_clean_journal_prints_paper_safe_report_without_live_approval(self):
        stream = StringIO()
        with redirect_stdout(stream):
            code = main([str(self.path)])
        self.assertEqual(0, code)
        result = json.loads(stream.getvalue())
        self.assertFalse(result["live_orders_approved"])
        self.assertTrue(result["new_intents_permitted"])
        self.assertEqual(0, result["intent_count"])

    def test_unresolved_intent_returns_nonzero_without_modification(self):
        self.journal.reserve("order-001", "BTC/KRW", "buy", "0.001")
        stream = StringIO()
        with redirect_stdout(stream):
            code = main([str(self.path)])
        self.assertEqual(3, code)
        self.assertEqual(1, json.loads(stream.getvalue())["unresolved_count"])
        self.assertEqual("reserved", self.journal.get("order-001")["status"])

    def test_missing_journal_does_not_get_created(self):
        self.path.unlink()
        with redirect_stderr(StringIO()):
            code = main([str(self.path)])
        self.assertEqual(2, code)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
