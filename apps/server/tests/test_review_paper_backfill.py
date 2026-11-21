"""Read-only recovery candidate review; no network or broker calls."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from trading_bot.paper_runner import PaperRunConfig, PaperRunner

from tools.review_paper_backfill import review_candidate


class PaperCandidateReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.archive = self.folder / "archive.json"
        self.checkpoint = self.folder / "paper.json"
        self.candidate = self.folder / "candidate.json"
        self.base = json.loads((ROOT / "config/examples/offline_fixture.json").read_text())
        self.archive.write_text(json.dumps(self.base))
        asyncio.run(
            PaperRunner(PaperRunConfig()).run(self.base["candles"], checkpoint_path=self.checkpoint)
        )
        self.proposed = json.loads(json.dumps(self.base))
        last = self.proposed["candles"][-1]
        self.proposed["candles"].append([last[0] + 3600000, "34", "34", "34", "34", "1000"])
        self.proposed["backfill_review"] = {
            "appended_bars": 1,
            "manual_promotion_required": True,
            "live_orders_enabled": False,
        }
        self.candidate.write_text(json.dumps(self.proposed))

    def test_valid_candidate_is_not_promoted_or_executed(self):
        before_archive = self.archive.read_bytes()
        before_checkpoint = self.checkpoint.read_bytes()
        report = review_candidate(self.archive, self.checkpoint, self.candidate)
        self.assertEqual("candidate_verified_for_manual_review", report["status"])
        self.assertFalse(report["promotion_performed"])
        self.assertEqual(1, report["new_bars"])
        self.assertEqual(before_archive, self.archive.read_bytes())
        self.assertEqual(before_checkpoint, self.checkpoint.read_bytes())

    def test_active_supervisor_denies_manual_candidate_review(self):
        from trading_bot.paper_checkpoint import CheckpointInUseError, locked_checkpoint

        owner = self.checkpoint.with_name(self.checkpoint.name + ".supervisor")
        before = (
            self.archive.read_bytes(),
            self.checkpoint.read_bytes(),
            self.candidate.read_bytes(),
        )
        with locked_checkpoint(owner):
            with self.assertRaises(CheckpointInUseError):
                review_candidate(self.archive, self.checkpoint, self.candidate)
        self.assertEqual(
            before,
            (self.archive.read_bytes(), self.checkpoint.read_bytes(), self.candidate.read_bytes()),
        )

    def test_changed_history_rejected(self):
        changed = json.loads(self.candidate.read_text())
        changed["candles"][10][4] = "10000"
        changed["candles"][10][2] = "10000"
        self.candidate.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            review_candidate(self.archive, self.checkpoint, self.candidate)

    def test_missing_interval_rejected(self):
        changed = json.loads(self.candidate.read_text())
        changed["candles"][-1][0] += 3600000
        self.candidate.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            review_candidate(self.archive, self.checkpoint, self.candidate)

    def test_cannot_bypass_manual_marker(self):
        changed = json.loads(self.candidate.read_text())
        changed["backfill_review"]["manual_promotion_required"] = False
        self.candidate.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            review_candidate(self.archive, self.checkpoint, self.candidate)

    def test_corrupt_checkpoint_disallows_review(self):
        state = json.loads(self.checkpoint.read_text())
        state["exchange"]["balances"]["USDT"] = "999999999"
        self.checkpoint.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            review_candidate(self.archive, self.checkpoint, self.candidate)


if __name__ == "__main__":
    unittest.main()
