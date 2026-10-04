"""Offline v1 checkpoint storage and economic consistency contracts."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trading_core.legacy_checkpoint import LegacyCheckpoint, CheckpointError
from trading_core.risk_manager import RiskConfig
from trading_core.state_store import SQLiteStateStore, JSONStateStore


class LegacyCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="legacy-v1-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "paper.db"

    def open(self, store=None, bot_id="audit", config=None):
        session = LegacyCheckpoint(store or SQLiteStateStore(str(self.path)), bot_id, config or RiskConfig())
        self.addCleanup(session.release)
        session.restore()
        return session

    def replace(self, data, key="bot:audit:legacy_checkpoint:v1"):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE state SET data=? WHERE key=?", (json.dumps(data), key))

    def test_fresh_simulation_is_committed_before_orders_and_restores_exactly(self):
        first = self.open()
        saved = first.snapshot()
        self.assertEqual(first.portfolio.cash_balance, Decimal("10000"))
        self.assertIsNone(first.recovery_reason)
        self.assertEqual(saved["generation"], 1)
        first.release()
        second = self.open()
        self.assertEqual(second.snapshot(), saved)
        self.assertEqual(second.exchange.snapshot(), first.exchange.snapshot())

    def test_existing_empty_database_does_not_mint_cash(self):
        SQLiteStateStore(str(self.path))
        session = self.open()
        self.assertIsNotNone(session.recovery_reason)
        self.assertIsNone(session.exchange)

    def test_legacy_portfolio_is_preserved_without_automatic_migration(self):
        store = SQLiteStateStore(str(self.path))
        store.save("bot:audit:portfolio", {"cash_balance": "9400", "daily_start_value": "10000"})
        original = self.path.read_bytes()
        session = self.open(store)
        self.assertIn("legacy", session.recovery_reason.lower())
        session.save()
        self.assertEqual(self.path.read_bytes(), original)

    def test_json_backend_is_unsupported_and_file_is_preserved(self):
        store = JSONStateStore(str(Path(self.tmp.name) / "json"))
        store.save("bot:audit:portfolio", {"cash_balance": "9400"})
        path = store._get_file_path("bot:audit:portfolio")
        before = path.read_bytes()
        session = self.open(store)
        self.assertIn("SQLite", session.recovery_reason)
        session.save()
        self.assertIsNone(session.exchange)
        self.assertEqual(path.read_bytes(), before)

    def test_competing_writer_is_rejected(self):
        self.open()
        with self.assertRaisesRegex(CheckpointError, "writer"):
            self.open()

    def test_generation_compare_rejects_stale_write(self):
        session = self.open()
        snapshot = session.snapshot()
        snapshot["generation"] += 1
        self.replace(snapshot)
        with self.assertRaisesRegex(CheckpointError, "generation"):
            session.save()
        self.assertIsNotNone(session.recovery_reason)

    def test_sensitive_configuration_is_not_part_of_payload(self):
        session = self.open()
        data = session.snapshot()
        self.assertEqual(set(data["risk_config"]), set(vars(RiskConfig())))
        encoded = json.dumps(data)
        for forbidden in ("api_key", "api_secret", "bot_token", "telegram", "password"):
            self.assertNotIn(forbidden, encoded)

    def test_unsupported_or_foreign_identity_does_not_restore_economics(self):
        base = self.open()
        saved = base.snapshot()
        base.release()
        for field, value in (("version", 999), ("bot_id", "other"), ("quote_asset", "KRW"),
                             ("mode", "live"), ("unexpected", "forbidden")):
            with self.subTest(field=field):
                altered = copy.deepcopy(saved)
                altered[field] = value
                self.replace(altered)
                session = self.open()
                self.assertIsNotNone(session.recovery_reason)
                self.assertIsNone(session.exchange)
                session.release()

    def test_incompatible_effective_config_is_not_applied(self):
        self.open().release()
        session = self.open(config=RiskConfig(max_daily_loss_pct=Decimal("2")))
        self.assertIsNotNone(session.recovery_reason)
        self.assertIsNone(session.exchange)

    def test_pending_intent_is_not_replayed(self):
        first = self.open()
        first.exchange.set_price("BTC/USDT", Decimal("100"))
        first.begin_intent("buy", "BTC/USDT", amount=Decimal("1"), price=Decimal("100"))
        before = first.snapshot()
        first.release()
        second = self.open()
        self.assertIn("intent", second.recovery_reason)
        self.assertEqual(second.snapshot(), before)
        self.assertEqual(second.exchange.snapshot()["orders"], {})
        second.save()
        self.assertEqual(second.snapshot(), before)

    def test_broker_portfolio_mismatch_never_recreates_broker(self):
        first = self.open()
        saved = first.snapshot()
        first.release()
        saved["portfolio"]["cash_balance"] = "20000"
        self.replace(saved)
        second = self.open()
        self.assertIsNotNone(second.recovery_reason)
        self.assertIsNone(second.exchange)

    def test_missing_or_nonpositive_baseline_blocks_entry(self):
        first = self.open()
        saved = first.snapshot()
        first.release()
        for baseline in (None, "0", "-1", "NaN", "Infinity"):
            with self.subTest(baseline=baseline):
                altered = copy.deepcopy(saved)
                if baseline is None:
                    altered["accounting"].pop("daily_start_value")
                else:
                    altered["accounting"]["daily_start_value"] = baseline
                self.replace(altered)
                second = self.open()
                self.assertIsNotNone(second.recovery_reason)
                self.assertFalse(second.entry_allowed()[0])
                second.release()


if __name__ == "__main__":
    unittest.main()
