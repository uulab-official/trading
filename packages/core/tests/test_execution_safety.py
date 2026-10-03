"""Credential-free, network-forbidden tests for execution routing."""

import importlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trading_core.execution import create_execution_exchange, validate_execution_mode
from trading_core.paper_exchange import PaperExchange
from trading_core.config_manager import ConfigManager
from trading_core.order_manager import OrderManager, OrderSide, OrderType, OrderStatus


def config(mode="paper", sandbox=False, testnet=False, keys=True):
    return SimpleNamespace(mode=mode, exchange=SimpleNamespace(
        id="binance", sandbox=sandbox, testnet=testnet,
        api_key="fake-test-key" if keys else "", api_secret="fake-test-secret" if keys else "",
    ))


class RoutingTests(unittest.TestCase):
    def test_default_paper_ignores_credentials_and_never_constructs_ccxt(self):
        with patch("trading_core.exchange.CCXTExchange", side_effect=AssertionError("CCXT forbidden")):
            exchange = create_execution_exchange(config())
        self.assertIsInstance(exchange, PaperExchange)
        self.assertFalse(hasattr(exchange, "api_key"))
        self.assertFalse(hasattr(exchange, "api_secret"))

    def test_activation_flag_does_not_change_paper_mode(self):
        with patch("trading_core.exchange.CCXTExchange", side_effect=AssertionError("CCXT forbidden")):
            self.assertIsInstance(create_execution_exchange(config(), enable_live_trading=True), PaperExchange)

    def test_live_fails_closed_without_explicit_boolean_flag(self):
        for flag in [False, None, "true", 1]:
            with self.subTest(flag=flag), patch("trading_core.exchange.CCXTExchange", side_effect=AssertionError("CCXT forbidden")):
                with self.assertRaises(ValueError):
                    create_execution_exchange(config("live"), enable_live_trading=flag)

    def test_invalid_mode_and_live_configuration_fail_before_construction(self):
        cases = [config("LIVE"), config("papre"), config(None), config("live", keys=False), config("live", sandbox=True), config("live", testnet=True)]
        for case in cases:
            with self.subTest(case=case.mode), patch("trading_core.exchange.CCXTExchange", side_effect=AssertionError("CCXT forbidden")):
                with self.assertRaises(ValueError):
                    create_execution_exchange(case, enable_live_trading=True)

    def test_live_adapter_is_only_constructed_after_guard_with_mock(self):
        with patch("trading_core.exchange.CCXTExchange") as adapter:
            result = create_execution_exchange(config("live"), enable_live_trading=True)
        self.assertIs(result, adapter.return_value)
        adapter.assert_called_once()
        adapter.return_value.connect.assert_not_called()

    def test_paper_loader_does_not_open_secret_file_or_keep_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "active").mkdir()
            (root / "secrets").mkdir()
            (root / "active/bot_config.yml").write_text("bot:\n  mode: paper\nexchange:\n  api_key: inline-fake\n  api_secret: inline-fake\ntelegram:\n  enabled: true\n")
            (root / "secrets/secrets.yml").write_text("this file must never be opened")
            real_open = open
            def guarded_open(path, *args, **kwargs):
                if Path(path) == root / "secrets/secrets.yml":
                    raise AssertionError("paper read secret file")
                return real_open(path, *args, **kwargs)
            with patch("builtins.open", side_effect=guarded_open):
                loaded = ConfigManager(directory).load_config()
            self.assertEqual(loaded.exchange.api_key, "")
            self.assertEqual(loaded.exchange.api_secret, "")
            self.assertFalse(loaded.telegram.enabled)


class PaperExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.guard.start()
        self.exchange = PaperExchange(initial_balance=Decimal("1000"))
        await self.exchange.connect()
        self.exchange.set_price("BTC/USDT", Decimal("100"), timestamp=1)

    async def asyncTearDown(self):
        await self.exchange.disconnect()
        self.guard.stop()

    async def test_market_buy_and_sell_use_local_ledger_only(self):
        buy = await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("2"), params={"clientOrderId": "local-buy"})
        self.assertTrue(buy["id"].startswith("paper-"))
        self.assertEqual(buy["status"], "closed")
        self.assertEqual(buy["filled"], Decimal("2"))
        balances = await self.exchange.fetch_balance()
        self.assertEqual(balances["USDT"].free, Decimal("800"))
        self.assertEqual(balances["BTC"].free, Decimal("2"))
        await self.exchange.create_order("BTC/USDT", "market", "sell", Decimal("2"))
        self.assertEqual((await self.exchange.fetch_balance())["USDT"].free, Decimal("1000"))
        self.assertEqual(len(await self.exchange.fetch_my_trades()), 2)

    async def test_order_manager_routes_to_paper_and_records_immediate_fill(self):
        manager = OrderManager(self.exchange)
        with patch("trading_core.exchange.CCXTExchange.create_order", side_effect=AssertionError("real order forbidden")):
            order = await manager.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), client_order_id="manager-local")
        self.assertTrue(order.exchange_order_id.startswith("paper-"))
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled, Decimal("1"))
        self.assertEqual(order.remaining, Decimal("0"))

    async def test_order_manager_accepts_nullable_mocked_live_response(self):
        adapter = SimpleNamespace(create_order=AsyncMock(return_value={"id": "mock-accepted", "status": None, "filled": None}))
        manager = OrderManager(adapter)
        order = await manager.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"))
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertEqual(order.filled, Decimal("0"))
        self.assertEqual(order.remaining, Decimal("1"))

    async def test_duplicate_local_order_is_not_charged_twice(self):
        first = await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "repeat"})
        second = await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "repeat"})
        self.assertEqual(first, second)
        self.assertEqual((await self.exchange.fetch_balance())["USDT"].free, Decimal("900"))
        with self.assertRaises(ValueError):
            await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("2"), params={"clientOrderId": "repeat"})

    async def test_generated_order_ids_do_not_collide_with_explicit_ids(self):
        first = await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "paper-local-2"})
        second = await self.exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((await self.exchange.fetch_balance())["USDT"].free, Decimal("800"))
        self.assertEqual(len(await self.exchange.fetch_my_trades()), 2)

    async def test_self_pair_is_rejected_without_changing_balance(self):
        with self.assertRaises(ValueError):
            self.exchange.set_price("USDT/USDT", Decimal("100"))
        with self.assertRaises(ValueError):
            PaperExchange(initial_positions={"USDT/USDT": Decimal("1")})
        with self.assertRaises(ValueError):
            await self.exchange.create_order("USDT/USDT", "market", "sell", Decimal("1"))
        self.assertEqual((await self.exchange.fetch_balance())["USDT"].free, Decimal("1000"))

    async def test_invalid_or_unfunded_orders_leave_ledger_unchanged(self):
        cases = [("buy", Decimal("0")), ("buy", Decimal("-1")), ("buy", Decimal("NaN")), ("buy", Decimal("Infinity")), ("buy", Decimal("11")), ("sell", Decimal("1"))]
        for side, amount in cases:
            with self.subTest(side=side, amount=amount), self.assertRaises(ValueError):
                await self.exchange.create_order("BTC/USDT", "market", side, amount)
        self.assertEqual((await self.exchange.fetch_balance())["USDT"].free, Decimal("1000"))
        self.assertEqual(await self.exchange.fetch_my_trades(), [])

    async def test_missing_quote_fails_without_network_fallback(self):
        with self.assertRaises(ValueError):
            await self.exchange.create_order("ETH/USDT", "market", "buy", Decimal("1"))

    async def test_recorded_candles_are_local_copies(self):
        candles = [[1, 100, 105, 95, 102, 10], [2, 102, 110, 101, 109, 12]]
        self.exchange.set_ohlcv("BTC/USDT", "1h", candles)
        candles[-1][4] = 999
        result = await self.exchange.fetch_ohlcv("BTC/USDT", "1h", limit=1)
        self.assertEqual(result[0][4], 109)
        self.assertEqual((await self.exchange.fetch_ticker("BTC/USDT")).last, Decimal("109"))
        self.assertEqual(await self.exchange.fetch_ohlcv("BTC/USDT", "4h"), [])


if __name__ == "__main__":
    unittest.main()
