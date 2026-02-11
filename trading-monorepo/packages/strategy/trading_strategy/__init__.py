"""Strategy package - 전략 베이스 및 구현"""

from trading_strategy.base import (
    Strategy, 
    Signal, 
    SignalType,
    StrategyConfig,
)
from trading_strategy.rsi_strategy import RSIStrategy
from trading_strategy.ma_strategy import MAStrategy, MACrossStrategy
from trading_strategy.breakout_strategy import BreakoutStrategy

__all__ = [
    # Base
    "Strategy",
    "Signal",
    "SignalType",
    "StrategyConfig",
    # Implementations
    "RSIStrategy",
    "MAStrategy",
    "MACrossStrategy",
    "BreakoutStrategy",
]

__version__ = "0.1.0"
