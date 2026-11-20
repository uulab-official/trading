"""Read-only, offline multi-market PAPER operations summary.

Combines independently audited crypto and stock *simulated* portfolios.
Never sums assets across brokers/currencies and never calls order endpoints.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
                 ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(location))
from tools.paper_checkpoint_audit import audit as audit_crypto
from tools.stock_paper_forward import audit_forward as audit_stock


MAX_SOURCE_BYTES = 10_000_000


def _fixture(path):
    with Path(path).open("rb") as input_file:
        raw = input_file.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Paper fixture exceeds configured read limit")
    return json.loads(raw)


def inspect_portfolios(*, crypto_fixture=None, crypto_checkpoint=None,
                       stock_fixture=None, stock_database=None,
                       stock_cash=None, stock_fee_pct="0.1",
                       fast=5, slow=20, allocation_pct="20"):
    if bool(crypto_fixture) != bool(crypto_checkpoint):
        raise ValueError("Crypto fixture and checkpoint must be paired")
    if bool(stock_fixture) != bool(stock_database) or (
        stock_fixture is not None and stock_cash is None
    ):
        raise ValueError("Stock fixture, database and initial cash must be paired")
    if not crypto_fixture and not stock_fixture:
        raise ValueError("At least one paper portfolio is required")
    payload = {
        "mode": "paper",
        "live_orders_enabled": False,
        "status": "audited_offline_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "portfolios": [],
        "cross_portfolio_total": None,
        "warning": "Separate paper portfolios; no real broker balances, FX or live orders",
    }
    if crypto_fixture:
        outcome = audit_crypto(Path(crypto_fixture), Path(crypto_checkpoint))
        if outcome["mode"] != "paper":
            raise ValueError("Cryptocurrency audit returned non-paper state")
        payload["portfolios"].append({
            "type": "crypto", "status": outcome["status"],
            "symbol": outcome["symbol"],
            "timeframe": outcome["timeframe"],
            "processed_bars": outcome["processed_bars"],
            "trade_count": outcome["trade_count"],
            "daily_loss_halted": outcome["daily_loss_halted"],
            "drawdown_halted": outcome["drawdown_halted"],
            "checkpoint_sha256": outcome["checkpoint_sha256"],
        })
    if stock_fixture:
        outcome = audit_stock(
            _fixture(stock_fixture), Path(stock_database),
            initial_cash=stock_cash, fee_pct=stock_fee_pct,
            fast=fast, slow=slow, allocation_pct=allocation_pct
        )
        if outcome["mode"] != "paper" or outcome["live_orders_enabled"] is not False:
            raise ValueError("Stock audit returned non-paper state")
        payload["portfolios"].append({
            "type": "stock", "status": outcome["status"],
            "symbol": outcome["symbol"], "quote_currency": outcome["quote_currency"],
            "processed_bars": outcome["bars"],
            "trade_count": outcome["simulated_fills"],
            "cash": outcome["cash"],
            "holdings": outcome["holdings"],
            "marked_equity": outcome["marked_equity"],
            "total_return_pct": outcome["total_return_pct"],
            "history_sha256": outcome["history_sha256"],
        })
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crypto-fixture", type=Path)
    parser.add_argument("--crypto-checkpoint", type=Path)
    parser.add_argument("--stock-fixture", type=Path)
    parser.add_argument("--stock-database", type=Path)
    parser.add_argument("--stock-initial-cash", type=Decimal)
    parser.add_argument("--stock-fee-pct", type=Decimal, default=Decimal("0.1"))
    parser.add_argument("--fast", type=int, default=5)
    parser.add_argument("--slow", type=int, default=20)
    parser.add_argument("--allocation-pct", type=Decimal, default=Decimal("20"))
    args = parser.parse_args(argv)
    try:
        result = inspect_portfolios(
            crypto_fixture=args.crypto_fixture,
            crypto_checkpoint=args.crypto_checkpoint,
            stock_fixture=args.stock_fixture,
            stock_database=args.stock_database,
            stock_cash=args.stock_initial_cash,
            stock_fee_pct=args.stock_fee_pct,
            fast=args.fast, slow=args.slow,
            allocation_pct=args.allocation_pct,
        )
    except (ValueError, TypeError, OSError, KeyError, ArithmeticError,
            sqlite3.Error, json.JSONDecodeError):
        print("Offline portfolio inspection refused invalid simulated state.",
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
