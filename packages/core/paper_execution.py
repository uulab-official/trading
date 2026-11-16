"""Paper-only order admission with reserved buying power.

This module does not simulate fills, persist state, or contact exchanges.
Never use it for live order routing.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

try:
    from .broker_interface import OrderRequest, Side
    from .paper_broker import PaperBroker
    from .risk_gate import RiskDecision, RiskLimits, RiskSnapshot, check_buy
except ImportError:
    from broker_interface import OrderRequest, Side
    from paper_broker import PaperBroker
    from risk_gate import RiskDecision, RiskLimits, RiskSnapshot, check_buy


@dataclass(frozen=True)
class Admission:
    decision: RiskDecision
    venue_order_id: str | None = None


class PaperOrderAdmission:
    """Single-process in-memory reservation layer; not restart safe."""

    def __init__(self, limits: RiskLimits, broker: PaperBroker | None = None):
        self.limits = limits
        self.broker = broker if broker is not None else PaperBroker()
        self._accepted: dict[str, tuple[OrderRequest, Admission]] = {}
        self._reserved: dict[str, Decimal] = {}

    @property
    def reserved_notional(self) -> Decimal:
        return sum(self._reserved.values(), Decimal("0"))

    def submit(self, order: OrderRequest, state: RiskSnapshot) -> Admission:
        previous = self._accepted.get(order.client_order_id)
        if previous is not None:
            if previous[0] != order:
                raise ValueError("client_order_id reused with different order")
            receipt = self.broker.get_order(previous[1].venue_order_id)
            if receipt.status == "cancelled":
                return Admission(RiskDecision(False, "previously_cancelled"), receipt.venue_order_id)
            return previous[1]
        if order.side != Side.BUY:
            return Admission(RiskDecision(False, "sell_not_implemented"))
        notional = order.quantity * order.limit_price
        adjusted = replace(
            state,
            cash=state.cash - self.reserved_notional,
            gross_exposure=state.gross_exposure + self.reserved_notional,
        )
        decision = check_buy(notional, self.limits, adjusted)
        if not decision.allowed:
            return Admission(decision)
        receipt = self.broker.submit_limit(order)
        admission = Admission(decision, receipt.venue_order_id)
        self._accepted[order.client_order_id] = (order, admission)
        self._reserved[order.client_order_id] = notional
        return admission

    def cancel(self, client_order_id: str) -> None:
        previous = self._accepted.get(client_order_id)
        if previous is None:
            raise KeyError(client_order_id)
        receipt = self.broker.cancel(previous[1].venue_order_id)
        if receipt.status != "cancelled":
            raise RuntimeError("cancel not confirmed")
        self._reserved.pop(client_order_id, None)
