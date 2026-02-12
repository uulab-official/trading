"""
Trading Core Package

Core modules for crypto trading bot:
- Exchange abstraction
- Order management
- Risk management  
- Portfolio tracking
- State persistence
"""

from trading_core.exchange import ExchangeInterface, CCXTExchange
from trading_core.order_manager import OrderManager, Order, OrderSide, OrderType, OrderStatus
from trading_core.risk_manager import RiskManager, RiskConfig
from trading_core.portfolio import Portfolio, Position
from trading_core.state_store import StateStore, SQLiteStateStore
from trading_core.config_manager import ConfigManager, load_config
from trading_core.telegram_notifier import TelegramNotifier, NotificationConfig, NullNotifier

__all__ = [
    "ExchangeInterface",
    "CCXTExchange",
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
    "ConfigManager",
    "load_config",
    "TelegramNotifier",
    "NotificationConfig",
    "NullNotifier",
]

__version__ = "0.1.0"
