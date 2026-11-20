"""Credential-free STOCK paper broker for KRW/USD cash markets.

This is an offline simulation, NOT a Toss Securities API connector. No sockets,
account tokens, real orders, margin, short selling or cross-currency conversion.
Caller supplies the execution price; no claim of exchange fill quality.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext, Context
from dataclasses import dataclass, field
import re

_CONTEXT = Context(prec=28)


def _amount(value, *, zero=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid decimal") from exc
    if not number.is_finite() or (number < 0 if zero else number <= 0):
        raise ValueError("Amount must be finite and nonnegative" if zero else
                         "Amount must be finite and positive")
    return number


@dataclass
class StockPaperBook:
    """One quote currency per book. Integer-share stock execution only."""

    quote_currency: str
    cash: Decimal
    fee_pct: Decimal = Decimal("0")
    holdings: dict = field(default_factory=dict)
    fills: list = field(default_factory=list)
    _requests: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.quote_currency not in ("KRW", "USD"):
            raise ValueError("Only KRW or USD isolated cash books are supported")
        self.cash = _amount(self.cash, zero=True)
        self.fee_pct = _amount(self.fee_pct, zero=True)
        if self.fee_pct >= 100:
            raise ValueError("Fee percentage must be below 100")

    def execute(self, client_id, symbol, side, shares, *, price):
        if not isinstance(client_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", client_id
        ):
            raise ValueError("Invalid paper client order ID")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.]{1,20}", symbol):
            raise ValueError("Invalid stock symbol")
        if side not in ("buy", "sell"):
            raise ValueError("Only stock cash buy/sell is supported")
        if type(shares) is not int or shares <= 0:
            raise ValueError("Only positive whole-share orders supported")
        price = _amount(price)
        request = (symbol, side, shares, price)
        if client_id in self._requests:
            if self._requests[client_id][:4] != request:
                raise ValueError("Paper client ID reused for different execution")
            return dict(self._requests[client_id][4])
        with localcontext(_CONTEXT):
            gross = Decimal(shares) * price
            fee = gross * self.fee_pct / 100
            held = self.holdings.get(symbol, 0)
            if side == "buy":
                if self.cash < gross + fee:
                    raise ValueError("Insufficient paper cash")
                next_cash = self.cash - gross - fee
                next_held = held + shares
            else:
                if shares > held:
                    raise ValueError("Cannot short-sell in cash paper book")
                next_cash = self.cash + gross - fee
                next_held = held - shares
            result = {
                "mode": "paper", "client_id": client_id, "symbol": symbol,
                "side": side, "shares": shares, "price": str(price),
                "gross": str(gross), "fee": str(fee),
                "quote_currency": self.quote_currency,
                "status": "simulated_fill",
            }
            # No mutations before all validation and affordability checks.
            self.cash = next_cash
            if next_held:
                self.holdings[symbol] = next_held
            else:
                self.holdings.pop(symbol, None)
            self.fills.append(dict(result))
            self._requests[client_id] = (*request, dict(result))
            return result

    def marked_equity(self, marks):
        """Fail if any open stock lacks a supplied positive quotation."""
        if not isinstance(marks, dict):
            raise ValueError("Explicit stock marks required")
        with localcontext(_CONTEXT):
            value = self.cash
            for symbol, shares in self.holdings.items():
                if symbol not in marks:
                    raise ValueError("Open stock has no supplied valuation mark")
                value += Decimal(shares) * _amount(marks[symbol])
            return value
