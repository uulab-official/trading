"""Trading core public API, resolved without unrelated import-time integrations.

Importing a paper submodule must not load configuration or notifications. Public
re-exports keep their original objects and load only when explicitly requested.
Dependency errors and diagnostics from requested integrations are not suppressed.
"""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from trading_core.config_manager import ConfigManager, load_config
    from trading_core.exchange import CCXTExchange, ExchangeInterface
    from trading_core.execution import create_execution_exchange, validate_execution_mode
    from trading_core.order_manager import Order, OrderManager, OrderSide, OrderStatus, OrderType
    from trading_core.paper_exchange import PaperExchange
    from trading_core.portfolio import Portfolio, Position
    from trading_core.risk_manager import RiskConfig, RiskManager
    from trading_core.state_store import BotState, SQLiteStateStore, StateStore
    from trading_core.telegram_notifier import NotificationConfig, NullNotifier, TelegramNotifier

# Retain the existing public names and their ordering. No feature module is
# imported by building this table, inspecting __all__, or calling dir().
_EXPORT_MODULES = {
    "ExchangeInterface": "exchange",
    "CCXTExchange": "exchange",
    "PaperExchange": "paper_exchange",
    "create_execution_exchange": "execution",
    "validate_execution_mode": "execution",
    "OrderManager": "order_manager",
    "Order": "order_manager",
    "OrderSide": "order_manager",
    "OrderType": "order_manager",
    "OrderStatus": "order_manager",
    "RiskManager": "risk_manager",
    "RiskConfig": "risk_manager",
    "Portfolio": "portfolio",
    "Position": "portfolio",
    "StateStore": "state_store",
    "SQLiteStateStore": "state_store",
    "BotState": "state_store",
    "ConfigManager": "config_manager",
    "load_config": "config_manager",
    "TelegramNotifier": "telegram_notifier",
    "NotificationConfig": "telegram_notifier",
    "NullNotifier": "telegram_notifier",
}

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


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Do not catch ImportError: missing dependencies of an explicitly requested
    # integration must remain visible, and unsuccessful imports must not cache.
    value = getattr(_import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
