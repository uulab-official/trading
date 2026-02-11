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

__all__ = [
    # Exchange
    "ExchangeInterface",
    "CCXTExchange",
    # Orders
    "OrderManager",
    "Order",
    "OrderSide",
    "OrderType", 
    "OrderStatus",
    # Risk
    "RiskManager",
    "RiskConfig",
    # Portfolio
    "Portfolio",
    "Position",
    # State
    "StateStore",
    "SQLiteStateStore",
]

__version__ = "0.1.0"
