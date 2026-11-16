"""Causal market-structure indicators using only closed OHLC candles.

A swing at index i is *confirmed* only after right bars have closed.
Signals are emitted at confirmation time, never retroactively at the pivot.
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        import math
        if not all(math.isfinite(v) for v in (self.open, self.high, self.low, self.close)):
            raise ValueError("non-finite OHLC")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC range")


@dataclass(frozen=True)
class StructureEvent:
    timestamp: int
    kind: str
    direction: str
    level: float
    pivot_timestamp: Optional[int] = None


def market_structure(candles: Iterable[Candle], left: int = 2, right: int = 2) -> List[StructureEvent]:
    """Detect confirmed swings and close-based BOS/CHOCH without lookahead."""
    if left < 1 or right < 1:
        raise ValueError("left/right must be >= 1")
    data = list(candles)
    if any(data[i].timestamp <= data[i - 1].timestamp for i in range(1, len(data))):
        raise ValueError("timestamps must be strictly increasing")
    events: List[StructureEvent] = []
    high_level = low_level = None
    high_broken = low_broken = False
    trend = None
    for end, current in enumerate(data):
        pivot = end - right
        if pivot >= left:
            center = data[pivot]
            window = data[pivot - left:end + 1]
            if all(center.high > bar.high for j, bar in enumerate(window) if j != left):
                high_level = center.high
                high_broken = False
                events.append(StructureEvent(current.timestamp, "swing_high", "up", high_level, center.timestamp))
            if all(center.low < bar.low for j, bar in enumerate(window) if j != left):
                low_level = center.low
                low_broken = False
                events.append(StructureEvent(current.timestamp, "swing_low", "down", low_level, center.timestamp))
        if high_level is not None and not high_broken and current.close > high_level:
            kind = "choch" if trend == "down" else "bos"
            events.append(StructureEvent(current.timestamp, kind, "up", high_level))
            trend, high_broken = "up", True
        if low_level is not None and not low_broken and current.close < low_level:
            kind = "choch" if trend == "up" else "bos"
            events.append(StructureEvent(current.timestamp, kind, "down", low_level))
            trend, low_broken = "down", True
    return events
