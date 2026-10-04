"""Offline application-method checks with isolated config and temporary state."""

import ast
import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[3]
BOT_SOURCE = ROOT / "apps/server/trading_bot/main.py"


def isolated_bot_class(namespace):
    tree = ast.parse(BOT_SOURCE.read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name in ("RecoveryRiskGate", "TradingBot")]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *classes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(BOT_SOURCE), "exec"), namespace)
    return namespace["TradingBot"]


class BotExecutionSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="legacy-adapter-safety-")
        self.addCleanup(self.temp.cleanup)
        self.config = SimpleNamespace(
            name="offline", version="test", mode="paper", bot_id="offline",
            exchange=SimpleNamespace(id="binance", api_key="", api_secret="", sandbox=False, testnet=False),
            telegram=SimpleNamespace(enabled=True, bot_token="unused", chat_id="unused", notifications={}),
            risk=SimpleNamespace(max_position_size_pct=10, max_positions=2, risk_per_trade_pct=1, max_daily_loss_pct=3, max_leverage=1),
            strategies=[],
        )
        self.forbidden_ccxt = Mock(side_effect=AssertionError("paper constructed CCXT"))
        self.forbidden_ws = Mock(side_effect=AssertionError("paper constructed WebSocket"))
        self.forbidden_notifier = Mock(side_effect=AssertionError("paper constructed Telegram notifier"))
        state = Mock()
        state.load_portfolio.return_value = None
        state.load_orders.return_value = None
        manager = Mock()
        manager.start = AsyncMock()
        null = Mock()
        null.notify_startup = AsyncMock()
        self.namespace = {
            "load_config": Mock(return_value=self.config), "logger": logging.getLogger("offline-test"),
            "CCXTExchange": self.forbidden_ccxt, "BinanceWebSocket": self.forbidden_ws,
            "TelegramNotifier": self.forbidden_notifier, "NullNotifier": Mock(return_value=null),
            "SQLiteStateStore": Mock(), "BotState": Mock(return_value=state),
            "Portfolio": Mock(return_value=SimpleNamespace(cash_balance=Decimal("10000"), positions={})),
            "OrderManager": Mock(return_value=manager), "RiskManagerConfig": Mock(), "RiskManager": Mock(),
            "NotificationConfig": Mock(), "Decimal": Decimal, "asyncio": asyncio,
        }
        # Once present, use the real factory; importing it must remain offline.
        core_path = str(ROOT / "packages/core")
        sys.path.insert(0, core_path)
        try:
            from trading_core.execution import create_execution_exchange, validate_execution_mode
            from trading_core.paper_exchange import PaperExchange
            from trading_core.portfolio import Portfolio
            from trading_core.legacy_checkpoint import LegacyCheckpoint, CheckpointError
            from trading_core.risk_manager import RiskConfig
            from trading_core.state_store import SQLiteStateStore
        except ImportError:
            pass
        else:
            self.namespace.update(create_execution_exchange=create_execution_exchange, validate_execution_mode=validate_execution_mode, PaperExchange=PaperExchange)
            self.namespace["Portfolio"] = Portfolio
            self.namespace.update(LegacyCheckpoint=LegacyCheckpoint, CheckpointError=CheckpointError,
                                  RiskManagerConfig=RiskConfig,
                                  SQLiteStateStore=lambda *args, **kwargs: SQLiteStateStore(str(Path(self.temp.name) / "paper.db"), **kwargs))
        self.bot_class = isolated_bot_class(self.namespace)

    async def test_paper_initialization_does_not_construct_network_adapters(self):
        bot = self.bot_class()
        bot._setup_strategies = AsyncMock()
        config_module = SimpleNamespace(get_config_manager=lambda *args: SimpleNamespace(validate_config=lambda: []))
        with patch.dict(sys.modules, {"trading_core.config_manager": config_module}), patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            await bot.initialize()
        self.forbidden_ccxt.assert_not_called()
        self.forbidden_ws.assert_not_called()
        self.forbidden_notifier.assert_not_called()
        self.assertIsNone(bot.websocket)
        self.assertIsInstance(bot.exchange, self.namespace["PaperExchange"])
        self.assertIsNone(bot.recovery_reason)
        await bot.stop()

    async def test_paper_start_skips_websocket(self):
        bot = self.bot_class.__new__(self.bot_class)
        bot.config = self.config
        bot.websocket = Mock(connect=AsyncMock(side_effect=AssertionError("WebSocket forbidden")))
        bot.candle_buffers = {}
        bot._main_loop = AsyncMock()
        bot._save_state_loop = AsyncMock()
        bot._risk_check_loop = AsyncMock()
        await bot.start()
        bot.websocket.connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
