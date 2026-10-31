"""Fail-closed pre-trade risk checks for paper order intents.

This pure module cannot authorize live trading. Account state must come from
a trusted reconciled ledger, not the frontend.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: Decimal
    max_gross_exposure: Decimal
    max_daily_loss: Decimal
    max_feed_age_seconds: int = 30


@dataclass(frozen=True)
class RiskSnapshot:
    cash: Decimal
    equity: Decimal
    gross_exposure: Decimal
    daily_pnl: Decimal
    feed_age_seconds: Optional[int]
    halted: bool = False


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


def check_buy(notional: Decimal, limits: RiskLimits, state: RiskSnapshot) -> RiskDecision:
    """Evaluate an unleveraged buy using already-reserved exposure/cash."""
    numbers = (notional, limits.max_order_notional, limits.max_gross_exposure,
               limits.max_daily_loss, state.cash, state.equity,
               state.gross_exposure, state.daily_pnl)
    if any(not value.is_finite() for value in numbers):
        return RiskDecision(False, "non_finite")
    if notional <= 0 or any(v <= 0 for v in
                           (limits.max_order_notional, limits.max_gross_exposure,
                            limits.max_daily_loss, state.equity)):
        return RiskDecision(False, "invalid_configuration")
    if state.cash < 0 or state.gross_exposure < 0 or limits.max_feed_age_seconds < 0:
        return RiskDecision(False, "invalid_state")
    if state.halted:
        return RiskDecision(False, "halted")
    if state.feed_age_seconds is None or state.feed_age_seconds < 0 or state.feed_age_seconds > limits.max_feed_age_seconds:
        return RiskDecision(False, "stale_feed")
    if state.daily_pnl <= -limits.max_daily_loss:
        return RiskDecision(False, "daily_loss_limit")
    if notional > limits.max_order_notional:
        return RiskDecision(False, "order_limit")
    if notional > state.cash:
        return RiskDecision(False, "insufficient_cash")
    if state.gross_exposure + notional > limits.max_gross_exposure:
        return RiskDecision(False, "exposure_limit")
    return RiskDecision(True, "approved_for_paper_only")
