"""Fail-closed public-data PAPER supervisor, with atomic local health snapshots.

Default --once performs one supervised public-data paper cycle. --loop is opt-in;
it never creates real orders or uses private exchange endpoints.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.upbit_paper_cycle import run_cycle


class PaperEmergencyStop(RuntimeError):
    """Local kill-switch marker denies any new public-data paper cycle."""


def _safe_heartbeat(path, payload):
    """Atomically replace only our health file; keep it distinct from trading state."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".paper-health-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(payload, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_supervised_once(*, archive, checkpoint, heartbeat, market, unit, count,
                        balance, cycle=run_cycle, now=time.time, kill_switch=None):
    if len({Path(archive).resolve(), Path(checkpoint).resolve(),
            Path(heartbeat).resolve()}) != 3:
        raise ValueError("Archive, checkpoint, and heartbeat must be distinct paths")
    observed = now()
    if not isinstance(observed, (int, float)) or not (0 < observed < 10**12):
        raise ValueError("Invalid supervisor clock")
    base = {"mode": "paper", "live_orders_enabled": False, "market": market,
            "unit_minutes": unit, "last_attempt_utc": datetime.fromtimestamp(
                observed, tz=timezone.utc).isoformat()}
    try:
        if kill_switch is not None and Path(kill_switch).exists():
            raise PaperEmergencyStop("Paper operator stop marker is present")
        result = cycle(Path(archive), Path(checkpoint), market=market, unit=unit,
                       count=count, initial_balance=balance)
        if not isinstance(result, dict) or result.get("mode") != "paper" or result.get("live_orders_enabled") is not False:
            raise ValueError("Unexpected non-paper trading result")
        heartbeat_state = dict(base, status="healthy", processed_bars=result["processed_bars"],
                               appended_bars=result["appended_bars"],
                               daily_loss_halted=result["daily_loss_halted"],
                               drawdown_halted=result["drawdown_halted"])
        _safe_heartbeat(heartbeat, heartbeat_state)
        return heartbeat_state
    except Exception as error:
        # Never persist broker credentials, payloads, URLs, or arbitrary error
        # strings to a report that might be shown in an operations dashboard.
        failure = dict(base, status="degraded", last_error_type=type(error).__name__)
        _safe_heartbeat(heartbeat, failure)
        raise


def run_loop(*, archive, checkpoint, heartbeat, market="KRW-BTC", unit=60,
             count=200, balance=Decimal("1000000"), interval_seconds=3600,
             max_failures=3, cycle=run_cycle, now=time.time, sleep=time.sleep,
             should_stop=lambda: False, kill_switch=None):
    if type(unit) is not int or unit not in (1, 3, 5, 10, 15, 30, 60, 240):
        raise ValueError("Unsupported paper candle minute unit")
    if type(interval_seconds) is not int or interval_seconds < unit * 60:
        raise ValueError("Paper polling interval must be at least a candle period")
    if type(max_failures) is not int or not (1 <= max_failures <= 10):
        raise ValueError("max_failures must be between 1 and 10")
    consecutive = 0
    attempts = 0
    while not should_stop():
        try:
            run_supervised_once(archive=archive, checkpoint=checkpoint,
                                heartbeat=heartbeat, market=market, unit=unit,
                                count=count, balance=balance, cycle=cycle, now=now,
                                kill_switch=kill_switch)
            consecutive = 0
        except PaperEmergencyStop:
            previous = json.loads(Path(heartbeat).read_text(encoding="utf-8"))
            previous["status"] = "halted"
            previous["operator_stop"] = True
            _safe_heartbeat(heartbeat, previous)
            return 3
        except Exception:
            consecutive += 1
            if consecutive >= max_failures:
                # Fatal state remains visible until an operator deliberately
                # diagnoses and restarts. Never spin through a broken archive.
                previous = json.loads(Path(heartbeat).read_text(encoding="utf-8"))
                previous["status"] = "halted"
                previous["consecutive_failures"] = consecutive
                _safe_heartbeat(heartbeat, previous)
                return 2
        attempts += 1
        if should_stop():
            break
        # Sleep occurs between complete, separately checkpointed cycles.
        # The supervisor never retries a single broker submission.
        sleep(interval_seconds)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["paper"], required=True)
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--unit", type=int, default=60)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--heartbeat", type=Path, required=True)
    parser.add_argument("--kill-switch", type=Path, default=None,
                        help="Local STOP marker file; existence prevents starting a new paper cycle")
    parser.add_argument("--initial-balance", type=Decimal, default=Decimal("1000000"))
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--once", action="store_true")
    operation.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=None)
    parser.add_argument("--max-failures", type=int, default=3)
    args = parser.parse_args(argv)
    if args.initial_balance <= 0 or not args.initial_balance.is_finite():
        parser.error("Paper initial balance must be a finite positive amount")
    if args.count < 1 or args.count > 200:
        parser.error("Public snapshot count must be 1..200")
    if args.unit not in (1, 3, 5, 10, 15, 30, 60, 240):
        parser.error("Unsupported paper candle minute unit")
    interval = args.interval_seconds if args.interval_seconds is not None else args.unit * 60
    if interval < args.unit * 60:
        parser.error("Supervision cadence cannot exceed candle frequency")
    paths = (args.archive.resolve(), args.checkpoint.resolve(), args.heartbeat.resolve())
    if len(set(paths)) != 3:
        parser.error("Archive, checkpoint and heartbeat paths must all be distinct")
    kill_switch = args.kill_switch or args.checkpoint.with_name(args.checkpoint.name + ".STOP")
    if kill_switch.resolve() in set(paths):
        parser.error("Kill-switch path must differ from data and health files")
    try:
        if args.loop:
            stopped = threading.Event()
            def on_signal(signum, frame):
                stopped.set()
            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)
            code = run_loop(archive=args.archive, checkpoint=args.checkpoint,
                            heartbeat=args.heartbeat, market=args.market,
                            unit=args.unit, count=args.count, balance=args.initial_balance,
                            interval_seconds=interval, max_failures=args.max_failures,
                            should_stop=stopped.is_set, sleep=stopped.wait,
                            kill_switch=kill_switch)
            return code
        status = run_supervised_once(archive=args.archive, checkpoint=args.checkpoint,
                                     heartbeat=args.heartbeat, market=args.market,
                                     unit=args.unit, count=args.count, balance=args.initial_balance,
                                     kill_switch=kill_switch)
    except Exception as error:
        # A failed public API/lock/network operation may be a RuntimeError or
        # URLError. Always exit nonzero; never falsely report a healthy cycle.
        print("Paper supervisor stopped safely (" + type(error).__name__ + "). Inspect local health.", file=sys.stderr)
        return 2
    print(json.dumps(status, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
