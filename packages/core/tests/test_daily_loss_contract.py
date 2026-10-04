"""Offline realized-loss limits through the legacy Portfolio/RiskManager API."""

from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from trading_core.portfolio import Portfolio
from trading_core.risk_manager import RiskConfig, RiskManager


class DailyLossContractTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Risk contract checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)

    def realize(self, pnl, *, daily_limit="3", circuit_limit="100"):
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        manager = RiskManager(RiskConfig(max_daily_loss_pct=Decimal(daily_limit),
                                        circuit_breaker_loss_pct=Decimal(circuit_limit),
                                        cooldown_minutes_after_loss=0), portfolio)
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = portfolio.close_position("BTC/USDT", Decimal("100") + Decimal(pnl))
        manager.on_position_closed(trade.pnl)
        return portfolio, manager

    def test_realized_loss_magnitude_and_signed_report_are_separate(self):
        for pnl, loss_pct, signed_pct in (("0", "0", "0"), ("50", "0", "5"), ("-50", "5", "-5")):
            with self.subTest(pnl=pnl):
                portfolio, manager = self.realize(pnl)
                self.assertEqual(portfolio.get_daily_loss_pct(), Decimal(loss_pct))
                self.assertEqual(portfolio.get_stats()["daily_loss_pct"], float(loss_pct))
                self.assertTrue(hasattr(portfolio, "get_daily_pnl_pct"))
                self.assertEqual(portfolio.get_daily_pnl_pct(), Decimal(signed_pct))
                self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], float(signed_pct))
                self.assertEqual(manager.get_risk_report()["daily_pnl"], float(pnl))

    def test_daily_loss_threshold_blocks_equal_and_above_but_not_below(self):
        for pnl, expected in (("-29.9", True), ("-30", False), ("-30.1", False)):
            with self.subTest(pnl=pnl):
                _, manager = self.realize(pnl)
                allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.1"), Decimal("100"))
                self.assertEqual(allowed, expected, reason)
                self.assertEqual(reason == "OK", expected)
                if not expected:
                    self.assertIn("Daily loss limit", reason)

    def test_circuit_breaker_latches_at_actual_loss_threshold_only(self):
        for pnl, expected in (("50.1", False), ("-49.9", False), ("-50", True), ("-50.1", True)):
            with self.subTest(pnl=pnl):
                _, manager = self.realize(pnl, daily_limit="100", circuit_limit="5")
                self.assertEqual(manager.get_risk_report()["circuit_breaker"], expected)

    def test_profit_never_trips_daily_loss_limit(self):
        _, manager = self.realize("50", circuit_limit="5")
        self.assertEqual(manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.1"), Decimal("100")), (True, "OK"))

    def test_unrealized_marks_do_not_change_realized_daily_loss_contract(self):
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        manager = RiskManager(RiskConfig(), portfolio)
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        portfolio.update_position_prices({"BTC/USDT": Decimal("50")})
        self.assertEqual(portfolio.get_unrealized_pnl(), Decimal("-50"))
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], 0)

    def test_explicit_daily_reset_rebases_realized_loss_without_changing_cash(self):
        now = datetime(2026, 10, 4, 12)
        class Clock(datetime):
            @classmethod
            def utcnow(cls):
                return now
        with patch("trading_core.risk_manager.datetime", Clock):
            portfolio, manager = self.realize("-50", circuit_limit="5")
            manager.reset_daily_stats()
            self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("5"))
            now += timedelta(days=1)
            # Legacy callers must explicitly reset the portfolio. Automatic
            # midnight coordination is a separate, unresolved lifecycle issue.
            portfolio.reset_daily()
            manager.reset_daily_stats()
            self.assertEqual(portfolio.cash_balance, Decimal("950"))
            self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
            self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], 0)
            self.assertFalse(manager.get_risk_report()["circuit_breaker"])
            self.assertEqual(manager.get_risk_report()["daily_trades"], 0)
            portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
            trade = portfolio.close_position("BTC/USDT", Decimal("90.5"))
            manager.on_position_closed(trade.pnl)
            manager.reset_daily_stats()
            self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("1"))
            self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], -1)

    def test_net_realized_recovery_reduces_loss_magnitude(self):
        portfolio, manager = self.realize("-50")
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = portfolio.close_position("BTC/USDT", Decimal("130"))
        manager.on_position_closed(trade.pnl)
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("2"))
        self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], -2)
        self.assertEqual(manager.get_risk_report()["daily_pnl"], -20)

    def test_circuit_breaker_stays_latched_after_same_day_recovery(self):
        portfolio, manager = self.realize("-50", circuit_limit="5")
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = portfolio.close_position("BTC/USDT", Decimal("160"))
        manager.on_position_closed(trade.pnl)
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(manager.get_risk_report()["daily_pnl_pct"], 1)
        self.assertTrue(manager.get_risk_report()["circuit_breaker"])

    def test_zero_baseline_keeps_existing_zero_metric_without_a_safety_claim(self):
        portfolio = Portfolio(initial_balance=Decimal("0"))
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(RiskManager(RiskConfig(), portfolio).get_risk_report()["daily_pnl_pct"], 0)


if __name__ == "__main__":
    unittest.main()
