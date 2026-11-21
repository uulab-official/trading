"""
Trading Core Package

Core modules for crypto trading bot:
- Exchange abstraction
- Order management
- Risk management
- Portfolio tracking
- State persistence
"""

from trading_core.config_manager import ConfigManager, load_config
from trading_core.exchange import CCXTExchange, ExchangeInterface
from trading_core.execution import create_execution_exchange, validate_execution_mode
from trading_core.order_manager import Order, OrderManager, OrderSide, OrderStatus, OrderType
from trading_core.paper_exchange import PaperExchange
from trading_core.portfolio import Portfolio, Position
from trading_core.risk_manager import RiskConfig, RiskManager
from trading_core.state_store import BotState, SQLiteStateStore, StateStore
from trading_core.telegram_notifier import NotificationConfig, NullNotifier, TelegramNotifier

__all__ = [
    "ExchangeInterface",
    "CCXTExchange",
    "PaperExchange",
    "create_execution_exchange",
    "validate_execution_mode",
    "OrderManager",
    "Order",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "RiskManager",
    "RiskConfig",
    "Portfolio",
    "Position",
    "StateStore",
    "SQLiteStateStore",
    "BotState",
    "ConfigManager",
    "load_config",
    "TelegramNotifier",
    "NotificationConfig",
    "NullNotifier",
]

__version__ = "0.1.0"
