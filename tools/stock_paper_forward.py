"""Restart-safe, offline STOCK paper strategy replay.

A normalized complete-bar fixture is the ONLY input. The output is a local
SQLite simulated portfolio, not broker orders or a live market service.
"""
import argparse
import hashlib
from contextlib import closing
from decimal import Decimal
import json
import sqlite3
from pathlib import Path
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


def audit_forward(fixture, database, *, initial_cash, fee_pct="0.1",
                  fast=5, slow=20, allocation_pct="20"):
    """Verify persisted paper strategy state without modifying its database."""
    result = run_backtest(
        fixture, initial_cash=initial_cash, fee_pct=fee_pct, fast=fast,
        slow=slow, allocation_pct=allocation_pct,
    )
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("Existing paper strategy journal required")
    identity = {
        "mode": "paper", "strategy": "ma_cross",
        "symbol": result["symbol"], "currency": result["quote_currency"],
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
    expected_identity = json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False)
    expected_hash = hashlib.sha256(json.dumps(
        fixture["candles"], sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()
    # mode=ro avoids automatic database creation, DDL, and WAL changes.
    with closing(sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5
    )) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            meta = dict(db.execute("SELECT key,value FROM paper_meta"))
            expected_meta = {
                "schema_version": "1", "mode": "paper",
                "quote_currency": result["quote_currency"],
                "initial_cash": str(Decimal(str(initial_cash))),
                "fee_pct": str(Decimal(str(fee_pct))),
            }
            if meta != expected_meta:
                raise ValueError("Paper account configuration differs")
            saved = db.execute(
                "SELECT identity_json,bars,prefix_sha256 "
                "FROM paper_strategy_state WHERE id=1"
            ).fetchone()
            if saved is None or saved != (
                expected_identity, len(fixture["candles"]), expected_hash
            ):
                raise ValueError("Paper strategy cursor/history differs")
            book, count = StockPaperLedger._rebuild(
                db, result["quote_currency"], initial_cash, fee_pct
            )
            if book.fills != result["simulated_fills"]:
                raise ValueError("Paper recorded fills differ from independent strategy replay")
            equity = book.marked_equity({result["symbol"]: fixture["candles"][-1][4]})
            if equity != Decimal(result["marked_equity"]):
                raise ValueError("Paper marked equity differs")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {
        "status": "audited_stock_paper",
        "mode": "paper", "live_orders_enabled": False,
        "symbol": result["symbol"], "quote_currency": result["quote_currency"],
        "bars": len(fixture["candles"]), "simulated_fills": count,
        "cash": str(book.cash), "holdings": dict(book.holdings),
        "marked_equity": str(equity),
        "total_return_pct": result["total_return_pct"],
        "history_sha256": expected_hash,
        "warning": "Read-only local simulation audit; not authenticated brokerage balances",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--audit-only", action="store_true",
                        help="Read-only audit of an existing paper strategy ledger")
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
        run = audit_forward if args.audit_only else replay_forward
        outcome = run(
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
