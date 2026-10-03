"""Pure offline market-data primitives. No live market-data adapter is supplied."""

from .candle import Candle, CandleBuffer
from .indicator import ATR, EMA, RSI, SMA


class LiveMarketDataUnavailable(RuntimeError):
    """Raised when legacy callers attempt to construct or connect a live feed."""


class BinanceWebSocket:
    """Fail-closed import compatibility only, never a functioning network adapter."""

    _message = (
        "Live market-data adapter is unavailable: trading_data is offline-only. "
        "Supply historical candles to the offline paper runner."
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise LiveMarketDataUnavailable(self._message)

    async def connect(self) -> None:
        raise LiveMarketDataUnavailable(self._message)


__all__ = [
    "Candle", "CandleBuffer", "SMA", "EMA", "RSI", "ATR",
    "BinanceWebSocket", "LiveMarketDataUnavailable",
]
