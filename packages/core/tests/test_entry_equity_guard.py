"""Reject new legacy entries at nonpositive equity without blocking local exits."""

from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from trading_core.portfolio import Portfolio
from trading_core.risk_manager import RiskConfig, RiskManager


class EntryEquityGuardTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Entry checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)

    def assert_entry_rejected(self, portfolio, amount=Decimal("1")):
        before = deepcopy(portfolio.to_dict())
        manager = RiskManager(RiskConfig(), portfolio)
        allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", amount, Decimal("100"))
        self.assertFalse(allowed)
        self.assertIn("equity", reason.lower())
        self.assertEqual(portfolio.to_dict(), before)

    def test_nonpositive_initial_equity_cannot_authorize_positive_entries(self):
        for balance in ("0", "-1000"):
            with self.subTest(balance=balance):
                self.assert_entry_rejected(Portfolio(Decimal(balance)))

    def test_unsized_buy_signal_cannot_bypass_nonpositive_equity(self):
        # The application passes amount=0 before calculating its final buy size.
        for balance in ("0", "-1000"):
            with self.subTest(balance=balance):
                self.assert_entry_rejected(Portfolio(Decimal(balance)), Decimal("0"))

    def test_total_realized_loss_then_explicit_reset_does_not_reenable_entries(self):
        portfolio = Portfolio(Decimal("1000"))
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("1000"))
        portfolio.close_position("BTC/USDT", Decimal("0"))
        portfolio.reset_daily()
        self.assertEqual(portfolio.get_total_value(), Decimal("0"))
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assert_entry_rejected(portfolio)

    def test_current_equity_is_checked_instead_of_original_starting_balance(self):
        portfolio = Portfolio.from_dict({"initial_balance": "1000", "cash_balance": "-10"})
        self.assert_entry_rejected(portfolio)

    def test_queries_and_non_entry_requests_keep_existing_behavior(self):
        for balance in ("0", "-1000"):
            with self.subTest(balance=balance):
                portfolio = Portfolio(Decimal(balance))
                manager = RiskManager(RiskConfig(), portfolio)
                before = deepcopy(portfolio.to_dict())
                self.assertIsInstance(manager.get_risk_report(), dict)
                self.assertTrue(manager.is_trading_allowed())
                self.assertEqual(manager.check_trade_allowed("BTC/USDT", "sell", Decimal("1"), Decimal("100")),
                                 (True, "OK"))
                self.assertEqual(portfolio.to_dict(), before)

    def test_local_position_close_remains_possible_at_negative_equity(self):
        portfolio = Portfolio.from_dict({"initial_balance": "1000", "cash_balance": "-10", "positions": {
            "BTC/USDT": {"symbol": "BTC/USDT", "side": "long", "size": "1",
                         "entry_price": "5", "opened_at": "2026-10-04T00:00:00"}}})
        self.assertEqual(portfolio.get_total_value(), Decimal("-5"))
        self.assert_entry_rejected(portfolio)
        trade = portfolio.close_position("BTC/USDT", Decimal("5"))
        self.assertEqual(trade.pnl, Decimal("0"))
        self.assertFalse(portfolio.has_position("BTC/USDT"))
        self.assertEqual(portfolio.cash_balance, Decimal("-5"))

    def test_positive_equity_preserves_valid_entries_and_existing_guards(self):
        portfolio = Portfolio(Decimal("1000"))
        manager = RiskManager(RiskConfig(), portfolio)
        self.assertEqual(manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.1"), Decimal("100")),
                         (True, "OK"))
        allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", Decimal("2"), Decimal("100"))
        self.assertFalse(allowed)
        self.assertIn("Position size", reason)
        with self.assertRaisesRegex(ValueError, "Insufficient balance"):
            portfolio.open_position("BTC/USDT", "long", Decimal("2"), Decimal("1000"))
        self.assertEqual(portfolio.cash_balance, Decimal("1000"))


if __name__ == "__main__":
    unittest.main()
