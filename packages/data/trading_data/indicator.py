"""Stateless Decimal indicators for supplied offline price histories.

    Every calculation uses a fixed 28-digit, half-even Decimal context. No
    network, clock, environment, exchange, or global mutable indicator state.
"""

from collections.abc import Mapping, Set
from decimal import Decimal, localcontext
from typing import Iterable, List

from ._validation import CALCULATION_CONTEXT, finite_decimal, positive_decimal, positive_integer


def _prices(values: Iterable[Decimal], name: str = "prices") -> List[Decimal]:
    if isinstance(values, (str, bytes, bytearray, Mapping, Set)):
        raise ValueError(f"{name} must be an ordered iterable of prices")
    try:
        return [positive_decimal(value, name) for value in values]
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of positive finite prices") from exc


class _PeriodIndicator:
    def __init__(self, period: int) -> None:
        self._period = positive_integer(period, "period")

    @property
    def period(self) -> int:
        return self._period

    def _history(self, prices: Iterable[Decimal], extra: int = 0) -> List[Decimal]:
        result = _prices(prices)
        if len(result) < self.period + extra:
            raise ValueError(f"insufficient prices: need at least {self.period + extra}")
        return result


class SMA(_PeriodIndicator):
    """Arithmetic mean of the latest period prices."""

    def calculate(self, prices: Iterable[Decimal]) -> Decimal:
        values = self._history(prices)
        with localcontext(CALCULATION_CONTEXT):
            return sum(values[-self.period:], Decimal("0")) / Decimal(self.period)


class EMA(_PeriodIndicator):
    """First-period SMA seed, then alpha = 2 / (period + 1)."""

    def calculate(self, prices: Iterable[Decimal]) -> Decimal:
        values = self._history(prices)
        with localcontext(CALCULATION_CONTEXT):
            value = sum(values[:self.period], Decimal("0")) / Decimal(self.period)
            alpha = Decimal("2") / Decimal(self.period + 1)
            for price in values[self.period:]:
                value = alpha * price + (Decimal("1") - alpha) * value
            return value


class RSI(_PeriodIndicator):
    """Wilder RSI: period deltas require period + 1 prices; flat RSI is 50."""

    def __init__(
        self, period: int, oversold: Decimal = Decimal("30"),
        overbought: Decimal = Decimal("70"),
    ) -> None:
        super().__init__(period)
        self.oversold = finite_decimal(oversold, "oversold")
        self.overbought = finite_decimal(overbought, "overbought")
        if not Decimal("0") <= self.oversold < self.overbought <= Decimal("100"):
            raise ValueError("RSI thresholds must satisfy 0 <= oversold < overbought <= 100")

    def calculate(self, prices: Iterable[Decimal]) -> Decimal:
        values = self._history(prices, extra=1)
        with localcontext(CALCULATION_CONTEXT):
            changes = [right - left for left, right in zip(values, values[1:])]
            gains = [max(change, Decimal("0")) for change in changes]
            losses = [max(-change, Decimal("0")) for change in changes]
            divisor = Decimal(self.period)
            average_gain = sum(gains[:self.period], Decimal("0")) / divisor
            average_loss = sum(losses[:self.period], Decimal("0")) / divisor
            for gain, loss in zip(gains[self.period:], losses[self.period:]):
                average_gain = (average_gain * (self.period - 1) + gain) / divisor
                average_loss = (average_loss * (self.period - 1) + loss) / divisor
            if average_gain == 0 and average_loss == 0:
                return Decimal("50")
            if average_loss == 0:
                return Decimal("100")
            return Decimal("100") - Decimal("100") / (Decimal("1") + average_gain / average_loss)


class ATR(_PeriodIndicator):
    """Wilder ATR, with first-bar true range equal to high minus low."""

    def calculate(
        self, highs: Iterable[Decimal], lows: Iterable[Decimal], closes: Iterable[Decimal],
    ) -> Decimal:
        highs, lows, closes = _prices(highs, "highs"), _prices(lows, "lows"), _prices(closes, "closes")
        if not len(highs) == len(lows) == len(closes):
            raise ValueError("highs, lows, and closes must have equal lengths")
        if len(highs) < self.period:
            raise ValueError(f"insufficient bars: need at least {self.period}")
        for high, low, close in zip(highs, lows, closes):
            if not low <= close <= high:
                raise ValueError("ATR bars must satisfy low <= close <= high")
        with localcontext(CALCULATION_CONTEXT):
            ranges = [highs[0] - lows[0]]
            for index in range(1, len(highs)):
                ranges.append(max(
                    highs[index] - lows[index], abs(highs[index] - closes[index - 1]),
                    abs(lows[index] - closes[index - 1]),
                ))
            divisor = Decimal(self.period)
            value = sum(ranges[:self.period], Decimal("0")) / divisor
            for true_range in ranges[self.period:]:
                value = (value * (self.period - 1) + true_range) / divisor
            return value
