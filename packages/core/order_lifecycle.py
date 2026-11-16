"""Conservative order lifecycle state machine inspired by event-driven trading engines.

The model never treats an uncertain transport outcome as a rejected order.
It is a pure in-memory primitive, not a durable broker reconciliation service.
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class OrderStatus(str, Enum):
    CREATED = "created"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


_ALLOWED = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTING, OrderStatus.REJECTED},
    OrderStatus.SUBMITTING: {OrderStatus.UNKNOWN, OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED},
    OrderStatus.UNKNOWN: {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
    OrderStatus.ACCEPTED: {OrderStatus.UNKNOWN, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.UNKNOWN, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELLED},
    OrderStatus.CANCEL_PENDING: {OrderStatus.UNKNOWN, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED},
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
}


@dataclass(frozen=True)
class OrderState:
    client_order_id: str
    requested_quantity: Decimal
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal("0")

    def __post_init__(self):
        if not self.client_order_id or not self.requested_quantity.is_finite() or self.requested_quantity <= 0:
            raise ValueError("invalid order")
        if not self.filled_quantity.is_finite() or not (0 <= self.filled_quantity <= self.requested_quantity):
            raise ValueError("invalid fill quantity")

    def transition(self, status: OrderStatus, cumulative_filled: Decimal | None = None) -> "OrderState":
        if status not in _ALLOWED[self.status]:
            raise ValueError(f"invalid transition: {self.status} -> {status}")
        quantity = self.filled_quantity if cumulative_filled is None else cumulative_filled
        if not quantity.is_finite() or not self.filled_quantity <= quantity <= self.requested_quantity:
            raise ValueError("cumulative fill must be monotonic and within requested quantity")
        if status == OrderStatus.FILLED and quantity != self.requested_quantity:
            raise ValueError("filled status requires full quantity")
        if status == OrderStatus.PARTIALLY_FILLED and not (0 < quantity < self.requested_quantity):
            raise ValueError("partial fill requires nonzero incomplete quantity")
        if status in (OrderStatus.CREATED, OrderStatus.SUBMITTING, OrderStatus.ACCEPTED, OrderStatus.REJECTED) and quantity != 0:
            raise ValueError("status incompatible with filled quantity")
        return OrderState(self.client_order_id, self.requested_quantity, status, quantity)
