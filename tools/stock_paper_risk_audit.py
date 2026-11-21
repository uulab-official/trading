"""Read-only risk metrics from independently audited offline STOCK PAPER history.

No broker, network, trading service, or file mutation. Uses only a complete
Toss-normalized 1m paper fixture and a verified existing SQLite strategy.
Drawdown is on hypothetical bar-close equity, not a real-account risk limit.
"""

import argparse
import json
import sqlite3
import sys
from decimal import Context, Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "packages/core", ROOT / "packages/data"):
    sys.path.insert(0, str(location))
from trading_core.stock_paper import StockPaperBook, _amount

from tools.stock_paper_backtest import run_backtest
from tools.stock_paper_forward import audit_forward


def stock_risk_audit(
    fixture, database, *, initial_cash, fee_pct="0.1", fast=5, slow=20, allocation_pct="20"
):
    """Reconstruct every paper bar-close equity from recorded strategy fills."""
    params = {
        "initial_cash": initial_cash,
        "fee_pct": fee_pct,
        "fast": fast,
        "slow": slow,
        "allocation_pct": allocation_pct,
    }
    verified = audit_forward(fixture, database, **params)
    outcome = run_backtest(fixture, **params)
    if verified["simulated_fills"] != len(outcome["simulated_fills"]):
        raise ValueError("Verified paper fills and strategy replay disagree")
    with localcontext(Context(prec=28)):
        book = StockPaperBook(outcome["quote_currency"], initial_cash, fee_pct)
        starting = _amount(initial_cash)
        peak = starting
        max_drawdown = Decimal("0")
        maximum_value = starting
        minimum_value = starting
        by_bar = {}
        for fill in outcome["simulated_fills"]:
            identity = fill["client_id"].split("-")
            if len(identity) != 3 or identity[0] != "ma" or not identity[1].isdigit():
                raise ValueError("Unexpected synthetic strategy fill identifier")
            index = int(identity[1])
            if index < slow + 1 or index >= len(fixture["candles"]):
                raise ValueError("Paper fill index outside complete bar history")
            if index in by_bar:
                raise ValueError("Multiple fills per completed bar are unsupported")
            if fill["price"] != fixture["candles"][index][1]:
                raise ValueError("Simulated next-open fill price differs from source")
            by_bar[index] = fill
        for index, candle in enumerate(fixture["candles"]):
            if index in by_bar:
                fill = by_bar[index]
                actual = book.execute(
                    fill["client_id"],
                    fill["symbol"],
                    fill["side"],
                    fill["shares"],
                    price=fill["price"],
                )
                if actual != fill:
                    raise ValueError("Paper fill state differs from strategy replay")
            equity = book.marked_equity({outcome["symbol"]: candle[4]})
            maximum_value = max(maximum_value, equity)
            minimum_value = min(minimum_value, equity)
            peak = max(peak, equity)
            if peak <= 0:
                raise ValueError("Invalid paper equity peak")
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        if len(book.fills) != verified["simulated_fills"]:
            raise ValueError("Paper fill reconstruction count mismatch")
        if book.marked_equity({outcome["symbol"]: fixture["candles"][-1][4]}) != Decimal(
            verified["marked_equity"]
        ):
            raise ValueError("Paper final equity differs from audited ledger")
        return {
            "status": "stock_paper_risk_metrics_audited",
            "mode": "paper",
            "live_orders_enabled": False,
            "symbol": outcome["symbol"],
            "quote_currency": outcome["quote_currency"],
            "bars": len(fixture["candles"]),
            "simulated_fills": len(book.fills),
            "initial_cash": str(starting),
            "final_equity": verified["marked_equity"],
            "max_drawdown_pct": str(max_drawdown),
            "peak_bar_close_equity": str(maximum_value),
            "min_bar_close_equity": str(minimum_value),
            "history_sha256": verified["history_sha256"],
            "warning": "Retrospective simulated bar-close risk only; no trading risk gate or profit forecast",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--initial-cash", required=True, type=Decimal)
    parser.add_argument("--fee-pct", default=Decimal("0.1"), type=Decimal)
    parser.add_argument("--fast", default=5, type=int)
    parser.add_argument("--slow", default=20, type=int)
    parser.add_argument("--allocation-pct", default=Decimal("20"), type=Decimal)
    args = parser.parse_args(argv)
    try:
        with args.fixture.open("rb") as file:
            content = file.read(10_000_001)
        if len(content) > 10_000_000:
            raise ValueError("Paper source file too large")
        result = stock_risk_audit(
            json.loads(content),
            args.database,
            initial_cash=args.initial_cash,
            fee_pct=args.fee_pct,
            fast=args.fast,
            slow=args.slow,
            allocation_pct=args.allocation_pct,
        )
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        ArithmeticError,
        sqlite3.Error,
        json.JSONDecodeError,
    ):
        print("Stock risk audit refused invalid paper state; no orders placed.", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
