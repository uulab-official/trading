"""Actual legacy API regressions for the UTC daily-reset boundary.

These tests do not construct a bot, exchange, service, or external client.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from trading_core.portfolio import Portfolio
from trading_core.risk_manager import RiskConfig, RiskManager


class UtcDailyBoundaryTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("UTC boundary checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)

        class Clock(datetime):
            current = datetime(2026, 10, 4, 23, 59, 50)

            @classmethod
            def utcnow(cls):
                return cls.current

            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return cls.current.replace(tzinfo=timezone.utc).astimezone(tz)
                # Simulate a machine whose local date is already tomorrow.
                return cls.current + timedelta(hours=9)

        self.clock = Clock
        for target in ("trading_core.portfolio.datetime", "trading_core.risk_manager.datetime"):
            guard = patch(target, Clock)
            guard.start()
            self.addCleanup(guard.stop)

    def account(self):
        portfolio = Portfolio(Decimal("1000"))
        manager = RiskManager(RiskConfig(cooldown_minutes_after_loss=0), portfolio)
        return portfolio, manager

    def close_trade(self, portfolio, manager, exit_price):
        trade = portfolio.close_position("BTC/USDT", Decimal(exit_price))
        manager.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)
        return trade

    def carry_position_into_next_utc_day(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "90")  # Prior-day realized loss: 10.
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.assertEqual(portfolio.get_total_value(), Decimal("990"))
        self.clock.current += timedelta(seconds=20)
        return portfolio, manager

    def assert_current_day_loss(self, portfolio, manager):
        report = manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], -60)
        self.assertEqual(report["daily_trades"], 1)
        self.assertEqual(report["daily_losses"], 1)
        self.assertTrue(report["circuit_breaker"])
        self.assertEqual(report["daily_pnl_pct"], float(Decimal("-60") / Decimal("990") * 100))
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "990")
        self.assertEqual(portfolio.cash_balance, Decimal("930"))
        self.assertFalse(portfolio.has_position("BTC/USDT"))
        return report

    def test_first_close_after_midnight_preserves_loss_and_latch_before_report(self):
        portfolio, manager = self.carry_position_into_next_utc_day()
        self.assertEqual(self.close_trade(portfolio, manager, "40").pnl, Decimal("-60"))
        self.assert_current_day_loss(portfolio, manager)

    def test_first_close_then_check_then_report_keeps_new_loss(self):
        portfolio, manager = self.carry_position_into_next_utc_day()
        self.close_trade(portfolio, manager, "40")
        allowed, reason = manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100"))
        self.assertFalse(allowed)
        self.assertIn("Circuit breaker", reason)
        self.assert_current_day_loss(portfolio, manager)

    def test_repeated_reset_check_and_report_do_not_erase_first_close(self):
        portfolio, manager = self.carry_position_into_next_utc_day()
        self.close_trade(portfolio, manager, "40")
        for _ in range(3):
            manager.reset_daily_stats()
            self.assertFalse(manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100"))[0])
            self.assert_current_day_loss(portfolio, manager)

    def test_first_action_is_risk_reset_and_repeated_calls_do_not_rebase(self):
        portfolio, manager = self.carry_position_into_next_utc_day()
        manager.reset_daily_stats()
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "990")
        self.assertEqual(manager.get_risk_report()["daily_trades"], 0)
        self.close_trade(portfolio, manager, "40")
        for _ in range(3):
            manager.reset_daily_stats()
            self.assert_current_day_loss(portfolio, manager)

    def test_same_utc_day_keeps_loss_even_when_local_calendar_is_tomorrow(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "90")
        self.clock.current += timedelta(seconds=5)
        self.assertEqual(self.clock.utcnow().date().isoformat(), "2026-10-04")
        self.assertEqual(self.clock.now().date().isoformat(), "2026-10-05")
        for _ in range(3):
            manager.reset_daily_stats()
            report = manager.get_risk_report()
            self.assertEqual(report["daily_pnl"], -10)
            self.assertEqual(report["daily_pnl_pct"], -1)
            self.assertEqual(report["daily_trades"], 1)
            self.assertFalse(report["circuit_breaker"])

    def test_old_day_close_callback_after_midnight_does_not_leak_into_new_day(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = portfolio.close_position("BTC/USDT", Decimal("40"))
        self.clock.current += timedelta(seconds=20)
        manager.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)
        report = manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], 0)
        self.assertEqual(report["daily_trades"], 0)
        self.assertFalse(report["circuit_breaker"])
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "940")

    def test_check_between_close_and_callback_does_not_charge_old_loss_to_new_day(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = portfolio.close_position("BTC/USDT", Decimal("40"))
        self.clock.current += timedelta(seconds=20)
        manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100"))
        manager.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)
        report = manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], 0)
        self.assertEqual(report["daily_trades"], 0)
        self.assertEqual(report["daily_losses"], 0)
        self.assertFalse(report["cooldown_active"])
        self.assertFalse(report["circuit_breaker"])
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))

    def test_new_close_between_old_close_and_callback_retains_only_new_day_loss(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        portfolio.open_position("ETH/USDT", "long", Decimal("1"), Decimal("100"))
        old_trade = portfolio.close_position("BTC/USDT", Decimal("40"))
        self.clock.current += timedelta(seconds=20)
        new_trade = portfolio.close_position("ETH/USDT", Decimal("90"))
        manager.on_position_closed(new_trade.pnl, accounting_date=new_trade.accounting_date)
        manager.on_position_closed(old_trade.pnl, accounting_date=old_trade.accounting_date)
        report = manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], -10)
        self.assertEqual(report["daily_trades"], 1)
        self.assertEqual(report["daily_losses"], 1)
        self.assertFalse(report["circuit_breaker"])
        self.assertEqual(report["daily_pnl_pct"], float(Decimal("-10") / Decimal("940") * 100))
        self.assertEqual(portfolio.cash_balance, Decimal("930"))

    def test_explicit_same_day_reset_still_rebases_but_does_not_unlatch_risk(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "40")
        portfolio.reset_daily()
        self.assertEqual(portfolio.cash_balance, Decimal("940"))
        self.assertEqual(portfolio.get_daily_pnl_pct(), Decimal("0"))
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "940")
        self.assertEqual(manager.get_risk_report()["daily_trades"], 1)
        self.assertTrue(manager.get_risk_report()["circuit_breaker"])
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "90.6")
        for _ in range(2):
            manager.reset_daily_stats()
            self.assertEqual(portfolio.get_daily_pnl_pct(), Decimal("-1"))
            self.assertTrue(manager.get_risk_report()["circuit_breaker"])

    def test_explicit_reset_during_clock_rollback_does_not_lower_period(self):
        self.clock.current = datetime(2026, 10, 5, 0, 0, 10)
        portfolio, manager = self.account()
        self.clock.current -= timedelta(seconds=20)
        portfolio.reset_daily()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = self.close_trade(portfolio, manager, "40")
        self.assertEqual(trade.accounting_date.isoformat(), "2026-10-05")
        self.clock.current += timedelta(seconds=20)
        self.assertTrue(manager.get_risk_report()["circuit_breaker"])
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("6"))

    def test_exit_time_is_metadata_and_new_period_is_processing_utc(self):
        portfolio, manager = self.carry_position_into_next_utc_day()
        supplied_time = datetime(2000, 1, 1)
        trade = portfolio.close_position("BTC/USDT", Decimal("40"), exit_time=supplied_time)
        self.assertEqual(trade.closed_at, supplied_time)
        self.assertEqual(trade.accounting_date, self.clock.utcnow().date())
        manager.on_position_closed(trade.pnl, accounting_date=trade.accounting_date)
        self.assert_current_day_loss(portfolio, manager)

    def test_invalid_or_future_callback_period_cannot_mutate_account_or_risk(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "40")
        expected_state = portfolio.to_dict()
        expected_report = manager.get_risk_report()
        period = portfolio.daily_date
        invalid_inputs = [("2026-10-04", TypeError), (1, TypeError),
                          (self.clock.utcnow(), TypeError),
                          (period + timedelta(days=1), ValueError)]
        for value, error in invalid_inputs:
            with self.subTest(value=value), self.assertRaises(error):
                manager.on_position_closed(Decimal("500"), accounting_date=value)
            self.assertEqual(portfolio.daily_date, period)
            self.assertEqual(portfolio.to_dict(), expected_state)
            self.assertEqual(manager.get_risk_report(), expected_report)

    def test_period_metadata_does_not_change_saved_state_or_report_schemas(self):
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        trade = self.close_trade(portfolio, manager, "90")
        self.assertEqual(trade.accounting_date, portfolio.daily_date)
        saved = json.loads(json.dumps(portfolio.to_dict()))
        self.assertEqual(set(saved), {"initial_balance", "cash_balance", "positions", "trades",
                                    "daily_start_value", "total_pnl", "win_count", "loss_count"})
        self.assertEqual(set(saved["trades"][0]), {"symbol", "pnl", "pnl_pct", "closed_at"})
        self.assertNotIn("accounting_date", json.dumps(saved))
        self.assertEqual(set(manager.get_risk_report()), {"daily_pnl", "daily_pnl_pct", "daily_trades",
                         "daily_losses", "open_positions", "circuit_breaker", "cooldown_active", "cooldown_remaining"})
        restored = Portfolio.from_dict(saved)
        self.assertEqual(restored.to_dict()["cash_balance"], saved["cash_balance"])
        # This remains the legacy persistence limitation, not restart recovery.
        self.assertEqual(restored.get_daily_pnl_pct(), Decimal("0"))

    def test_aware_observation_uses_utc_date_not_the_supplied_local_date(self):
        self.clock.current = datetime(2026, 10, 4, 23)
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "40")
        same_instant = datetime(2026, 10, 5, 8, tzinfo=timezone(timedelta(hours=9)))
        portfolio.ensure_daily_period(same_instant)
        self.assertEqual(portfolio.daily_date, self.clock.utcnow().date())
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("6"))
        self.assertTrue(manager.get_risk_report()["circuit_breaker"])
        # This is October 5 in UTC even though the supplied local date is Oct 4.
        next_utc_day = datetime(2026, 10, 4, 15, tzinfo=timezone(timedelta(hours=-10)))
        portfolio.ensure_daily_period(next_utc_day)
        self.assertEqual(portfolio.daily_date.isoformat(), "2026-10-05")
        self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("0"))
        self.assertEqual(portfolio.to_dict()["daily_start_value"], "940")

    def test_clock_rollback_cannot_clear_loss_or_circuit_latch(self):
        self.clock.current = datetime(2026, 10, 5, 0, 0, 10)
        portfolio, manager = self.account()
        portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.close_trade(portfolio, manager, "40")
        for seconds in (-20, 20):  # Roll back into yesterday, then restore time.
            self.clock.current += timedelta(seconds=seconds)
            for _ in range(2):
                manager.reset_daily_stats()
                report = manager.get_risk_report()
                self.assertEqual(report["daily_pnl"], -60)
                self.assertEqual(report["daily_pnl_pct"], -6)
                self.assertEqual(report["daily_trades"], 1)
                self.assertTrue(report["circuit_breaker"])
                self.assertEqual(portfolio.get_daily_loss_pct(), Decimal("6"))
                self.assertFalse(manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100"))[0])


if __name__ == "__main__":
    unittest.main()
