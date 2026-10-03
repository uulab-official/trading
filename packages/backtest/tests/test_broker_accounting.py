"""Offline regressions for atomic spot fills and marked broker equity."""

from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path
import sys
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
for package in ("backtest", "strategy", "data", "core"):
    sys.path.insert(0, str(PACKAGE_ROOT / package))

from trading_backtest.brokers.virtual_broker import (
    BrokerConfig,
    OrderStatus,
    VirtualBroker,
)


def candle(price="100", **changes):
    bar = {
        "open": Decimal(price),
        "high": Decimal(price),
        "low": Decimal(price),
        "close": Decimal(price),
        "volume": Decimal("1000"),
        "timestamp": 1,
    }
    bar.update(changes)
    return bar


class BrokerAccountingTests(unittest.TestCase):
    def broker(self, balance="1000", fee="1", slippage="0"):
        return VirtualBroker(
            config=BrokerConfig(
                slippage_pct=Decimal(slippage),
                slippage_volatility_factor=False,
                maker_fee_pct=Decimal(fee),
                taker_fee_pct=Decimal(fee),
            ),
            initial_balance=Decimal(balance),
        )

    def accounting(self, broker):
        return (
            broker.balance,
            dict(broker.positions),
            broker.total_fees,
            deepcopy(broker.trades),
        )

    def assert_unfilled(self, order):
        self.assertEqual(order.filled_amount, Decimal("0"))
        self.assertIsNone(order.filled_price)
        self.assertIsNone(order.filled_at)
        self.assertEqual(order.slippage_pct, Decimal("0"))

    def test_insufficient_buy_is_atomic_including_fee_and_fill_metadata(self):
        broker = self.broker(slippage="1")
        before = self.accounting(broker)
        order = broker.submit_order("BTC/USDT", "buy", Decimal("10"), current_bar=candle())
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(self.accounting(broker), before)
        self.assert_unfilled(order)

    def test_sell_without_holdings_cannot_create_cash(self):
        broker = self.broker()
        before = self.accounting(broker)
        order = broker.submit_order("BTC/USDT", "sell", Decimal("1"), current_bar=candle())
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(self.accounting(broker), before)
        self.assert_unfilled(order)

    def test_oversell_preserves_existing_holdings(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("2"), current_bar=candle())
        before = self.accounting(broker)
        order = broker.submit_order("BTC/USDT", "sell", Decimal("3"), current_bar=candle("110"))
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(self.accounting(broker), before)
        self.assert_unfilled(order)

    def test_funded_buy_and_sell_charge_fees_once(self):
        broker = self.broker(slippage="1")
        buy = broker.submit_order("BTC/USDT", "buy", Decimal("2"), current_bar=candle())
        self.assertEqual(buy.filled_price, Decimal("101"))
        self.assertEqual(broker.balance, Decimal("795.98"))
        self.assertEqual(broker.total_fees, Decimal("2.02"))
        sell = broker.submit_order("BTC/USDT", "sell", Decimal("2"), current_bar=candle("110"))
        self.assertEqual(sell.filled_price, Decimal("108.9"))
        self.assertEqual(broker.balance, Decimal("1011.602"))
        self.assertEqual(broker.total_fees, Decimal("4.198"))
        self.assertEqual(broker.positions, {})
        self.assertEqual(len(broker.trades), 2)

    def test_a_filled_order_cannot_fill_twice(self):
        broker = self.broker()
        order = broker.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=candle())
        before = self.accounting(broker)
        filled_at = order.filled_at
        self.assertFalse(broker._try_fill_order(order, candle("200")))
        self.assertEqual(self.accounting(broker), before)
        self.assertEqual(order.filled_at, filled_at)
        self.assertEqual(order.filled_price, Decimal("100"))

    def test_rejected_queued_order_leaves_queue_and_cannot_retry(self):
        broker = self.broker()
        order = broker.submit_order("BTC/USDT", "buy", Decimal("10"))
        broker.process_bar(candle())
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertNotIn(order.order_id, broker.pending_orders)
        before = self.accounting(broker)
        broker.process_bar(candle("1"))
        self.assertFalse(broker._try_fill_order(order, candle("1")))
        self.assertEqual(self.accounting(broker), before)
        self.assert_unfilled(order)

    def test_filled_queued_order_leaves_queue_and_future_bars_do_not_refill(self):
        broker = self.broker()
        order = broker.submit_order("BTC/USDT", "buy", Decimal("1"))
        broker.process_bar(candle())
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertNotIn(order.order_id, broker.pending_orders)
        before = self.accounting(broker)
        broker.process_bar(candle("200"))
        self.assertEqual(self.accounting(broker), before)

    def test_immediate_unreached_limit_stays_pending_until_reached(self):
        broker = self.broker()
        before = self.accounting(broker)
        order = broker.submit_order(
            "BTC/USDT", "buy", Decimal("1"), order_type="limit",
            price=Decimal("90"), current_bar=candle(),
        )
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertIn(order.order_id, broker.pending_orders)
        self.assertEqual(self.accounting(broker), before)
        self.assert_unfilled(order)
        broker.process_bar(candle(low=Decimal("89")))
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_price, Decimal("90"))
        self.assertEqual(broker.balance, Decimal("909.1"))
        self.assertNotIn(order.order_id, broker.pending_orders)

    def test_immediate_low_volume_market_order_stays_pending(self):
        broker = self.broker()
        order = broker.submit_order(
            "BTC/USDT", "buy", Decimal("1"), current_bar=candle(volume=Decimal("999")),
        )
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertIn(order.order_id, broker.pending_orders)
        self.assert_unfilled(order)
        broker.process_bar(candle())
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_invalid_order_fields_are_terminal_without_accounting_changes(self):
        invalid = [
            {"side": "hold"}, {"side": "BUY"}, {"symbol": ""},
            {"order_type": "unknown"}, {"order_type": "stop", "price": Decimal("100")},
            {"amount": Decimal("0")}, {"amount": Decimal("-1")},
            {"amount": Decimal("NaN")}, {"amount": Decimal("Infinity")},
            {"amount": "invalid"},
            {"order_type": "limit"},
            {"order_type": "limit", "price": Decimal("0")},
            {"order_type": "limit", "price": Decimal("-1")},
            {"order_type": "limit", "price": Decimal("NaN")},
            {"order_type": "limit", "price": Decimal("Infinity")},
        ]
        for fields in invalid:
            for immediate in (False, True):
                with self.subTest(fields=fields, immediate=immediate):
                    broker = self.broker()
                    request = {"symbol": "BTC/USDT", "side": "buy", "amount": Decimal("1")}
                    request.update(fields)
                    if immediate:
                        request["current_bar"] = candle()
                    before = self.accounting(broker)
                    order = broker.submit_order(**request)
                    self.assertEqual(order.status, OrderStatus.REJECTED)
                    self.assertEqual(self.accounting(broker), before)
                    self.assertNotIn(order.order_id, broker.pending_orders)
                    self.assert_unfilled(order)

    def test_invalid_execution_price_cannot_create_free_holdings(self):
        for price in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), "invalid"):
            with self.subTest(price=price):
                broker = self.broker()
                before = self.accounting(broker)
                order = broker.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=candle(open=price))
                self.assertEqual(order.status, OrderStatus.REJECTED)
                self.assertEqual(self.accounting(broker), before)
                self.assert_unfilled(order)

    def test_invalid_volume_or_supplied_ohlc_is_rejected_atomically(self):
        for fields in (
            {"volume": Decimal("NaN")}, {"volume": Decimal("-1")},
            {"volume": Decimal("Infinity")}, {"high": Decimal("0")},
            {"close": Decimal("NaN")}, {"high": Decimal("90"), "low": Decimal("110")},
        ):
            with self.subTest(fields=fields):
                broker = self.broker()
                before = self.accounting(broker)
                order = broker.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=candle(**fields))
                self.assertEqual(order.status, OrderStatus.REJECTED)
                self.assertEqual(self.accounting(broker), before)
                self.assert_unfilled(order)

    def test_open_only_market_bar_is_supported_with_fixed_slippage(self):
        broker = self.broker(slippage="1")
        order = broker.submit_order("BTC/USDT", "buy", Decimal("1"))
        broker.process_bar({"open": Decimal("100"), "volume": Decimal("1000"), "timestamp": 2})
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_price, Decimal("101"))
        self.assertEqual(broker.balance, Decimal("897.99"))

    def test_invalid_friction_and_balance_configuration_is_rejected(self):
        for field in ("slippage_pct", "maker_fee_pct", "taker_fee_pct"):
            for number in ("-1", "100", "NaN", "Infinity"):
                with self.subTest(field=field, number=number), self.assertRaises(ValueError):
                    VirtualBroker(config=BrokerConfig(**{field: Decimal(number)}))
        for number in ("-1", "NaN", "Infinity"):
            with self.subTest(initial_balance=number), self.assertRaises(ValueError):
                VirtualBroker(initial_balance=Decimal(number))
        for number in ("-1", "NaN", "Infinity"):
            with self.subTest(min_volume_threshold=number), self.assertRaises(ValueError):
                VirtualBroker(config=BrokerConfig(min_volume_threshold=Decimal(number)))

    def test_summary_reports_recorded_marked_equity_separately_from_cash(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("2"), current_bar=candle())
        self.assertEqual(broker.get_total_value({"BTC/USDT": Decimal("110")}), Decimal("1018"))
        summary = broker.get_performance_summary()
        self.assertIn("final_equity", summary)
        self.assertEqual(summary["final_equity"], 1018.0)
        self.assertEqual(summary["final_balance"], 798.0)
        self.assertEqual(summary["position_value"], 220.0)
        self.assertEqual(summary["total_return_pct"], 1.8)

    def test_summary_accepts_explicit_marks_and_records_them(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("2"), current_bar=candle())
        summary = broker.get_performance_summary({"BTC/USDT": Decimal("120")})
        self.assertEqual(summary["final_equity"], 1038.0)
        self.assertEqual(summary["total_return_pct"], 3.8)
        self.assertEqual(broker.get_performance_summary(), summary)

    def test_successful_fill_records_a_caller_supplied_close_mark(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("2"), current_bar=candle(close=Decimal("110")))
        summary = broker.get_performance_summary()
        self.assertIn("final_equity", summary)
        self.assertEqual(summary["final_equity"], 1018.0)

    def test_a_missing_mark_cannot_silently_value_holdings_at_zero(self):
        broker = self.broker()
        broker.positions["BTC/USDT"] = Decimal("1")
        with self.assertRaises(ValueError):
            broker.get_total_value({})
        with self.assertRaises(ValueError):
            broker.get_performance_summary()

    def test_invalid_marks_do_not_replace_a_recorded_good_mark(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=candle())
        broker.get_total_value({"BTC/USDT": Decimal("110")})
        for price in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                broker.get_total_value({"BTC/USDT": price})
        self.assertEqual(broker.get_performance_summary()["final_equity"], 1009.0)

    def test_zero_initial_cash_has_no_defined_percentage_return(self):
        summary = self.broker(balance="0").get_performance_summary()
        self.assertEqual(summary["final_balance"], 0.0)
        self.assertEqual(summary["final_equity"], 0.0)
        self.assertIsNone(summary["total_return_pct"])

    def test_reset_discards_recorded_marks_and_fill_accounting(self):
        broker = self.broker()
        broker.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=candle())
        broker.get_total_value({"BTC/USDT": Decimal("110")})
        broker.reset()
        self.assertEqual(self.accounting(broker), (Decimal("1000"), {}, Decimal("0"), []))
        self.assertEqual(broker.orders, [])
        self.assertEqual(broker.pending_orders, {})
        broker.positions["BTC/USDT"] = Decimal("1")
        with self.assertRaises(ValueError):
            broker.get_performance_summary()

    def test_accounting_ignores_ambient_decimal_precision_and_rounding(self):
        def sample():
            broker = self.broker(fee="0.1", slippage="0.05")
            order = broker.submit_order("BTC/USDT", "buy", Decimal("4.84709586"), current_bar=candle("103"))
            equity = broker.get_total_value({"BTC/USDT": Decimal("110")})
            return broker.balance, dict(broker.positions), broker.total_fees, order.filled_price, equity, broker.get_performance_summary()

        expected = sample()
        with localcontext() as context:
            context.prec = 12
            context.rounding = ROUND_DOWN
            actual = sample()
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
