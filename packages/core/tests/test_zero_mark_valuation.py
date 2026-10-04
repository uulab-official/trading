"""Zero is an observed price, not the legacy unknown-mark sentinel."""

from decimal import Decimal
import unittest
from unittest.mock import patch

from trading_core.portfolio import Portfolio
from trading_core.risk_manager import RiskConfig, RiskManager


class ZeroMarkValuationTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Valuation checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)

    def account(self):
        portfolio = Portfolio(Decimal("1000"))
        position = portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("1000"))
        portfolio.update_position_prices({"BTC/USDT": Decimal("0")})
        return portfolio, position

    def test_observed_zero_mark_has_zero_position_and_total_value(self):
        portfolio, position = self.account()
        self.assertEqual(position.current_price, Decimal("0"))
        self.assertEqual(position.unrealized_pnl, Decimal("-1000"))
        self.assertEqual(position.position_value, Decimal("0"))
        self.assertEqual(portfolio.get_total_value(), Decimal("0"))

    def test_zero_mark_cannot_authorize_sized_or_unsized_buy(self):
        for amount in (Decimal("0"), Decimal("0.1")):
            with self.subTest(amount=amount):
                portfolio, _ = self.account()
                manager = RiskManager(RiskConfig(), portfolio)
                allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", amount, Decimal("100"))
                self.assertFalse(allowed)
                self.assertIn("equity", reason.lower())

    def test_unknown_mark_is_none_and_keeps_entry_valuation(self):
        from trading_core.portfolio import Position
        position = Position("BTC/USDT", "long", Decimal("2"), Decimal("10"),
                            stop_loss_price=Decimal("8"), take_profit_price=Decimal("12"))
        self.assertIsNone(position.current_price)
        self.assertEqual(position.position_value, Decimal("20"))
        self.assertEqual(position.unrealized_pnl, Decimal("0"))
        self.assertFalse(position.should_stop_loss())
        self.assertFalse(position.should_take_profit())

    def test_explicit_constructor_mark_recomputes_unrealized_pnl(self):
        from trading_core.portfolio import Position
        for mark, value, pnl in (("0", "0", "-20"), ("12", "24", "4"), ("-1", "-2", "-22")):
            with self.subTest(mark=mark):
                position = Position("BTC/USDT", "long", Decimal("2"), Decimal("10"),
                                    current_price=Decimal(mark))
                self.assertEqual(position.position_value, Decimal(value))
                self.assertEqual(position.unrealized_pnl, Decimal(pnl))

    def test_zero_and_recovery_updates_flow_through_reports_and_sizing(self):
        portfolio, position = self.account()
        self.assertEqual(portfolio.get_stats()["total_value"], 0.0)
        self.assertEqual(portfolio.get_stats()["position_value"], 0.0)
        self.assertEqual(portfolio.get_position_report()[0]["current_price"], 0.0)
        manager = RiskManager(RiskConfig(), portfolio)
        self.assertEqual(manager.calculate_position_size(Decimal("100"), Decimal("98")), Decimal("0"))
        portfolio.update_position_prices({"BTC/USDT": Decimal("20")})
        self.assertEqual(position.position_value, Decimal("20"))
        self.assertEqual(position.unrealized_pnl, Decimal("-980"))
        self.assertEqual(portfolio.get_total_value(), Decimal("20"))
        self.assertEqual(manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100")),
                         (True, "OK"))

    def test_unknown_report_and_missing_update_remain_unknown(self):
        portfolio = Portfolio(Decimal("1000"))
        position = portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        portfolio.update_position_prices({"ETH/USDT": Decimal("0")})
        self.assertIsNone(position.current_price)
        self.assertIsNone(portfolio.get_position_report()[0]["current_price"])
        self.assertEqual(portfolio.get_total_value(), Decimal("1000"))

    def test_zero_mark_can_trigger_stop_and_local_close_remains_possible(self):
        portfolio, position = self.account()
        position.stop_loss_price = Decimal("900")
        position.take_profit_price = Decimal("1100")
        self.assertTrue(position.should_stop_loss())
        self.assertFalse(position.should_take_profit())
        # The equity guard must never prevent this local risk-reducing close.
        trade = portfolio.close_position("BTC/USDT", Decimal("0"))
        self.assertEqual(trade.pnl, Decimal("-1000"))
        self.assertEqual(portfolio.cash_balance, Decimal("0"))
        self.assertFalse(portfolio.has_position("BTC/USDT"))

    def test_json_roundtrip_preserves_unknown_zero_positive_and_negative_marks(self):
        import json
        for mark in (None, Decimal("0"), Decimal("10.123456789123456789"), Decimal("-1")):
            with self.subTest(mark=mark):
                portfolio = Portfolio(Decimal("1000"))
                portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("1000"))
                if mark is not None:
                    portfolio.update_position_prices({"BTC/USDT": mark})
                saved = json.loads(json.dumps(portfolio.to_dict()))
                expected_mark = str(mark) if mark is not None else None
                self.assertEqual(saved["positions"]["BTC/USDT"]["current_price"], expected_mark)
                restored = Portfolio.from_dict(saved)
                self.assertEqual(restored.get_position("BTC/USDT").current_price, mark)
                self.assertEqual(restored.get_total_value(), portfolio.get_total_value())
                self.assertEqual(restored.get_unrealized_pnl(), portfolio.get_unrealized_pnl())
                self.assertEqual(restored.to_dict(), saved)

    def test_legacy_missing_mark_and_explicit_null_restore_as_unknown(self):
        for include_null in (False, True):
            with self.subTest(include_null=include_null):
                row = {"symbol": "BTC/USDT", "side": "long", "size": "1", "entry_price": "1000",
                       "opened_at": "2026-10-04T00:00:00"}
                if include_null:
                    row["current_price"] = None
                restored = Portfolio.from_dict({"cash_balance": "0", "positions": {"BTC/USDT": row}})
                self.assertIsNone(restored.get_position("BTC/USDT").current_price)
                self.assertEqual(restored.get_total_value(), Decimal("1000"))
                self.assertIsNone(restored.get_position_report()[0]["current_price"])

    def test_numeric_zero_in_saved_payload_is_observed_zero(self):
        portfolio, _ = self.account()
        data = portfolio.to_dict()
        data["positions"]["BTC/USDT"]["current_price"] = 0
        restored = Portfolio.from_dict(data)
        self.assertEqual(restored.get_total_value(), Decimal("0"))
        self.assertEqual(restored.get_unrealized_pnl(), Decimal("-1000"))

    def test_real_state_stores_preserve_zero_mark_and_reject_buy_after_restore(self):
        from pathlib import Path
        import tempfile
        from trading_core.state_store import BotState, JSONStateStore, SQLiteStateStore
        with tempfile.TemporaryDirectory() as directory:
            for store in (JSONStateStore(str(Path(directory) / "json")),
                          SQLiteStateStore(str(Path(directory) / "state.db"))):
                with self.subTest(store=type(store).__name__):
                    portfolio, _ = self.account()
                    state = BotState(store)
                    state.save_portfolio(portfolio.to_dict())
                    restored = Portfolio.from_dict(state.load_portfolio())
                    self.assertEqual(restored.get_total_value(), Decimal("0"))
                    self.assertEqual(restored.get_unrealized_pnl(), Decimal("-1000"))
                    manager = RiskManager(RiskConfig(), restored)
                    allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0"), Decimal("100"))
                    self.assertFalse(allowed)
                    self.assertIn("equity", reason.lower())

    def test_explicit_daily_reset_uses_observed_zero_valuation(self):
        portfolio, _ = self.account()
        portfolio.reset_daily()
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "0")
        self.assertEqual(portfolio.get_daily_pnl_pct(), Decimal("0"))
        allowed, reason = RiskManager(RiskConfig(), portfolio).check_trade_allowed(
            "ETH/USDT", "buy", Decimal("0"), Decimal("100"))
        self.assertFalse(allowed)
        self.assertIn("equity", reason.lower())

    def test_malformed_or_nonfinite_saved_marks_are_rejected(self):
        from copy import deepcopy
        for mark in ("", "bad", "NaN", "sNaN", "Infinity", "-Infinity", True, [], {}):
            with self.subTest(mark=mark):
                portfolio, _ = self.account()
                data = portfolio.to_dict()
                data["positions"]["BTC/USDT"]["current_price"] = mark
                before = deepcopy(data)
                with self.assertRaisesRegex(ValueError, "[Cc]urrent price"):
                    Portfolio.from_dict(data)
                self.assertEqual(data, before)

    def test_nonfinite_price_update_does_not_mutate_last_known_mark_or_pnl(self):
        from copy import deepcopy
        for mark in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(mark=mark):
                portfolio, position = self.account()
                before = deepcopy(position)
                with self.assertRaisesRegex(ValueError, "[Cc]urrent price"):
                    portfolio.update_position_prices({"BTC/USDT": Decimal(mark)})
                self.assertEqual(position, before)

    def test_unknown_and_zero_marks_do_not_confuse_short_price_triggers(self):
        from trading_core.portfolio import Position
        position = Position("BTC/USDT", "short", Decimal("1"), Decimal("1000"),
                            stop_loss_price=Decimal("1100"), take_profit_price=Decimal("900"))
        self.assertFalse(position.should_stop_loss())
        self.assertFalse(position.should_take_profit())
        position.update_price(Decimal("0"))
        self.assertFalse(position.should_stop_loss())
        self.assertTrue(position.should_take_profit())
        self.assertEqual(position.unrealized_pnl, Decimal("1000"))

    def test_failed_restore_leaves_store_bytes_schema_and_existing_object_unchanged(self):
        from copy import deepcopy
        from pathlib import Path
        import sqlite3
        import tempfile
        from trading_core.state_store import BotState, JSONStateStore, SQLiteStateStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stores = (JSONStateStore(str(root / "json")), SQLiteStateStore(str(root / "state.db")))
            for store in stores:
                for mark in ("bad", "NaN", "Infinity", "-Infinity"):
                    with self.subTest(store=type(store).__name__, mark=mark):
                        portfolio, _ = self.account()
                        previous = portfolio
                        previous_state = deepcopy(portfolio.to_dict())
                        payload = deepcopy(previous_state)
                        payload["positions"]["BTC/USDT"]["current_price"] = mark
                        state = BotState(store)
                        state.save_portfolio(payload)
                        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                        with sqlite3.connect(root / "state.db") as connection:
                            schema_before = connection.execute("SELECT sql FROM sqlite_master").fetchall()
                        with self.assertRaises(ValueError):
                            portfolio = Portfolio.from_dict(state.load_portfolio())
                        self.assertIs(portfolio, previous)
                        self.assertEqual(portfolio.to_dict(), previous_state)
                        self.assertEqual(state.load_portfolio(), payload)
                        self.assertEqual({p: p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)
                        with sqlite3.connect(root / "state.db") as connection:
                            self.assertEqual(connection.execute("SELECT sql FROM sqlite_master").fetchall(),
                                             schema_before)


if __name__ == "__main__":
    unittest.main()
