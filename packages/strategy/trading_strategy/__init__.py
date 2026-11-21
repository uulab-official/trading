"""Strategy package - 전략 베이스 및 구현"""

from trading_strategy.base import (
    Signal,
    SignalType,
    Strategy,
    StrategyConfig,
)
from trading_strategy.breakout_strategy import BreakoutStrategy
from trading_strategy.ma_strategy import MACrossStrategy, MAStrategy
from trading_strategy.rsi_strategy import RSIStrategy

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
