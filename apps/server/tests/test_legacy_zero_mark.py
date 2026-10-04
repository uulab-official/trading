"""Actual legacy quote callback: missing marks are unknown, zero marks are real."""

import asyncio
from decimal import Decimal
import importlib
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from trading_core.portfolio import Portfolio


class LegacyZeroMarkTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Quote checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)
        with patch("logging.FileHandler", return_value=logging.NullHandler()), patch("logging.basicConfig"):
            self.bot_class = importlib.import_module("trading_bot.main").TradingBot
        self.portfolio = Portfolio(Decimal("1000"))
        self.position = self.portfolio.open_position("BTC/USDT", "long", Decimal("1"), Decimal("1000"))
        self.closes = []

        async def close_locally(symbol, price, reason):
            self.closes.append((reason, self.portfolio.close_position(symbol, price)))

        self.bot = SimpleNamespace(portfolio=self.portfolio, _close_position=close_locally)

    def update(self, data):
        asyncio.run(self.bot_class._on_price_update(self.bot, data))

    def test_zero_callback_updates_equity_and_unrealized_pnl(self):
        self.update({"symbol": "BTC/USDT", "price": Decimal("0")})
        self.assertEqual(self.portfolio.get_total_value(), Decimal("0"))
        self.assertEqual(self.position.unrealized_pnl, Decimal("-1000"))

    def test_zero_quote_updates_valuation_without_automatic_close(self):
        self.position.stop_loss_price = Decimal("900")
        for mark in ("0", "-0"):
            with self.subTest(mark=mark):
                self.update({"symbol": "BTC/USDT", "price": Decimal(mark)})
                self.assertTrue(self.portfolio.has_position("BTC/USDT"))
                self.assertEqual(self.portfolio.get_total_value(), Decimal(mark))
                self.assertEqual(self.closes, [])

    def test_positive_quote_retains_existing_stop_loss_path(self):
        self.position.stop_loss_price = Decimal("900")
        self.update({"symbol": "BTC/USDT", "price": Decimal("800")})
        self.assertFalse(self.portfolio.has_position("BTC/USDT"))
        self.assertEqual(len(self.closes), 1)
        self.assertEqual(self.closes[0][0], "stop_loss")
        self.assertEqual(self.closes[0][1].pnl, Decimal("-200"))

    def test_malformed_or_nonfinite_quotes_do_not_change_valuation_or_close(self):
        self.position.stop_loss_price = Decimal("900")
        self.portfolio.update_position_prices({"BTC/USDT": Decimal("950")})
        for mark in ("0", "bad", 0, True, [], {}, Decimal("NaN"), Decimal("sNaN"),
                     Decimal("Infinity"), Decimal("-Infinity"), Decimal("-1")):
            with self.subTest(mark=mark):
                self.update({"symbol": "BTC/USDT", "price": mark})
                self.assertEqual(self.position.current_price, Decimal("950"))
                self.assertEqual(self.position.unrealized_pnl, Decimal("-50"))
                self.assertEqual(self.closes, [])

    def test_missing_or_null_callback_price_leaves_existing_mark_unchanged(self):
        self.portfolio.update_position_prices({"BTC/USDT": Decimal("800")})
        for data in ({"symbol": "BTC/USDT"}, {"symbol": "BTC/USDT", "price": None}):
            with self.subTest(data=data):
                self.update(data)
                self.assertEqual(self.position.current_price, Decimal("800"))
                self.assertEqual(self.portfolio.get_total_value(), Decimal("800"))
                self.assertEqual(self.closes, [])


if __name__ == "__main__":
    unittest.main()
