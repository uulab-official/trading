"""Restart-safe, offline STOCK paper strategy replay.

A normalized complete-bar fixture is the ONLY input. The output is a local
SQLite simulated portfolio, not broker orders or a live market service.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/core", ROOT / "packages/data"):
    sys.path.insert(0, str(source))
from trading_core.stock_paper_ledger import StockPaperLedger
from tools.stock_paper_backtest import run_backtest


def replay_forward(fixture, database, *, initial_cash, fee_pct="0.1",
                   fast=5, slow=20, allocation_pct="20"):
    # Entire strategy is recalculated from complete bars. This is slower than
    # a streaming incremental indicator but avoids an unverified state cache.
    outcome = run_backtest(
        fixture, initial_cash=initial_cash, fee_pct=fee_pct,
        fast=fast, slow=slow, allocation_pct=allocation_pct,
    )
    book = StockPaperLedger(
        database, quote_currency=outcome["quote_currency"],
        initial_cash=initial_cash, fee_pct=fee_pct,
    )
    identity = {
        "mode": "paper", "strategy": "ma_cross",
        "symbol": outcome["symbol"], "currency": outcome["quote_currency"],
        "fast": fast, "slow": slow,
        "allocation_pct": str(Decimal(str(allocation_pct))),
        "initial_cash": str(Decimal(str(initial_cash))),
        "fee_pct": str(Decimal(str(fee_pct))),
        "interval": "1m",
        "timestamp_semantics": "source_close_to_utc_open",
        "provider": fixture.get("provider"),
        "source": fixture.get("source"),
        "adjusted": fixture.get("adjusted"),
    }
    latest_mark = fixture["candles"][-1][4]
    report = book.apply_strategy_replay(
        fixture, identity=identity,
        expected_fills=outcome["simulated_fills"],
        marks={outcome["symbol"]: latest_mark},
    )
    if Decimal(report["marked_equity"]) != Decimal(outcome["marked_equity"]):
        raise ValueError("Durable paper ledger differs from independent strategy replay")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--initial-cash", type=Decimal, required=True)
    parser.add_argument("--fee-pct", type=Decimal, default=Decimal("0.1"))
    parser.add_argument("--allocation-pct", type=Decimal, default=Decimal("20"))
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        with args.fixture.open("rb") as file:
            raw = file.read(10_000_001)
        if len(raw) > 10_000_000:
            raise ValueError("Stock fixture exceeds 10 MB")
        if args.fixture.resolve() == args.database.resolve():
            raise ValueError("Stock fixture cannot be used as the ledger database")
        data = json.loads(raw)
        outcome = replay_forward(
            data, args.database, initial_cash=args.initial_cash,
            fee_pct=args.fee_pct, fast=args.fast, slow=args.slow,
            allocation_pct=args.allocation_pct,
        )
    except (ValueError, TypeError, OSError, KeyError, ArithmeticError, sqlite3.Error):
        print("Stock PAPER replay refused invalid or conflicting historical state.", file=sys.stderr)
        return 2
    print(json.dumps(outcome, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
