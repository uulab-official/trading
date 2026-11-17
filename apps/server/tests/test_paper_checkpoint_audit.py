"""Regression tests for the read-only checkpoint audit."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.paper_checkpoint_audit import audit
from trading_bot.paper_runner import PaperRunConfig, PaperRunner

ROOT = Path(__file__).resolve().parents[3]


class TestPaperCheckpointAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.fixture = self.folder / "fixture.json"
        source = json.loads((ROOT / "config/examples/offline_fixture.json").read_text())
        self.fixture.write_text(json.dumps(source), encoding="utf-8")
        self.checkpoint = self.folder / "checkpoint.json"
        asyncio.run(PaperRunner(PaperRunConfig()).run(source["candles"], checkpoint_path=self.checkpoint))

    def test_valid_audit_is_read_only_and_never_approves_live(self):
        before = self.checkpoint.read_bytes()
        result = audit(self.fixture, self.checkpoint)
        self.assertEqual("verified_offline_paper", result["status"])
        self.assertFalse(result["live_trading_approved"])
        self.assertEqual(90, result["processed_bars"])
        self.assertEqual(before, self.checkpoint.read_bytes())

    def test_modified_cash_is_rejected_without_repair(self):
        state = json.loads(self.checkpoint.read_text())
        state["exchange"]["cash"] = "999999999"
        corrupted = json.dumps(state).encode()
        self.checkpoint.write_bytes(corrupted)
        with self.assertRaises(ValueError):
            audit(self.fixture, self.checkpoint)
        self.assertEqual(corrupted, self.checkpoint.read_bytes())

    def test_changed_fixture_prefix_is_rejected(self):
        fixture = json.loads(self.fixture.read_text())
        fixture["candles"][0][4] = 101
        fixture["candles"][0][2] = 101
        self.fixture.write_text(json.dumps(fixture))
        with self.assertRaises(ValueError):
            audit(self.fixture, self.checkpoint)

    def test_missing_checkpoint_does_not_create_one(self):
        self.checkpoint.unlink()
        with self.assertRaises(ValueError):
            audit(self.fixture, self.checkpoint)
        self.assertFalse(self.checkpoint.exists())

    def test_live_fixture_rejected(self):
        fixture = json.loads(self.fixture.read_text())
        fixture["mode"] = "live"
        self.fixture.write_text(json.dumps(fixture))
        with self.assertRaises(ValueError):
            audit(self.fixture, self.checkpoint)


if __name__ == "__main__":
    unittest.main()
