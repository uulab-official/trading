"""Explicit KRW paper units, cross-currency rejection and legacy USDT compatibility."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_core.paper_exchange import PaperExchange
from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from test_paper_runner import rows, scripted


class PaperCurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Currency checks forbid network/DNS"))
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch("trading_core.exchange.CCXTExchange", side_effect=AssertionError("Paper cannot construct a live exchange"))
        guard.start()
        self.addCleanup(guard.stop)

    def krw(self, **kwargs):
        return PaperExchange(initial_balance=Decimal("1000000"), quote_currency="KRW", **kwargs)

    async def test_krw_ledger_uses_real_won_units_for_friction_and_valuation(self):
        exchange = self.krw(fee_pct=Decimal("0.1"), slippage_pct=Decimal("0.05"))
        await exchange.connect()
        self.assertEqual(set(await exchange.fetch_balance()), {"KRW"})
        exchange.set_price("BTC/KRW", Decimal("100000000"), timestamp=1)
        order = await exchange.create_order("BTC/KRW", "market", "buy", Decimal("0.001"))
        self.assertEqual(order["average"], Decimal("100050000"))
        self.assertEqual(order["fee"], {"cost": Decimal("100.05"), "currency": "KRW"})
        self.assertEqual((await exchange.fetch_balance())["KRW"].free, Decimal("899849.95"))
        self.assertEqual(exchange.get_total_value(), Decimal("999849.95"))
        exchange.set_price("BTC/KRW", Decimal("110000000"), timestamp=2)
        await exchange.create_order("BTC/KRW", "market", "sell", Decimal("0.001"))
        self.assertEqual(exchange.get_total_value(), Decimal("1009685.005"))
        self.assertEqual(exchange.total_fees, Decimal("209.995"))

    async def test_krw_snapshot_has_explicit_currency_and_exact_deduplicated_restore(self):
        exchange = self.krw(fee_pct=Decimal("0.1"))
        await exchange.connect()
        exchange.set_price("BTC/KRW", Decimal("100000000"), timestamp=1)
        first = await exchange.create_order("BTC/KRW", "market", "buy", Decimal("0.001"), params={"clientOrderId": "stable"})
        snapshot = exchange.snapshot()
        self.assertEqual((snapshot["version"], snapshot["quote_currency"]), (2, "KRW"))
        restored = PaperExchange.from_snapshot(json.loads(json.dumps(snapshot, sort_keys=True)))
        await restored.connect()
        self.assertEqual(restored.snapshot(), snapshot)
        self.assertEqual(await restored.create_order("BTC/KRW", "market", "buy", Decimal("0.001"),
                                                    params={"clientOrderId": "stable"}), first)
        self.assertEqual(restored.get_total_value(), exchange.get_total_value())

    async def test_legacy_usdt_snapshot_and_default_result_shape_remain_unchanged(self):
        exchange = PaperExchange(initial_balance=Decimal("1000"))
        snapshot = exchange.snapshot()
        self.assertEqual(snapshot["version"], 1)
        self.assertNotIn("quote_currency", snapshot)
        self.assertEqual(PaperExchange.from_snapshot(deepcopy(snapshot)).snapshot(), snapshot)
        result = await PaperRunner(strategy_factory=scripted({})).run(rows([(100, 100)]))
        self.assertNotIn("quote_currency", result)
        self.assertEqual(result["assumptions"]["symbol"], "BTC/USDT")

    async def test_cross_currency_prices_orders_and_initial_holdings_are_rejected(self):
        exchange = self.krw()
        await exchange.connect()
        before = exchange.snapshot()
        with self.assertRaises(ValueError):
            exchange.set_price("BTC/USDT", Decimal("100"))
        with self.assertRaises(ValueError):
            exchange.set_ohlcv("BTC/USDT", "1h", [[1, 100, 100, 100, 100, 1]])
        with self.assertRaises(ValueError):
            await exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"))
        self.assertEqual(exchange.snapshot(), before)
        with self.assertRaises(ValueError):
            self.krw(initial_positions={"BTC/USDT": Decimal("1")})

    async def test_legacy_usdt_price_metadata_is_retained_but_cross_quote_spending_fails(self):
        exchange = PaperExchange(initial_balance=Decimal("1000"), initial_positions={"BTC/USDT": Decimal("1")})
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100000"))
        exchange.set_price("ETH/BTC", Decimal("100"))
        before = exchange.snapshot()
        self.assertEqual(PaperExchange.from_snapshot(deepcopy(before)).snapshot(), before)
        with self.assertRaises(ValueError):
            await exchange.create_order("ETH/BTC", "market", "buy", Decimal("0.001"))
        self.assertEqual(exchange.snapshot(), before)

    async def test_currency_metadata_cannot_silently_relabel_a_snapshot(self):
        snapshot = self.krw().snapshot()
        for mutate in (lambda state: state.pop("quote_currency"),
                       lambda state: state.update(quote_currency="USDT"),
                       lambda state: state.update(version=1),
                       lambda state: state["balances"].update(USDT=state["balances"].pop("KRW"))):
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                state = deepcopy(snapshot)
                mutate(state)
                PaperExchange.from_snapshot(state)
        legacy = PaperExchange().snapshot()
        legacy["quote_currency"] = "KRW"
        with self.assertRaises(ValueError):
            PaperExchange.from_snapshot(legacy)

    async def test_unsupported_quote_currencies_fail_before_ledger_creation(self):
        for currency in ("USD", "krw", "", None):
            with self.subTest(currency=currency), self.assertRaises(ValueError):
                PaperExchange(quote_currency=currency)
        for symbol in ("BTC/USD", "BTC/EUR"):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                PaperRunConfig(symbol=symbol)

    async def test_one_million_krw_runner_restart_matches_uninterrupted_net_equity(self):
        config = PaperRunConfig(symbol="BTC/KRW", initial_balance=Decimal("1000000"))
        fixture = rows([(100000000, 100000000), (110000000, 120000000),
                        (130000000, 130000000), (125000000, 125000000)])
        def runner():
            return PaperRunner(config, strategy_factory=scripted({0: "buy", 2: "sell"}))
        expected = await runner().run(fixture)
        self.assertEqual(expected["quote_currency"], "KRW")
        self.assertEqual(expected["assumptions"]["initial_balance"], "1000000")
        self.assertEqual({trade["fee"]["currency"] for trade in expected["trades"]}, {"KRW"})
        for cursor in (1, 2, 3):
            with self.subTest(cursor=cursor), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "krw.json"
                await runner().run(fixture, checkpoint_path=path, stop_after=cursor)
                self.assertEqual(await runner().run(fixture, checkpoint_path=path), expected)
                before = path.read_bytes()
                self.assertEqual(await runner().run(fixture, checkpoint_path=path), expected)
                self.assertEqual(path.read_bytes(), before)

    async def test_krw_risk_halts_block_entry_and_preserve_exit(self):
        config = PaperRunConfig(symbol="BTC/KRW", initial_balance=Decimal("1000000"),
                                position_size_pct=Decimal("50"), fee_pct=Decimal("0"),
                                slippage_pct=Decimal("0"), max_daily_loss_pct=Decimal("3"),
                                max_drawdown_pct=Decimal("10"))
        fixture = rows([(100000000, 100000000), (100000000, 50000000),
                        (50000000, 50000000), (50000000, 50000000)])
        result = await PaperRunner(config, strategy_factory=scripted({0: "buy", 1: "sell", 2: "buy"})).run(fixture)
        self.assertEqual([trade["side"] for trade in result["trades"]], ["buy", "sell"])
        self.assertEqual(Decimal(result["equity"]), Decimal("750000"))
        self.assertTrue(result["daily_loss_halted"])
        self.assertTrue(result["drawdown_halted"])


if __name__ == "__main__":
    unittest.main()
