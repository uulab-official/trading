"""End-to-end local fixtures: causal signals, accounting, and restart."""

import json
import io
from decimal import Decimal, localcontext, ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for package in ["core", "data", "strategy"]:
    sys.path.insert(0, str(ROOT / "packages" / package))
sys.path.insert(0, str(ROOT / "apps/server"))
from trading_bot.paper_runner import PaperRunner, PaperRunConfig, main

START = (1700000000000 // 86400000) * 86400000


def rows(prices):
    return [[START + index * 3600000, opening, max(opening, close), min(opening, close), close, 1000]
            for index, (opening, close) in enumerate(prices)]


def scripted(actions):
    class Scripted:
        def on_candle(self, candle):
            index = (candle.timestamp - START) // 3600000
            action = actions.get(index)
            return SimpleNamespace(type=SimpleNamespace(value=action)) if action else None
    return Scripted


class PaperRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.guard.start()

    async def asyncTearDown(self):
        self.guard.stop()

    async def test_signal_fills_next_open_and_return_includes_open_holdings(self):
        runner = PaperRunner(PaperRunConfig(initial_balance=Decimal("1000"), position_size_pct=Decimal("50"), fee_pct=Decimal("0"), slippage_pct=Decimal("0")), strategy_factory=scripted({0: "buy"}))
        result = await runner.run(rows([(100, 200), (250, 300)]))
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["trades"][0]["timestamp"], START + 3600000)
        self.assertEqual(result["trades"][0]["average"], "250")
        self.assertEqual(result["cash"], "500.00000000")
        self.assertEqual(Decimal(result["equity"]), Decimal("1100"))
        self.assertEqual(Decimal(result["total_return_pct"]), Decimal("10"))

    async def test_final_bar_signal_remains_pending_until_a_future_bar(self):
        runner = PaperRunner(strategy_factory=scripted({0: "buy"}))
        result = await runner.run(rows([(100, 200)]))
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["pending_signal"]["action"], "buy")

    async def test_restart_before_and_after_fill_matches_uninterrupted_run(self):
        fixture = rows([(100, 100), (110, 120), (130, 130), (125, 125)])
        config = PaperRunConfig(initial_balance=Decimal("1000"), position_size_pct=Decimal("50"))
        expected = await PaperRunner(config, strategy_factory=scripted({0: "buy", 2: "sell"})).run(fixture)
        for stop_after in [1, 2, 3]:
            with self.subTest(stop_after=stop_after), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "paper-checkpoint.json"
                await PaperRunner(config, strategy_factory=scripted({0: "buy", 2: "sell"})).run(fixture, checkpoint_path=path, stop_after=stop_after)
                actual = await PaperRunner(config, strategy_factory=scripted({0: "buy", 2: "sell"})).run(fixture, checkpoint_path=path)
                self.assertEqual(actual, expected)
                again = await PaperRunner(config, strategy_factory=scripted({0: "buy", 2: "sell"})).run(fixture, checkpoint_path=path)
                self.assertEqual(again, expected)

    async def test_changed_past_data_or_configuration_cannot_resume(self):
        fixture = rows([(100, 100), (110, 110)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            await PaperRunner(strategy_factory=scripted({0: "buy"})).run(fixture, checkpoint_path=path, stop_after=1)
            before = path.read_bytes()
            altered = rows([(99, 99), (110, 110)])
            with self.assertRaises(ValueError):
                await PaperRunner(strategy_factory=scripted({0: "buy"})).run(altered, checkpoint_path=path)
            with self.assertRaises(ValueError):
                await PaperRunner(PaperRunConfig(fee_pct=Decimal("1")), strategy_factory=scripted({0: "buy"})).run(fixture, checkpoint_path=path)
            self.assertEqual(path.read_bytes(), before)

    async def test_appended_future_fixture_can_continue_saved_pending_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            await PaperRunner(strategy_factory=scripted({0: "buy"})).run(rows([(100, 100)]), checkpoint_path=path)
            result = await PaperRunner(strategy_factory=scripted({0: "buy"})).run(rows([(100, 100), (110, 110)]), checkpoint_path=path)
            self.assertEqual(len(result["trades"]), 1)
            self.assertEqual(result["bars_processed"], 2)

    async def test_daily_loss_guard_blocks_new_buys_but_allows_exit(self):
        config = PaperRunConfig(initial_balance=Decimal("1000"), position_size_pct=Decimal("50"), max_daily_loss_pct=Decimal("3"), max_drawdown_pct=Decimal("99"), fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
        fixture = rows([(100, 100), (100, 50), (50, 50), (50, 50), (50, 50)])
        result = await PaperRunner(config, strategy_factory=scripted({0: "buy", 1: "sell", 2: "buy"})).run(fixture)
        self.assertEqual([trade["side"] for trade in result["trades"]], ["buy", "sell"])
        self.assertTrue(result["daily_loss_halted"])

    async def test_real_existing_ma_strategy_runs_fully_offline(self):
        prices = [100 - i for i in range(55)] + [50 + i * 5 for i in range(20)] + [145 - i * 8 for i in range(15)]
        fixture = rows([(max(1, price), max(1, price)) for price in prices])
        runner = PaperRunner(PaperRunConfig(fast_period=3, slow_period=5))
        first = await runner.run(fixture)
        second = await PaperRunner(PaperRunConfig(fast_period=3, slow_period=5)).run(fixture)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first["trades"]), 2)
        self.assertEqual(first["trades"][0]["side"], "buy")
        self.assertEqual(first["trades"][1]["side"], "sell")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "real-ma.json"
            await PaperRunner(PaperRunConfig(fast_period=3, slow_period=5)).run(fixture, checkpoint_path=path, stop_after=58)
            resumed = await PaperRunner(PaperRunConfig(fast_period=3, slow_period=5)).run(fixture, checkpoint_path=path)
            self.assertEqual(resumed, first)

    def test_cli_entrypoint_is_offline_and_resume_stable(self):
        prices = [100 - i for i in range(55)] + [50 + i * 5 for i in range(20)] + [145 - i * 8 for i in range(15)]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.json"
            checkpoint = Path(directory) / "paper.json"
            fixture.write_text(json.dumps({"mode": "paper", "symbol": "BTC/USDT", "timeframe": "1h", "candles": rows([(max(1, p), max(1, p)) for p in prices])}))
            with patch("sys.stdout", new_callable=io.StringIO):
                first = main([str(fixture), "--checkpoint", str(checkpoint)])
                resumed = main([str(fixture), "--checkpoint", str(checkpoint)])
            self.assertEqual(first, resumed)
            self.assertEqual(first["bars_processed"], 90)
            self.assertGreaterEqual(len(first["trades"]), 2)

    async def test_live_mode_and_bad_or_out_of_order_fixtures_fail_closed(self):
        with self.assertRaises(ValueError):
            PaperRunConfig(mode="live")
        for fixture in [rows([(100, 100)])[::-1] + rows([(100, 100)]), [[START, 100, 50, 90, 100, 1]], [[START, 100, 100, 100, "NaN", 1]]]:
            with self.subTest(fixture=fixture), self.assertRaises(ValueError):
                await PaperRunner().run(fixture)

    async def test_overlapping_closed_bars_cannot_trade_on_future_information(self):
        fixture = rows([(100, 200), (250, 300)])
        fixture[1][0] = fixture[0][0] + 1
        with self.assertRaises(ValueError):
            await PaperRunner(strategy_factory=scripted({0: "buy"})).run(fixture)

    async def test_forged_checkpoint_cash_or_pending_signal_is_rejected(self):
        fixture = rows([(100, 100), (110, 110)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            await PaperRunner(PaperRunConfig(initial_balance=Decimal("1000")), strategy_factory=scripted({})).run(fixture, checkpoint_path=path, stop_after=1)
            saved = json.loads(path.read_text())
            saved["exchange"]["balances"]["USDT"] = "2000"
            saved["pending_signal"] = {"action": "buy", "source_index": 0}
            path.write_text(json.dumps(saved))
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                await PaperRunner(PaperRunConfig(initial_balance=Decimal("1000")), strategy_factory=scripted({})).run(fixture, checkpoint_path=path)
            self.assertEqual(path.read_bytes(), before)

    async def test_rsi_period_fifty_waits_for_full_wilder_seed(self):
        result = await PaperRunner(PaperRunConfig(strategy="rsi", rsi_period=50)).run(rows([(100, 100)] * 60))
        self.assertEqual(result["bars_processed"], 60)
        self.assertEqual(result["trades"], [])

    async def test_decimal_context_does_not_change_replay_or_checkpoint(self):
        fixture = rows([(100, 100), (103, 107), (111, 110)])
        config = PaperRunConfig(initial_balance=Decimal("1000"), position_size_pct=Decimal("50"))
        expected = await PaperRunner(config, strategy_factory=scripted({0: "buy"})).run(fixture)
        with localcontext() as context:
            context.prec = 12
            context.rounding = ROUND_DOWN
            actual = await PaperRunner(config, strategy_factory=scripted({0: "buy"})).run(fixture)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
