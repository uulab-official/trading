"""One-shot, fail-closed Upbit-public-data -> offline-paper strategy cycle.

Designed for an operator-owned local scheduler; there are no private API calls,
no live orders, and no unattended auto-promotion to a real-money mode.
"""
import argparse
import asyncio
from decimal import Decimal, DecimalException
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
               ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(source))

from tools.upbit_paper_archive import capture_once
from trading_bot.paper_checkpoint import CheckpointInUseError, locked_checkpoint
from trading_bot.paper_runner import PaperRunner, PaperRunConfig


def run_cycle(archive, checkpoint, *, market="KRW-BTC", unit=60, count=200,
              initial_balance=Decimal("1000000"), allow_gaps=False,
              fetcher=None, now_ms=None):
    if archive.resolve() == checkpoint.resolve():
        raise ValueError("Archive and ledger checkpoint must be different files")
    if not market.startswith("KRW-"):
        raise ValueError("Cycle accepts KRW public quotation markets only")
    # A separate cycle lock encloses capture AND replay, preventing cooperating
    # schedules from simultaneously advancing one portfolio.
    lock_target = checkpoint.with_name("." + checkpoint.name + ".cycle")
    with locked_checkpoint(lock_target):
        capture = capture_once(archive, market=market, unit=unit, count=count,
                               allow_gaps=allow_gaps, fetcher=fetcher, now_ms=now_ms)
        fixture = json.loads(archive.read_text(encoding="utf-8"))
        if fixture.get("mode") != "paper" or fixture.get("symbol") != market[4:] + "/KRW":
            raise ValueError("Paper fixture market identity changed")
        if fixture.get("timeframe") != f"{unit}m":
            raise ValueError("Paper fixture timeframe changed")
        config = PaperRunConfig(
            mode="paper", symbol=fixture["symbol"], timeframe=fixture["timeframe"],
            initial_balance=Decimal(str(initial_balance)),
        )
        result = asyncio.run(PaperRunner(config).run(fixture["candles"], checkpoint_path=checkpoint))
    return {
        "mode": "paper",
        "live_orders_enabled": False,
        "status": result["status"],
        "market": market,
        "archive_bars": capture["archived_bars"],
        "appended_bars": capture["appended_bars"],
        "processed_bars": result["bars_processed"],
        "simulated_fills": len(result["trades"]),
        "equity": result["equity"],
        "quote_currency": "KRW",
        "daily_loss_halted": result["daily_loss_halted"],
        "drawdown_halted": result["drawdown_halted"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--unit", type=int, default=60)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--initial-balance", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--allow-gaps", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_cycle(args.archive, args.checkpoint, market=args.market, unit=args.unit,
                           count=args.count, initial_balance=args.initial_balance,
                           allow_gaps=args.allow_gaps)
    except CheckpointInUseError:
        parser.exit(3, "Paper cycle is already active; no concurrent cycle permitted.\n")
    except (ValueError, OSError, KeyError, TypeError, DecimalException):
        parser.exit(2, "Paper cycle rejected input or state; no live orders attempted.\n")
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
