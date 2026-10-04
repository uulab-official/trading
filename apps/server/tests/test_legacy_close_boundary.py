"""Exercise the actual legacy close method with local, inert collaborators."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import importlib
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from trading_core.portfolio import Portfolio
from trading_core.risk_manager import RiskConfig, RiskManager


class LegacyCloseBoundaryTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Legacy close checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)
        # Import the method without opening log files or constructing a bot/client.
        with patch("logging.FileHandler", return_value=logging.NullHandler()), patch("logging.basicConfig"):
            self.bot_class = importlib.import_module("trading_bot.main").TradingBot

        class Clock(datetime):
            current = datetime(2026, 10, 4, 23, 59, 50)

            @classmethod
            def utcnow(cls):
                return cls.current

        self.clock = Clock
        for target in ("trading_core.portfolio.datetime", "trading_core.risk_manager.datetime"):
            guard = patch(target, Clock)
            guard.start()
            self.addCleanup(guard.stop)
        self.portfolio = Portfolio(Decimal("1000"))
        self.manager = RiskManager(RiskConfig(cooldown_minutes_after_loss=0), self.portfolio)
        self.portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("100"))
        self.callback = Mock(wraps=self.manager.on_position_closed)
        self.manager.on_position_closed = self.callback
        self.bot = SimpleNamespace(
            portfolio=self.portfolio,
            risk_manager=self.manager,
            order_manager=SimpleNamespace(place_order=AsyncMock()),
            notifier=SimpleNamespace(notify_position_closed=AsyncMock(), notify_error=AsyncMock()),
        )

    def close(self):
        asyncio.run(self.bot_class._close_position(self.bot, "BTC/USDT", Decimal("40"), "offline test"))
        self.bot.order_manager.place_order.assert_awaited_once()
        self.bot.notifier.notify_position_closed.assert_awaited_once()
        self.bot.notifier.notify_error.assert_not_awaited()
        self.callback.assert_called_once()
        self.assertEqual(self.callback.call_args.kwargs["accounting_date"],
                         self.portfolio._trades[-1].accounting_date)

    def test_fill_await_crossing_midnight_records_new_day_loss(self):
        async def inert_fill(**kwargs):
            self.clock.current += timedelta(seconds=20)
        self.bot.order_manager.place_order.side_effect = inert_fill
        self.close()
        report = self.manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], -60)
        self.assertEqual(report["daily_trades"], 1)
        self.assertTrue(report["circuit_breaker"])

    def test_check_after_accounting_before_callback_does_not_reassign_old_close(self):
        original_close = self.portfolio.close_position

        def close_then_check(*args, **kwargs):
            trade = original_close(*args, **kwargs)
            self.clock.current += timedelta(seconds=20)
            self.manager.check_trade_allowed("ETH/USDT", "buy", Decimal("0.01"), Decimal("100"))
            return trade

        with patch.object(self.portfolio, "close_position", side_effect=close_then_check):
            self.close()
        report = self.manager.get_risk_report()
        self.assertEqual(report["daily_pnl"], 0)
        self.assertEqual(report["daily_trades"], 0)
        self.assertFalse(report["circuit_breaker"])
        self.assertFalse(report["cooldown_active"])


if __name__ == "__main__":
    unittest.main()
