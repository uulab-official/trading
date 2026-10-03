"""Fail-closed paper/live execution selection. No network calls occur here."""

from decimal import Decimal
from typing import Any, Mapping, Optional

from trading_core.paper_exchange import PaperExchange


def validate_execution_mode(config: Any, *, enable_live_trading: bool = False) -> None:
    """Live mode needs a deliberate runtime flag as well as valid configuration."""
    if config.mode not in ("paper", "live"):
        raise ValueError("Trading mode must be exactly 'paper' or 'live'")
    if config.mode == "paper":
        return
    if enable_live_trading is not True:
        raise ValueError("Live trading is disabled; the user must explicitly pass --enable-live-trading")
    exchange = config.exchange
    if not exchange.api_key or not exchange.api_secret:
        raise ValueError("Live mode requires user-configured exchange credentials")
    if exchange.sandbox or exchange.testnet:
        raise ValueError("Live mode requires sandbox=false and testnet=false")


def create_execution_exchange(
    config: Any,
    *,
    enable_live_trading: bool = False,
    initial_balance: Decimal = Decimal("10000"),
    initial_positions: Optional[Mapping[str, Decimal]] = None,
):
    """Paper never constructs a live adapter or consumes its credentials."""
    validate_execution_mode(config, enable_live_trading=enable_live_trading)
    if config.mode == "paper":
        return PaperExchange(initial_balance=initial_balance, initial_positions=initial_positions)
    # Import only after the live guard. Construction alone does not connect.
    from trading_core.exchange import CCXTExchange

    return CCXTExchange(
        exchange_id=config.exchange.id,
        api_key=config.exchange.api_key,
        api_secret=config.exchange.api_secret,
        sandbox=False,
    )
