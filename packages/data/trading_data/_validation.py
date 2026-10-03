"""Shared numeric validation for pure, offline market data."""

from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN


CALCULATION_CONTEXT = Context(
    prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
    capitals=1, clamp=0, traps=[InvalidOperation, DivisionByZero, Overflow],
)


def positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def timestamp_milliseconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("timestamp must be a nonnegative integer in Unix milliseconds")
    return value


def finite_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not a boolean")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def positive_decimal(value: object, name: str) -> Decimal:
    result = finite_decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def nonnegative_decimal(value: object, name: str) -> Decimal:
    result = finite_decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result
