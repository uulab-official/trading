"""Net FIFO reporting regressions using the original broker and engine."""

from copy import deepcopy
from decimal import Decimal, localcontext
import unittest
from unittest.mock import patch
import json
from pathlib import Path
import importlib.util
import tempfile

from trading_backtest import BacktestConfig, BacktestEngine, VirtualBroker
from trading_backtest.brokers.virtual_broker import BrokerConfig
from trading_strategy import MACrossStrategy, SignalType, StrategyConfig
from test_engine_accounting import ScheduledStrategy, fixture


def broker(initial="1000", fee="1"):
    return VirtualBroker(BrokerConfig(slippage_pct=Decimal("0"),
                         slippage_volatility_factor=False,
                         maker_fee_pct=Decimal(fee), taker_fee_pct=Decimal(fee),
                         min_volume_threshold=Decimal("0")), Decimal(initial))


def fill(account, side, amount, price, symbol="BTC/USDT"):
    price = Decimal(str(price))
    return account.submit_order(symbol, side, Decimal(str(amount)), current_bar={
        "open": price, "high": price, "low": price, "close": price,
        "volume": 2000, "timestamp": len(account.trades) * 3600000})


class TestRealizedReporting(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Reporting tests forbid network"))
            guard.start()
            self.addCleanup(guard.stop)

    def report(self, account):
        self.assertTrue(hasattr(account, "get_trade_report"), "The broker needs a fee-inclusive closed-exit report")
        return account.get_trade_report()

    def test_real_engine_price_gain_after_fees_is_a_completed_loss(self):
        data = fixture()
        data.loc[:, ["open", "high", "low", "close"]] = 100
        data.loc[52:, ["open", "high", "low", "close"]] = 101
        engine = BacktestEngine(BacktestConfig(initial_balance=Decimal("1000"),
                                position_size_pct=Decimal("10"), commission_pct=Decimal("1"),
                                slippage_pct=Decimal("0")))
        result = engine.run(ScheduledStrategy({49: SignalType.BUY, 51: SignalType.SELL}), data)
        self.assertEqual(result.final_balance, Decimal("998.99"))
        self.assertEqual(result.win_count, 0)
        self.assertEqual(result.loss_count, 1)
        self.assertEqual(result.closed_trade_count, 1)
        self.assertEqual(result.total_trades, 2)  # compatibility: executed fill count
        self.assertEqual(result.realized_net_pnl, Decimal("-1.01"))
        self.assertEqual(result.win_rate, Decimal("0"))
        self.assertEqual(result.to_dict()["realized_net_pnl"], -1.01)

    def test_zero_price_gain_is_net_loss_with_entry_and_exit_fees(self):
        account = broker()
        fill(account, "buy", 1, 100)
        fill(account, "sell", 1, 100)
        report = self.report(account)
        self.assertEqual(report.gross_realized_pnl, Decimal("0"))
        self.assertEqual(report.realized_net_pnl, Decimal("-2"))
        self.assertEqual(report.loss_count, 1)
        self.assertEqual(report.closed_trade_count, 1)
        self.assertEqual(report.closed_exits[0].entry_fees, Decimal("1"))
        self.assertEqual(report.closed_exits[0].exit_fee, Decimal("1"))

    def test_partial_exit_matches_multiple_buys_fifo_and_retains_open_cost(self):
        account = broker()
        fill(account, "buy", 1, 100)
        fill(account, "buy", 2, 200)
        fill(account, "sell", "1.5", 180)
        report = self.report(account)
        exit = report.closed_exits[0]
        self.assertEqual(exit.entry_notional, Decimal("200"))
        self.assertEqual(exit.entry_fees, Decimal("2"))
        self.assertEqual(exit.exit_fee, Decimal("2.7"))
        self.assertEqual(exit.gross_pnl, Decimal("70"))
        self.assertEqual(exit.net_pnl, Decimal("65.3"))
        self.assertEqual([match.amount for match in exit.matches], [Decimal("1"), Decimal("0.5")])
        self.assertEqual(report.open_lots[0].amount, Decimal("1.5"))
        self.assertEqual(report.open_lots[0].entry_notional, Decimal("300"))
        self.assertEqual(report.open_lots[0].entry_fee, Decimal("3"))
        self.assertEqual(report.closed_trade_count, 1)
        self.assertEqual(report.win_rate, Decimal("100"))
        self.assertEqual(sum(exit.entry_fees + exit.exit_fee for exit in report.closed_exits)
                         + sum(lot.entry_fee for lot in report.open_lots), account.total_fees)
        self.assertEqual(account.get_total_value({"BTC/USDT": Decimal("180")}) - account.initial_balance,
                         report.realized_net_pnl + Decimal("270") - Decimal("303"))

    def test_remaining_exit_consumes_entry_fees_once_and_uses_currency_profit_factor(self):
        account = broker()
        fill(account, "buy", 1, 100)
        fill(account, "buy", 2, 200)
        fill(account, "sell", "1.5", 180)
        fill(account, "sell", "1.5", 190)
        report = self.report(account)
        self.assertEqual(report.realized_net_pnl, Decimal("44.45"))
        self.assertEqual(report.realized_net_pnl, account.balance - account.initial_balance)
        self.assertEqual(sum(e.entry_fees for e in report.closed_exits), Decimal("5"))
        self.assertEqual(sum(e.exit_fee for e in report.closed_exits), Decimal("5.55"))
        self.assertEqual(report.open_lots, ())
        self.assertEqual((report.win_count, report.loss_count, report.closed_trade_count), (1, 1, 2))
        self.assertEqual(report.win_rate, Decimal("50"))
        with localcontext() as ctx:
            ctx.prec = 28
            self.assertEqual(report.profit_factor, Decimal("65.3") / Decimal("20.85"))

    def test_unmatched_open_buys_are_not_realized_trades_or_wins(self):
        account = broker()
        fill(account, "buy", 1, 100)
        report = self.report(account)
        self.assertEqual(report.closed_trade_count, 0)
        self.assertEqual(report.realized_net_pnl, Decimal("0"))
        self.assertEqual(report.win_rate, Decimal("0"))
        self.assertIsNone(report.profit_factor)
        self.assertEqual(len(report.open_lots), 1)

    def test_genuine_break_even_exit_is_counted_separately(self):
        account = broker(fee="0")
        fill(account, "buy", 1, 100)
        fill(account, "sell", 1, 100)
        report = self.report(account)
        self.assertEqual((report.win_count, report.loss_count, report.break_even_count), (0, 0, 1))
        self.assertEqual(report.closed_trade_count, 1)
        self.assertIsNone(report.profit_factor)

    def test_profit_factor_without_losses_is_undefined(self):
        account = broker(fee="0")
        fill(account, "buy", 1, 100)
        fill(account, "sell", 1, 110)
        report = self.report(account)
        self.assertIsNone(report.profit_factor)
        self.assertEqual(report.win_rate, Decimal("100"))

    def test_reports_do_not_mutate_raw_fills_cash_holdings_or_fees(self):
        account = broker()
        fill(account, "buy", 3, 100)
        fill(account, "sell", 1, 110)
        state = deepcopy((account.trades, account.balance, account.positions, account.total_fees))
        first = self.report(account)
        second = self.report(account)
        self.assertEqual(second, first)
        self.assertEqual((account.trades, account.balance, account.positions, account.total_fees), state)

    def test_mutable_source_timestamp_metadata_cannot_change_fresh_or_raw_reports(self):
        account = broker(fee="0")
        timestamp = {"tag": "original"}
        bar = {"open": 100, "high": 100, "low": 100, "close": 100,
               "volume": 2000, "timestamp": timestamp}
        account.submit_order("BTC/USDT", "buy", Decimal("1"), current_bar=bar)
        account.submit_order("BTC/USDT", "sell", Decimal("1"), current_bar=bar)
        report = self.report(account)
        report.closed_exits[0].timestamp["tag"] = "report-mutated"
        self.assertEqual(account.trades[-1]["timestamp"]["tag"], "original")
        self.assertEqual(account.get_trade_report().closed_exits[0].timestamp["tag"], "original")
        timestamp["tag"] = "caller-mutated"
        self.assertEqual(account.get_trade_report().closed_exits[0].timestamp["tag"], "original")

    def test_rejected_orders_do_not_create_closed_reports(self):
        account = broker()
        fill(account, "buy", 100, 100)
        fill(account, "sell", 1, 100)
        report = self.report(account)
        self.assertEqual(report.closed_trade_count, 0)
        self.assertEqual(report.open_lots, ())
        self.assertEqual(account.trades, [])

    def test_fractional_splits_allocate_the_entire_entry_fee_without_residue(self):
        account = broker(fee="0.1")
        fill(account, "buy", 1, 100)
        third = Decimal("0.3333333333333333333333333333")
        fill(account, "sell", third, 100)
        fill(account, "sell", third, 100)
        fill(account, "sell", account.get_position("BTC/USDT"), 100)
        report = self.report(account)
        self.assertEqual(sum(e.entry_fees for e in report.closed_exits), Decimal("0.1"))
        self.assertEqual(report.open_lots, ())

    def test_full_exit_matches_broker_rounded_inventory_without_missing_lots(self):
        account = broker(fee="0")
        fill(account, "buy", "0.9876543210987654321098765432", 1)
        fill(account, "buy", "0.1357913579135791357913579136", 1)
        fill(account, "sell", account.get_position("BTC/USDT"), 1)
        report = self.report(account)
        self.assertEqual(account.positions, {})
        self.assertEqual(report.open_lots, ())
        self.assertEqual(report.closed_trade_count, 1)

    def test_full_exit_cannot_leave_a_fifo_rounding_phantom_lot_or_fee(self):
        account = broker(fee="0.1")
        fill(account, "buy", "0.6666666666666666666666666667", 1)
        fill(account, "buy", "0.7777777777777777777777777777", 1)
        entry_fees = account.total_fees
        fill(account, "sell", account.get_position("BTC/USDT"), 1)
        report = self.report(account)
        self.assertEqual(account.positions, {})
        self.assertEqual(report.open_lots, ())
        self.assertEqual(report.closed_exits[0].entry_fees, entry_fees)

    def test_small_genuine_open_inventory_is_not_erased_by_a_tolerance(self):
        account = broker(fee="0")
        fill(account, "buy", "1E-29", 100)
        report = self.report(account)
        self.assertEqual(report.open_lots[0].amount, Decimal("1E-29"))
        self.assertEqual(report.closed_trade_count, 0)

    def test_zero_credit_rounding_lot_keeps_its_cost_until_known_full_close(self):
        account = broker(fee="0.1")
        fill(account, "buy", "1E29", "1E-29")
        fill(account, "buy", 1, "1E-29")
        report = self.report(account)
        self.assertEqual(report.open_lots[-1].amount, Decimal("0"))
        self.assertEqual(report.open_lots[-1].entry_notional, Decimal("1E-29"))
        self.assertEqual(report.open_lots[-1].entry_fee, Decimal("1E-32"))
        self.assertTrue(report.inventory_rounding)
        fill(account, "sell", account.get_position("BTC/USDT"), "1E-29")
        closed = self.report(account)
        self.assertEqual(closed.open_lots, ())
        self.assertEqual(len(closed.closed_exits[0].matches), 2)

    def test_reporting_is_stable_across_decimal_context_and_broker_reset(self):
        account = broker()
        fill(account, "buy", 1, 100)
        fill(account, "sell", 1, 101)
        expected = self.report(account)
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(self.report(account), expected)
        account.reset()
        empty = self.report(account)
        self.assertEqual(empty.closed_trade_count, 0)
        self.assertEqual(empty.realized_net_pnl, Decimal("0"))

    def test_symbols_keep_distinct_fifo_lots_with_a_shared_quote_unit(self):
        account = broker(fee="0")
        fill(account, "buy", 1, 100, "BTC/USDT")
        fill(account, "buy", 1, 200, "ETH/USDT")
        fill(account, "sell", 1, 150, "BTC/USDT")
        fill(account, "sell", 1, 180, "ETH/USDT")
        report = self.report(account)
        self.assertEqual([exit.net_pnl for exit in report.closed_exits], [Decimal("50"), Decimal("-20")])
        self.assertEqual(report.realized_net_pnl, Decimal("30"))
        self.assertEqual(report.quote_currency, "USDT")

    def test_different_quote_currencies_cannot_be_added_without_conversion(self):
        account = broker(fee="0")
        fill(account, "buy", 1, 100, "BTC/USDT")
        fill(account, "buy", 1, 100, "ETH/EUR")
        with self.assertRaisesRegex(ValueError, "single quote currency"):
            account.get_trade_report()

    def test_exact_report_preserves_decimal_amounts_and_json_undefined_ratio(self):
        account = broker(fee="0.1")
        amount = Decimal("0.123456789123456789")
        fill(account, "buy", amount, "100.123456789123456789")
        fill(account, "sell", amount, "110.123456789123456789")
        report = self.report(account)
        payload = json.loads(json.dumps(report.to_dict(), allow_nan=False))
        self.assertEqual(payload["closed_exits"][0]["amount"], str(amount))
        self.assertEqual(payload["closed_exits"][0]["net_pnl"], str(report.realized_net_pnl))
        self.assertIsNone(payload["profit_factor"])
        self.assertEqual(payload["pnl_unit"], "USDT")
        self.assertEqual(payload["return_unit"], "percent_of_fee_inclusive_entry_cost")

    def test_repeated_real_engine_runs_preserve_net_statistics(self):
        data = fixture()
        data.loc[:, ["open", "high", "low", "close"]] = 100
        data.loc[52:, ["open", "high", "low", "close"]] = 101
        runner = BacktestEngine(BacktestConfig(initial_balance=Decimal("1000"),
                                position_size_pct=Decimal("10"), commission_pct=Decimal("1"),
                                slippage_pct=Decimal("0")))
        strategy = ScheduledStrategy({49: SignalType.BUY, 51: SignalType.SELL})
        first = runner.run(strategy, data)
        second = runner.run(strategy, data)
        for field in ("realized_net_pnl", "gross_realized_pnl", "win_rate", "closed_trade_count",
                      "profit_factor", "avg_trade", "expectancy", "final_equity"):
            self.assertEqual(getattr(second, field), getattr(first, field), field)

    def test_cli_can_show_undefined_profit_factor_from_real_report(self):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        from trading_backtest.cli import run_backtest
        args = Namespace(symbol="BTC/USDT", strategy="ma_cross", timeframe="1h", data_dir="unused",
                         fast_period=3, slow_period=5, balance=1000, commission=0, slippage=0,
                         position_size=10, output=None, plot=False)
        output = StringIO()
        with patch("trading_backtest.cli.HistoricalDataLoader.load_from_csv", return_value=fixture()), \
             patch("trading_backtest.cli.HistoricalDataLoader.fetch_from_exchange", side_effect=AssertionError("No exchange data")), \
             redirect_stdout(output):
            run_backtest(args)
        self.assertIn("N/A (no realized losses)", output.getvalue())
        self.assertIn("Closed Exits:", output.getvalue())

    def test_optimizer_does_not_convert_an_undefined_ratio_into_failed_return(self):
        from trading_backtest.optimization.grid_search import GridSearchOptimizer
        config = BacktestConfig(initial_balance=Decimal("1000"), commission_pct=Decimal("0"),
                                slippage_pct=Decimal("0"), position_size_pct=Decimal("10"))
        params = {"fast_period": 3, "slow_period": 5}
        optimizer = GridSearchOptimizer(MACrossStrategy, {name: [value] for name, value in params.items()}, config)
        row = optimizer._evaluate_params(params, fixture(), "total_return_pct")
        expected = BacktestEngine(config).run(MACrossStrategy(StrategyConfig("expected", "TEST", "1h"), **params), fixture())
        self.assertEqual(row["total_return_pct"], float(expected.total_return_pct))
        self.assertIsNone(row["profit_factor"])

    def test_optimizer_rejects_an_entirely_undefined_ranking_metric(self):
        from trading_backtest.optimization.grid_search import GridSearchOptimizer
        optimizer = GridSearchOptimizer(MACrossStrategy, {"fast_period": [3], "slow_period": [5]})
        with self.assertRaisesRegex(ValueError, "defined"):
            optimizer.optimize(fixture(), metric="profit_factor", n_jobs=1)

    def standalone_report(self):
        path = Path(__file__).resolve().parents[3] / "tools/offline_trade_report.py"
        self.assertTrue(path.is_file(), "A runnable local-fixture report command is required")
        spec = importlib.util.spec_from_file_location("offline_trade_report_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_standalone_report_runs_existing_engine_and_strict_json_offline(self):
        module = self.standalone_report()
        result = module.build_fixture_report(module.DEFAULT_FIXTURE)
        payload = json.loads(json.dumps(result, allow_nan=False))
        self.assertEqual(payload["mode"], "paper")
        self.assertGreater(payload["trade_report"]["fill_count"], 1)
        self.assertGreater(payload["trade_report"]["closed_trade_count"], 0)
        self.assertEqual(payload["trade_report"]["pnl_unit"], "USDT")
        self.assertIn("unverified", payload["risk_statistics"])

    def test_standalone_report_rejects_a_live_mode_fixture_before_execution(self):
        module = self.standalone_report()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "live.json"
            path.write_text(json.dumps({"mode": "live", "candles": []}))
            with self.assertRaisesRegex(ValueError, "paper"):
                module.build_fixture_report(path)


if __name__ == "__main__":
    unittest.main()
