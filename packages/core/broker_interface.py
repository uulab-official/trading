"""Broker-neutral contracts. No live trading implementation is enabled here."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol, Sequence


class Environment(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    currency: str
    tick_size: Decimal
    quantity_step: Decimal


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    instrument: Instrument
    side: Side
    quantity: Decimal
    limit_price: Decimal

    def __post_init__(self):
        if not self.client_order_id.strip():
            raise ValueError("client_order_id required")
        if self.quantity <= 0 or self.limit_price <= 0:
            raise ValueError("quantity and limit_price must be positive")
        if not self.quantity.is_finite() or not self.limit_price.is_finite():
            raise ValueError("order values must be finite")
        if self.instrument.tick_size <= 0 or self.instrument.quantity_step <= 0:
            raise ValueError("instrument precision must be positive")
        if self.quantity % self.instrument.quantity_step != 0:
            raise ValueError("quantity violates step")
        if self.limit_price % self.instrument.tick_size != 0:
            raise ValueError("price violates tick size")


@dataclass(frozen=True)
class OrderReceipt:
    client_order_id: str
    venue_order_id: str
    status: str


class Broker(Protocol):
    environment: Environment

    def list_instruments(self) -> Sequence[Instrument]: ...
    def submit_limit(self, request: OrderRequest) -> OrderReceipt: ...
    def cancel(self, venue_order_id: str) -> OrderReceipt: ...
    def get_order(self, venue_order_id: str) -> OrderReceipt: ...


class LiveTradingDisabled(RuntimeError):
    pass


def require_paper(environment: Environment) -> None:
    if environment != Environment.PAPER:
        raise LiveTradingDisabled("Live trading is disabled until explicit audited activation")
