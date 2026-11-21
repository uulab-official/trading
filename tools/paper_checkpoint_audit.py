"""Read-only, fail-closed audit of a deterministic offline paper checkpoint.

This command never submits orders to a broker and never repairs or overwrites state.
It independently replays the processed prefix using the existing paper engine.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import fields
from decimal import localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("core", "data", "strategy"):
    sys.path.insert(0, str(ROOT / "packages" / package))
sys.path.insert(0, str(ROOT / "apps/server"))

from trading_bot.paper_checkpoint import CheckpointInUseError, locked_checkpoint
from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT


def audit(fixture_path: Path, checkpoint_path: Path):
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    if not isinstance(fixture, dict) or fixture.get("mode", "paper") != "paper":
        raise ValueError("Only local paper fixtures can be audited")
    candles = fixture.get("candles")
    if not isinstance(candles, list) or not candles:
        raise ValueError("Fixture requires a nonempty candles array")
    with locked_checkpoint(checkpoint_path) as path:
        if path is None or not path.is_file():
            raise ValueError("Existing checkpoint is required; audit never creates it")
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1 or state.get("mode") != "paper":
            raise ValueError("Unsupported checkpoint mode/version")
        identity = state.get("config")
        required = {field.name for field in fields(PaperRunConfig)}
        if not isinstance(identity, dict) or set(identity) != required:
            raise ValueError("Checkpoint configuration keys do not match")
        config = PaperRunConfig(**identity)
        if config.identity() != identity:
            raise ValueError("Checkpoint configuration is not canonical")
        if (
            fixture.get("symbol", "BTC/USDT") != config.symbol
            or fixture.get("timeframe", "1h") != config.timeframe
        ):
            raise ValueError("Fixture symbol/timeframe differs from checkpoint")
        cursor = state.get("next_index")
        if type(cursor) is not int or cursor < 1 or cursor > len(candles):
            raise ValueError("Checkpoint cursor is invalid")
        # Compare ALL saved economic and risk state, not just file hashes.
        with localcontext(PAPER_CALCULATION_CONTEXT):
            expected = asyncio.run(PaperRunner(config)._run(candles[:cursor], return_state=True))
        if state != expected:
            raise ValueError("Checkpoint diverges from independently replayed state")
        return {
            "status": "verified_offline_paper",
            "mode": "paper",
            "live_trading_approved": False,
            "processed_bars": cursor,
            "available_bars": len(candles),
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "checkpoint_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "trade_count": len(state["exchange"]["trades"]),
            "daily_loss_halted": state["daily_loss_halted"],
            "drawdown_halted": state["drawdown_halted"],
            "warning": "Synthetic/offline verification is not live trading validation",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit(args.fixture, args.checkpoint)
    except CheckpointInUseError:
        print("Audit blocked: checkpoint is in use", file=sys.stderr)
        return 3
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        print("Audit failed (no state changed): " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
