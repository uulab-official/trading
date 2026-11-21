"""Causal stock MA cross backtest on validated paper-only 1m candles.

Never fetches market data or places broker orders. Signals use bars through
i-1 and simulated fills execute at the next available bar i open.
"""

import argparse
import json
import sys
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/core"))
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_core.stock_paper import StockPaperBook, _amount
from trading_data import Candle


def run_backtest(fixture, *, initial_cash, fee_pct="0.1", fast=5, slow=20, allocation_pct="20"):
    with localcontext(Context(prec=28)):
        return _run_backtest(
            fixture,
            initial_cash=initial_cash,
            fee_pct=fee_pct,
            fast=fast,
            slow=slow,
            allocation_pct=allocation_pct,
        )


def _run_backtest(fixture, *, initial_cash, fee_pct, fast, slow, allocation_pct):
    if not isinstance(fixture, dict) or fixture.get("mode") != "paper":
        raise ValueError("Only paper stock fixtures supported")
    if (
        fixture.get("interval") != "1m"
        or fixture.get("no_live_orders") is not True
        or fixture.get("timestamp_semantics") != "source_close_to_utc_open"
    ):
        raise ValueError("Fixture must originate from verified 1m Toss paper normalization")
    symbol = fixture.get("symbol")
    currency = fixture.get("quote_currency")
    if not isinstance(symbol, str) or currency not in ("KRW", "USD"):
        raise ValueError("Stock symbol and currency required")
    if type(fast) is not int or type(slow) is not int or not 1 < fast < slow <= 500:
        raise ValueError("Indicator periods must satisfy 1 < fast < slow <= 500")
    allocation = _amount(allocation_pct)
    if allocation > 100:
        raise ValueError("Allocation must not exceed 100 percent")
    bars = fixture.get("candles")
    if not isinstance(bars, list) or len(bars) < slow + 2:
        raise ValueError("Fixture does not contain enough completed 1m candles")
    history = []
    for raw in bars:
        if not isinstance(raw, list) or len(raw) != 6:
            raise ValueError("Invalid six-column stock OHLCV")
        candle = Candle(*raw)
        if candle.timestamp % 60000:
            raise ValueError("One-minute candle opening timestamp is misaligned")
        if history and candle.timestamp < history[-1].timestamp + 60000:
            raise ValueError("Duplicate, backward or overlapping stock candle")
        history.append(candle)
    book = StockPaperBook(currency, initial_cash, fee_pct)
    closing = [bar.close for bar in history]
    for index in range(slow + 1, len(history)):
        # Previous MA uses bars up to i-2, current MA uses up to i-1.
        # No current-bar high, low, close or volume influences a fill.
        previous_fast = sum(closing[index - fast - 1 : index - 1]) / fast
        previous_slow = sum(closing[index - slow - 1 : index - 1]) / slow
        current_fast = sum(closing[index - fast : index]) / fast
        current_slow = sum(closing[index - slow : index]) / slow
        if previous_fast <= previous_slow and current_fast > current_slow:
            price = history[index].open
            budget = book.cash * allocation / 100
            per_share = price * (1 + book.fee_pct / 100)
            shares = int((budget / per_share).to_integral_value(rounding=ROUND_DOWN))
            if shares:
                book.execute(f"ma-{index}-buy", symbol, "buy", shares, price=price)
        elif previous_fast >= previous_slow and current_fast < current_slow:
            shares = book.holdings.get(symbol, 0)
            if shares:
                book.execute(f"ma-{index}-sell", symbol, "sell", shares, price=history[index].open)
    equity = book.marked_equity({symbol: history[-1].close})
    original = _amount(initial_cash)
    return {
        "mode": "paper",
        "live_orders_enabled": False,
        "strategy": f"MA cross {fast}/{slow}",
        "symbol": symbol,
        "quote_currency": currency,
        "bars": len(history),
        "cash": str(book.cash),
        "holdings": dict(book.holdings),
        "marked_equity": str(equity),
        "total_return_pct": str((equity - original) / original * 100),
        "simulated_fills": book.fills,
        "assumptions": {
            "fee_pct": str(book.fee_pct),
            "allocation_pct": str(allocation),
            "signal": "previous completed close",
            "execution": "next available bar open",
            "market_impact": "not modeled",
            "provenance": "caller-supplied normalized paper fixture",
        },
        "warning": "Offline historical simulation is not a profitability forecast",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--initial-cash", type=Decimal, required=True)
    parser.add_argument("--fee-pct", type=Decimal, default=Decimal("0.1"))
    parser.add_argument("--allocation-pct", type=Decimal, default=Decimal("20"))
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        raw = args.fixture.read_bytes()
        if len(raw) > 10_000_000:
            raise ValueError("Fixture exceeds offline input limit")
        result = run_backtest(
            json.loads(raw),
            initial_cash=args.initial_cash,
            fee_pct=args.fee_pct,
            fast=args.fast,
            slow=args.slow,
            allocation_pct=args.allocation_pct,
        )
    except (ValueError, OSError, KeyError, TypeError, ArithmeticError):
        print("Paper stock backtest rejected unsafe or invalid input.", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
