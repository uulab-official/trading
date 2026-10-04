"""Actual legacy paper lifecycle, crash ambiguity, and explicit recovery exits."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal
import importlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from trading_core.state_store import JSONStateStore
from trading_core.order_manager import OrderSide, OrderType
from trading_strategy import Signal, SignalType


class Clock(datetime):
    current = datetime(2026, 10, 4, 12)

    @classmethod
    def utcnow(cls):
        return cls.current


class LegacyPaperLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="legacy-lifecycle-")
        self.old_cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.stack = ExitStack()
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Lifecycle tests forbid network/DNS")))
        with patch("logging.FileHandler", return_value=logging.NullHandler()), patch("logging.basicConfig"):
            self.Bot = importlib.import_module("trading_bot.main").TradingBot
        Clock.current = datetime(2026, 10, 4, 12)
        for target in ("trading_core.portfolio.datetime", "trading_core.risk_manager.datetime",
                       "trading_core.order_manager.datetime", "trading_core.state_store.datetime",
                       "trading_core.legacy_checkpoint.datetime", "trading_bot.main.datetime"):
            self.stack.enter_context(patch(target, Clock))
        self.stack.enter_context(patch("trading_core.config_manager._config_manager", None))
        Path("config/active").mkdir(parents=True)
        Path("config/active/bot_config.yml").write_text(
            "bot: {id: audit, mode: paper}\nexchange: {id: binance, sandbox: true, testnet: true}\n"
            "strategies: [{name: fixture, enabled: true, type: ma_cross, symbol: BTC/USDT, timeframe: 1h}]\n")
        self.bots = []
        self.bot = await self.new_bot()

    async def asyncTearDown(self):
        for bot in reversed(self.bots):
            if bot._paper_checkpoint and bot._paper_checkpoint._descriptor is not None:
                try:
                    await bot.stop()
                except Exception:
                    bot._paper_checkpoint.release()
        self.stack.close()
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    async def new_bot(self, store=None):
        bot = self.Bot("config")
        self.bots.append(bot)
        bot.state_store = store
        await bot.initialize()
        return bot

    async def buy(self, bot=None, symbol="BTC/USDT"):
        bot = bot or self.bot
        bot.exchange.set_price(symbol, Decimal("100"))
        await bot._process_signal(Signal(SignalType.BUY, symbol, Decimal("100"), Clock.utcnow()))

    def raw(self):
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            return conn.execute("SELECT data FROM state WHERE key='bot:audit:legacy_checkpoint:v1'").fetchone()[0]

    def replace(self, data):
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            conn.execute("UPDATE state SET data=? WHERE key='bot:audit:legacy_checkpoint:v1'", (json.dumps(data),))

    async def test_open_position_broker_orders_and_marks_restore_without_orders(self):
        await self.buy()
        await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("0")})
        await self.bot.stop()
        before = json.loads(self.raw())
        resumed = await self.new_bot()
        self.assertIsNone(resumed.recovery_reason)
        self.assertEqual(resumed.exchange.snapshot(), before["exchange"])
        self.assertEqual(resumed.portfolio.get_position("BTC/USDT").current_price, Decimal("0"))
        self.assertEqual(len(resumed.order_manager.get_all_orders()), 1)
        self.assertEqual(resumed.portfolio.cash_balance, Decimal("9000"))

    async def test_direct_order_boundary_requires_a_current_durable_intent(self):
        self.bot.exchange.set_price("BTC/USDT", Decimal("100"))
        with self.assertRaisesRegex(RuntimeError, "intent"):
            await self.bot.order_manager.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"))
        self.assertEqual(self.bot.exchange.snapshot()["orders"], {})

    async def test_risk_only_gap_allows_explicit_exit_keeps_source_and_blocks_later_buy(self):
        await self.buy()
        await self.bot.stop()
        damaged = json.loads(self.raw())
        del damaged["accounting"]["daily_start_value"]
        self.replace(damaged)
        original = self.raw()
        resumed = await self.new_bot()
        self.assertTrue(resumed._paper_checkpoint.risk_only_recovery)
        self.assertIsNone(resumed.get_recovery_status()["risk"]["daily_pnl"])
        # Neither boot nor a quote callback automatically liquidates a recovery position.
        await resumed._on_price_update({"symbol": "BTC/USDT", "price": Decimal("80")})
        self.assertTrue(resumed.portfolio.has_position("BTC/USDT"))
        await self.buy(resumed, "ETH/USDT")
        self.assertFalse(resumed.portfolio.has_position("ETH/USDT"))
        await resumed._close_position("BTC/USDT", Decimal("80"), "explicit recovery exit")
        self.assertFalse(resumed.portfolio.has_position("BTC/USDT"))
        self.assertIsNotNone(resumed.recovery_reason)
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            source, = conn.execute("SELECT data FROM state WHERE key LIKE '%:recovery_source:%'").fetchone()
        self.assertEqual(source, original)
        await resumed.stop()
        again = await self.new_bot()
        self.assertIsNotNone(again.recovery_reason)
        self.assertEqual(again.portfolio.cash_balance, Decimal("9800"))
        await self.buy(again, "ETH/USDT")
        self.assertFalse(again.portfolio.has_position("ETH/USDT"))

    async def test_source_archive_and_checkpoint_rollback_together_on_write_failure(self):
        await self.buy()
        await self.bot.stop()
        damaged = json.loads(self.raw())
        del damaged["accounting"]["daily_start_value"]
        self.replace(damaged)
        resumed = await self.new_bot()
        original = self.raw()
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            conn.execute("CREATE TRIGGER fail_projection BEFORE UPDATE ON state WHEN NEW.key='bot:audit:positions' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
        with self.assertRaisesRegex(RuntimeError, "transaction"):
            await resumed._close_position("BTC/USDT", Decimal("100"), "explicit recovery exit")
        self.assertEqual(self.raw(), original)
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM state WHERE key LIKE '%:recovery_source:%'").fetchone()[0], 0)
        self.assertEqual(len(resumed.exchange.snapshot()["orders"]), 1)

    async def test_circuit_halt_does_not_prevent_explicit_reducing_sell(self):
        await self.buy()
        await self.buy(symbol="ETH/USDT")
        self.bot.exchange.set_price("BTC/USDT", Decimal("40"))
        await self.bot._close_position("BTC/USDT", Decimal("40"), "fixture loss")
        self.assertTrue(self.bot.risk_manager._circuit_breaker_triggered)
        self.bot.exchange.set_price("ETH/USDT", Decimal("90"))
        await self.bot._process_signal(Signal(SignalType.SELL, "ETH/USDT", Decimal("90"), Clock.utcnow()))
        self.assertFalse(self.bot.portfolio.has_position("ETH/USDT"))
        await self.buy(symbol="LTC/USDT")
        self.assertFalse(self.bot.portfolio.has_position("LTC/USDT"))

    async def test_pending_close_is_never_reissued_on_boot_quote_or_explicit_retry(self):
        await self.buy()
        self.bot._paper_checkpoint.begin_intent("sell", "BTC/USDT", amount=self.bot.portfolio.get_position("BTC/USDT").size, price=Decimal("100"))
        await self.bot.stop()
        original = self.raw()
        resumed = await self.new_bot()
        self.assertIn("intent", resumed.recovery_reason)
        resumed.exchange.set_price("BTC/USDT", Decimal("80"))
        await resumed._on_price_update({"symbol": "BTC/USDT", "price": Decimal("80")})
        await resumed._close_position("BTC/USDT", Decimal("80"), "retry")
        await self.buy(resumed, "ETH/USDT")
        self.assertEqual(len(resumed.exchange.snapshot()["orders"]), 1)
        self.assertEqual(self.raw(), original)

    async def test_cancellation_after_paper_fill_preserves_ambiguous_intent(self):
        original_create = self.bot.exchange.create_order

        async def fill_then_cancel(*args, **kwargs):
            await original_create(*args, **kwargs)
            raise asyncio.CancelledError()

        with patch.object(self.bot.exchange, "create_order", side_effect=fill_then_cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.buy()
        await self.bot.stop()
        original = self.raw()
        resumed = await self.new_bot()
        self.assertIn("intent", resumed.recovery_reason)
        await self.buy(resumed)
        self.assertEqual(self.raw(), original)
        self.assertEqual(resumed.exchange.snapshot()["orders"], {})

    async def test_failed_final_commit_does_not_clear_durable_intent(self):
        await self.buy()
        self.bot.exchange.set_price("BTC/USDT", Decimal("40"))
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            conn.execute("CREATE TRIGGER fail_finish BEFORE UPDATE ON state WHEN NEW.key='bot:audit:legacy_checkpoint:v1' AND json_extract(NEW.data,'$.intent') IS NULL BEGIN SELECT RAISE(ABORT,'fixture final commit failure'); END")
        with self.assertRaises(RuntimeError):
            await self.bot._close_position("BTC/USDT", Decimal("40"), "loss")
        self.assertIsNotNone(json.loads(self.raw())["intent"])
        await self.bot.stop()
        resumed = await self.new_bot()
        self.assertIn("intent", resumed.recovery_reason)
        self.assertTrue(resumed.portfolio.has_position("BTC/USDT"))
        await self.buy(resumed, "ETH/USDT")
        self.assertFalse(resumed.portfolio.has_position("ETH/USDT"))

    async def test_shutdown_waits_for_serialized_inflight_paper_transition(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        original_create = self.bot.exchange.create_order

        async def delayed_create(*args, **kwargs):
            entered.set()
            await finish.wait()
            return await original_create(*args, **kwargs)

        with patch.object(self.bot.exchange, "create_order", side_effect=delayed_create):
            order_task = asyncio.create_task(self.buy())
            await entered.wait()
            stop_task = asyncio.create_task(self.bot.stop())
            await asyncio.sleep(0)
            self.assertFalse(stop_task.done())
            finish.set()
            await asyncio.gather(order_task, stop_task)
        resumed = await self.new_bot()
        self.assertIsNone(resumed.recovery_reason)
        self.assertTrue(resumed.portfolio.has_position("BTC/USDT"))

    async def test_second_actual_bot_cannot_run_as_a_new_funded_writer(self):
        second = await self.new_bot()
        self.assertIn("writer", second.recovery_reason)
        self.assertIsNone(second.exchange)
        await second.stop()
        await self.buy()
        self.assertTrue(self.bot.portfolio.has_position("BTC/USDT"))

    async def test_strategy_initialization_exception_releases_writer_and_blocks_failed_bot(self):
        await self.bot.stop()
        failed = self.Bot("config")
        self.bots.append(failed)
        failed._setup_strategies = AsyncMock(side_effect=ValueError("fixture strategy failure"))
        with self.assertRaisesRegex(ValueError, "fixture strategy"):
            await failed.initialize()
        self.assertIsNone(failed._paper_checkpoint._descriptor)
        self.assertIsNotNone(failed.recovery_reason)
        resumed = await self.new_bot()
        self.assertIsNone(resumed.recovery_reason)

    async def test_cas_failure_revokes_in_memory_execution_permission(self):
        session = self.bot._paper_checkpoint
        self.bot.exchange.set_price("BTC/USDT", Decimal("100"))
        key = session.begin_intent("buy", "BTC/USDT", amount=Decimal("1"), price=Decimal("100"))
        altered = json.loads(self.raw())
        altered["generation"] += 1
        self.replace(altered)
        with self.assertRaisesRegex(RuntimeError, "generation"):
            session.save(transition=True)
        with self.assertRaisesRegex(RuntimeError, "intent"):
            await self.bot.order_manager.place_order("BTC/USDT", OrderSide.BUY, OrderType.MARKET,
                Decimal("1"), client_order_id=key)
        self.assertEqual(self.bot.exchange.snapshot()["orders"], {})
        self.assertEqual(json.loads(self.raw()), altered)

    async def test_json_actual_initialize_and_stop_preserve_prior_file(self):
        await self.bot.stop()
        store = JSONStateStore("json-state")
        store.save("bot:audit:portfolio", {"cash_balance": "9400"})
        path = store._get_file_path("bot:audit:portfolio")
        original = path.read_bytes()
        resumed = await self.new_bot(store)
        self.assertIn("SQLite", resumed.recovery_reason)
        await resumed.stop()
        self.assertIsNone(resumed.exchange)
        self.assertEqual(path.read_bytes(), original)

    async def test_corrupt_row_is_preserved_by_actual_initialize_and_stop(self):
        await self.bot.stop()
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            conn.execute("UPDATE state SET data='not json' WHERE key='bot:audit:legacy_checkpoint:v1'")
        original = self.raw()
        resumed = await self.new_bot()
        self.assertIsNone(resumed.exchange)
        await resumed.stop()
        self.assertEqual(self.raw(), original)

    async def test_next_utc_day_rolls_once_and_diagnostics_do_not_roll(self):
        await self.buy()
        self.bot.exchange.set_price("BTC/USDT", Decimal("40"))
        await self.bot._close_position("BTC/USDT", Decimal("40"), "loss")
        old_day = self.bot.portfolio.daily_date
        Clock.current += timedelta(days=1)
        self.assertEqual(self.bot.get_recovery_status()["risk"]["daily_pnl"], -600)
        self.assertEqual(self.bot.portfolio.daily_date, old_day)
        await self.bot._save_state()
        self.assertEqual(self.bot.portfolio._daily_start_value, Decimal("9400"))
        self.assertEqual(self.bot.risk_manager._daily_stats["trade_count"], 0)
        await self.buy(symbol="ETH/USDT")
        self.assertTrue(self.bot.portfolio.has_position("ETH/USDT"))
        await self.bot.stop()
        resumed = await self.new_bot()
        self.assertIsNone(resumed.recovery_reason)
        self.assertEqual(resumed.portfolio._daily_start_value, Decimal("9400"))

    async def test_recovered_gains_do_not_clear_saved_circuit_latch(self):
        await self.buy()
        await self.buy(symbol="ETH/USDT")
        self.bot.exchange.set_price("BTC/USDT", Decimal("40"))
        await self.bot._close_position("BTC/USDT", Decimal("40"), "loss")
        self.bot.exchange.set_price("ETH/USDT", Decimal("170"))
        await self.bot._close_position("ETH/USDT", Decimal("170"), "recovery gain")
        self.assertEqual(self.bot.portfolio._daily_pnl, Decimal("100"))
        await self.bot.stop()
        resumed = await self.new_bot()
        self.assertTrue(resumed.risk_manager._circuit_breaker_triggered)
        await self.buy(resumed, "LTC/USDT")
        self.assertFalse(resumed.portfolio.has_position("LTC/USDT"))

    async def test_future_trade_period_cannot_hide_realized_loss(self):
        await self.buy()
        self.bot.exchange.set_price("BTC/USDT", Decimal("40"))
        await self.bot._close_position("BTC/USDT", Decimal("40"), "loss")
        await self.bot.stop()
        data = json.loads(self.raw())
        data["portfolio"]["trades"][0]["accounting_date"] = "2027-10-04"
        data["accounting"]["daily_pnl"] = "0"
        data["risk"].update(total_pnl="0", trade_count=0, loss_count=0,
                            circuit_breaker=False, cooldown_until=None)
        self.replace(data)
        resumed = await self.new_bot()
        self.assertIsNotNone(resumed.recovery_reason)
        await self.buy(resumed, "ETH/USDT")
        self.assertFalse(resumed.portfolio.has_position("ETH/USDT"))

    async def timestamped_buy(self):
        self.bot.exchange.set_ohlcv("BTC/USDT", "1h", [[1000, 100, 100, 100, 100, 1]])
        await self.bot._process_signal(Signal(SignalType.BUY, "BTC/USDT", Decimal("100"), Clock.utcnow()))
        self.assertTrue(self.bot.portfolio.has_position("BTC/USDT"))

    async def test_timestamped_callback_and_explicit_close_commit_and_restore(self):
        await self.timestamped_buy()
        await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("101"), "timestamp": 2000})
        await self.bot._process_signal(Signal(SignalType.SELL, "BTC/USDT", Decimal("101"), Clock.utcnow()))
        self.assertEqual([t["timestamp"] for t in self.bot.exchange.snapshot()["trades"]], [1000, 2000])
        await self.bot.stop()
        resumed = await self.new_bot()
        self.assertIsNone(resumed.recovery_reason)
        self.assertFalse(resumed.portfolio.has_position("BTC/USDT"))

    async def test_timestamped_stop_callback_closes_without_ambiguous_intent(self):
        await self.timestamped_buy()
        await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("90"), "timestamp": 2000})
        self.assertFalse(self.bot.portfolio.has_position("BTC/USDT"))
        self.assertIsNone(json.loads(self.raw())["intent"])
        self.assertEqual(self.bot.exchange.snapshot()["trades"][-1]["timestamp"], 2000)

    async def test_timestamped_take_profit_callback_closes_without_ambiguous_intent(self):
        await self.timestamped_buy()
        await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("105"), "timestamp": 2000})
        self.assertFalse(self.bot.portfolio.has_position("BTC/USDT"))
        self.assertIsNone(json.loads(self.raw())["intent"])
        self.assertEqual(self.bot.exchange.snapshot()["trades"][-1]["timestamp"], 2000)

    async def test_missing_timestamp_preserves_known_timestamp_and_regression_is_ignored(self):
        await self.timestamped_buy()
        await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("101")})
        quote = await self.bot.exchange.fetch_ticker("BTC/USDT")
        self.assertEqual(quote.timestamp, 1000)
        for timestamp in (500, -1, True, "2000", 2.5):
            await self.bot._on_price_update({"symbol": "BTC/USDT", "price": Decimal("90"), "timestamp": timestamp})
            current = await self.bot.exchange.fetch_ticker("BTC/USDT")
            self.assertEqual((current.last, current.timestamp), (Decimal("101"), 1000))
            self.assertTrue(self.bot.portfolio.has_position("BTC/USDT"))

    async def test_unsupported_currency_never_spends_an_existing_quote_asset(self):
        self.bot.exchange.set_price("BTC/USDT", Decimal("1"))
        await self.bot._process_signal(Signal(SignalType.BUY, "BTC/USDT", Decimal("1"), Clock.utcnow()))
        self.bot.exchange.set_price("ETH/BTC", Decimal("100"))
        broker, portfolio, canonical = self.bot.exchange.snapshot(), self.bot.portfolio.to_dict(), self.raw()
        await self.bot._process_signal(Signal(SignalType.BUY, "ETH/BTC", Decimal("100"), Clock.utcnow()))
        self.assertEqual(self.bot.exchange.snapshot(), broker)
        self.assertEqual(self.bot.portfolio.to_dict(), portfolio)
        self.assertEqual(self.raw(), canonical)
        self.assertIsNone(self.bot._paper_checkpoint.intent)
        with self.assertRaisesRegex(RuntimeError, "USDT"):
            self.bot._paper_checkpoint.begin_intent("buy", "ETH/BTC", amount=Decimal("10"), price=Decimal("100"))

    async def test_zero_byte_existing_database_is_not_initialized_or_changed(self):
        await self.bot.stop()
        path = Path("data/paper_bot_state.db")
        path.write_bytes(b"")
        resumed = await self.new_bot()
        self.assertIsNotNone(resumed.recovery_reason)
        self.assertIsNone(resumed.exchange)
        await resumed.stop()
        self.assertEqual(path.read_bytes(), b"")

    async def test_foreign_existing_database_schema_and_bytes_are_preserved(self):
        await self.bot.stop()
        path = Path("data/paper_bot_state.db")
        path.unlink()
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE evidence(value TEXT)")
            conn.execute("INSERT INTO evidence VALUES ('preserve this')")
        original = path.read_bytes()
        resumed = await self.new_bot()
        self.assertIsNotNone(resumed.recovery_reason)
        await resumed.stop()
        self.assertEqual(path.read_bytes(), original)

    async def test_missing_database_with_retained_workspace_marker_never_reseeds(self):
        await self.bot.stop()
        path = Path("data/paper_bot_state.db")
        self.assertTrue(path.with_name(".paper_bot_state.db.legacy-paper.lock").exists())
        path.unlink()
        resumed = await self.new_bot()
        self.assertIsNotNone(resumed.recovery_reason)
        self.assertIsNone(resumed.portfolio)
        self.assertIsNone(resumed.exchange)
        await resumed.stop()
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
