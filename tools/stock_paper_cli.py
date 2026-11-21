"""Explicit operator-only STOCK PAPER command. No network or brokerage orders."""

import argparse
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.stock_paper_ledger import StockPaperLedger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--currency", choices=("KRW", "USD"), required=True)
    parser.add_argument("--initial-cash", type=Decimal, required=True)
    parser.add_argument("--fee-pct", type=Decimal, default=Decimal("0"))
    operation = parser.add_subparsers(dest="operation", required=True)
    trade = operation.add_parser("trade")
    trade.add_argument("--client-id", required=True)
    trade.add_argument("--symbol", required=True)
    trade.add_argument("--side", required=True, choices=("buy", "sell"))
    trade.add_argument("--shares", required=True, type=int)
    trade.add_argument("--price", required=True, type=Decimal)
    report = operation.add_parser("report")
    report.add_argument(
        "--mark", action="append", default=[], help="Required SYMBOL=PRICE for each nonzero holding"
    )
    args = parser.parse_args(argv)
    try:
        ledger = StockPaperLedger(
            args.database,
            quote_currency=args.currency,
            initial_cash=args.initial_cash,
            fee_pct=args.fee_pct,
        )
        if args.operation == "trade":
            outcome = ledger.execute(
                args.client_id, args.symbol, args.side, args.shares, price=args.price
            )
        else:
            marks = {}
            for item in args.mark:
                if "=" not in item:
                    raise ValueError("Mark must be SYMBOL=PRICE")
                symbol, price = item.split("=", 1)
                if not symbol or symbol in marks:
                    raise ValueError("Empty or duplicate marked symbol")
                marks[symbol] = price
            outcome = ledger.report(marks=marks)
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error):
        print("Stock paper operation refused. No brokerage order was placed.", file=sys.stderr)
        return 2
    print(json.dumps(outcome, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
