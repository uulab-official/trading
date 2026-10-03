"""Deterministic offline accounting and checkpoint regressions."""

from decimal import Decimal, localcontext, ROUND_DOWN
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trading_core.paper_exchange import PaperExchange


class PaperLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fees_slippage_and_marked_holdings(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            exchange = PaperExchange(initial_balance=Decimal("1000"), fee_pct=Decimal("1"), slippage_pct=Decimal("1"))
            await exchange.connect()
            exchange.set_price("BTC/USDT", Decimal("100"), timestamp=1)
            buy = await exchange.create_order("BTC/USDT", "market", "buy", Decimal("2"))
            self.assertEqual(buy["average"], Decimal("101"))
            self.assertEqual(buy["fee"]["cost"], Decimal("2.02"))
            self.assertEqual((await exchange.fetch_balance())["USDT"].free, Decimal("795.98"))
            self.assertEqual(exchange.get_total_value(), Decimal("995.98"))
            exchange.set_price("BTC/USDT", Decimal("110"), timestamp=2)
            await exchange.create_order("BTC/USDT", "market", "sell", Decimal("2"))
            self.assertEqual(exchange.get_total_value(), Decimal("1011.602"))
            self.assertEqual(exchange.total_fees, Decimal("4.198"))

    async def test_rejected_order_has_no_fee_or_fill_side_effects(self):
        exchange = PaperExchange(initial_balance=Decimal("1000"), fee_pct=Decimal("1"), slippage_pct=Decimal("1"))
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100"))
        before = exchange.snapshot()
        with self.assertRaises(ValueError):
            await exchange.create_order("BTC/USDT", "market", "buy", Decimal("10"), params={"clientOrderId": "unfunded"})
        self.assertEqual(exchange.snapshot(), before)

    async def test_snapshot_restore_is_exact_and_preserves_deduplication(self):
        exchange = PaperExchange(initial_balance=Decimal("1000"), fee_pct=Decimal("0.1"), slippage_pct=Decimal("0.05"))
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100"), timestamp=1)
        first = await exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "stable-1"})
        snapshot = exchange.snapshot()
        restored = PaperExchange.from_snapshot(snapshot)
        await restored.connect()
        second = await restored.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "stable-1"})
        self.assertEqual(first, second)
        self.assertEqual(restored.snapshot(), snapshot)
        self.assertEqual(restored.get_total_value(), exchange.get_total_value())

    async def test_sorted_json_checkpoint_preserves_trade_chronology(self):
        exchange = PaperExchange(initial_balance=Decimal("1000"))
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100"), timestamp=1)
        await exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "fixture-9-buy"})
        exchange.set_price("BTC/USDT", Decimal("110"), timestamp=2)
        await exchange.create_order("BTC/USDT", "market", "sell", Decimal("1"), params={"clientOrderId": "fixture-10-sell"})
        restored = PaperExchange.from_snapshot(json.loads(json.dumps(exchange.snapshot(), sort_keys=True)))
        self.assertEqual(restored.snapshot(), exchange.snapshot())
        self.assertEqual([trade["side"] for trade in restored.snapshot()["trades"]], ["buy", "sell"])

    async def test_ledger_calculations_ignore_ambient_decimal_precision(self):
        async def sample():
            exchange = PaperExchange(initial_balance=Decimal("1000"), fee_pct=Decimal("0.1"), slippage_pct=Decimal("0.05"))
            await exchange.connect()
            exchange.set_price("BTC/USDT", Decimal("103"), timestamp=1)
            await exchange.create_order("BTC/USDT", "market", "buy", Decimal("4.84709586"))
            exchange.set_price("BTC/USDT", Decimal("110"), timestamp=2)
            return exchange.snapshot(), exchange.get_total_value()
        expected = await sample()
        with localcontext() as context:
            context.prec = 12
            context.rounding = ROUND_DOWN
            actual = await sample()
        self.assertEqual(actual, expected)

    async def test_inconsistent_request_trade_or_fee_snapshot_is_rejected(self):
        exchange = PaperExchange(fee_pct=Decimal("1"))
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100"), timestamp=1)
        await exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"), params={"clientOrderId": "stable"})
        def request(state):
            state["requests"]["stable"][2] = "sell"
        def trade(state):
            state["trades"][0]["amount"] = "2"
        def fee(state):
            state["orders"]["stable"]["fee"]["currency"] = "BTC"
        for mutate in [request, trade, fee]:
            state = exchange.snapshot()
            mutate(state)
            with self.subTest(mutation=mutate.__name__), self.assertRaises(ValueError):
                PaperExchange.from_snapshot(state)

    async def test_snapshot_and_order_responses_cannot_mutate_internal_state(self):
        exchange = PaperExchange(fee_pct=Decimal("1"))
        await exchange.connect()
        exchange.set_price("BTC/USDT", Decimal("100"))
        order = await exchange.create_order("BTC/USDT", "market", "buy", Decimal("1"))
        order["fee"]["cost"] = Decimal("999")
        snapshot = exchange.snapshot()
        snapshot["balances"]["USDT"] = "0"
        self.assertEqual((await exchange.fetch_balance())["USDT"].free, Decimal("9899"))
        self.assertEqual((await exchange.fetch_my_trades())[0]["fee"]["cost"], Decimal("1"))

    async def test_invalid_friction_and_live_or_negative_snapshot_fail_closed(self):
        for kwargs in [{"fee_pct": Decimal("-1")}, {"slippage_pct": Decimal("NaN")}, {"slippage_pct": Decimal("100")}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PaperExchange(**kwargs)
        for mode in ["live", "invalid"]:
            data = PaperExchange().snapshot()
            data["mode"] = mode
            with self.assertRaises(ValueError):
                PaperExchange.from_snapshot(data)
        data = PaperExchange().snapshot()
        data["balances"]["USDT"] = "-1"
        with self.assertRaises(ValueError):
            PaperExchange.from_snapshot(data)


if __name__ == "__main__":
    unittest.main()
