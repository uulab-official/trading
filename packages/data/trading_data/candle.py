"""Validated historical candles and deterministic, local-only tick aggregation."""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
import re
from typing import Deque, List, Optional, Union

from ._validation import (
    CALCULATION_CONTEXT, nonnegative_decimal, positive_decimal,
    positive_integer, timestamp_milliseconds,
)


@dataclass(frozen=True)
class Candle:
    """An immutable OHLCV bar with a Unix-millisecond opening timestamp."""

    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        timestamp_milliseconds(self.timestamp)
        for field in ("open", "high", "low", "close"):
            object.__setattr__(self, field, positive_decimal(getattr(self, field), field))
        object.__setattr__(self, "volume", nonnegative_decimal(self.volume, "volume"))
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("OHLC must satisfy low <= open/close <= high")


class CandleBuffer:
    """Bounded completed bars. Exact retained replays are safe no-ops.

    Tick aggregation is optional and entirely offline. A bucket change returns
    the prior completed candle without inserting it; call add_candle explicitly.
    The pending bucket is excluded from current, count, and all history methods.
    """

    def __init__(self, max_size: int = 500, timeframe: str = "1h") -> None:
        self.max_size = positive_integer(max_size, "max_size")
        if not isinstance(timeframe, str):
            raise ValueError("timeframe must be a fixed duration such as '1m' or '1h'")
        match = re.fullmatch(r"([1-9][0-9]*)(ms|s|m|h|d|w)", timeframe)
        if match is None:
            raise ValueError("timeframe must be a positive fixed duration in ms/s/m/h/d/w")
        units = {"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}
        self.timeframe = timeframe
        self._duration_ms = int(match.group(1)) * units[match.group(2)]
        self._candles: Deque[Candle] = deque(maxlen=self.max_size)
        self._pending: Optional[Candle] = None
        self._last_tick: Optional[tuple] = None

    @property
    def duration_ms(self) -> int:
        """Validated fixed timeframe duration, in milliseconds (read-only)."""
        return self._duration_ms

    @property
    def current(self) -> Optional[Candle]:
        return self._candles[-1] if self._candles else None

    @property
    def count(self) -> int:
        return len(self._candles)

    def add_candle(self, candle: Union[Candle, list]) -> bool:
        """Append a completed bar; return False for an exact retained replay.

        Conflicting duplicates and unseen timestamps preceding the latest bar
        are rejected before mutation. Evicted bars cannot be replayed.
        """
        if isinstance(candle, list) and len(candle) == 6:
            candle = Candle(*candle)
        if not isinstance(candle, Candle):
            raise ValueError("candle must be a Candle or a six-element OHLCV list")
        for existing in self._candles:
            if existing.timestamp == candle.timestamp:
                if existing != candle:
                    raise ValueError("conflicting candle for an existing timestamp")
                return False
        if self.current is not None and candle.timestamp <= self.current.timestamp:
            raise ValueError("out-of-order candle timestamp")
        self._candles.append(candle)
        return True

    def get_price_history(self) -> List[Decimal]:
        return [candle.close for candle in self._candles]

    def get_all_candles(self) -> List[Candle]:
        return list(self._candles)

    def get_highs(self) -> List[Decimal]:
        return [candle.high for candle in self._candles]

    def get_lows(self) -> List[Decimal]:
        return [candle.low for candle in self._candles]

    def get_volumes(self) -> List[Decimal]:
        return [candle.volume for candle in self._candles]

    def add_tick(
        self, timestamp: int, price: Decimal, volume: Decimal = Decimal("0"),
    ) -> Optional[Candle]:
        """Aggregate supplied ticks; never fetch ticks or fill missing buckets.

        Identical repeated latest ticks are ignored. Conflicting ticks at the
        same timestamp and late ticks are rejected. Volume is incremental.
        """
        timestamp = timestamp_milliseconds(timestamp)
        price = positive_decimal(price, "price")
        volume = nonnegative_decimal(volume, "volume")
        tick = (timestamp, price, volume)
        if self._last_tick is not None:
            if timestamp < self._last_tick[0]:
                raise ValueError("out-of-order tick timestamp")
            if timestamp == self._last_tick[0]:
                if tick != self._last_tick:
                    raise ValueError("conflicting tick for an existing timestamp")
                return None
        bucket = timestamp - timestamp % self._duration_ms
        if self.current is not None and bucket <= self.current.timestamp:
            raise ValueError("tick would reopen an already completed candle bucket")
        completed = None
        if self._pending is None or bucket > self._pending.timestamp:
            completed = self._pending
            pending = Candle(bucket, price, price, price, price, volume)
        else:
            with localcontext(CALCULATION_CONTEXT):
                total_volume = self._pending.volume + volume
            pending = Candle(
                bucket, self._pending.open, max(self._pending.high, price),
                min(self._pending.low, price), price, total_volume,
            )
        self._pending = pending
        self._last_tick = tick
        return completed

    def clear(self) -> None:
        self._candles.clear()
        self._pending = None
        self._last_tick = None
