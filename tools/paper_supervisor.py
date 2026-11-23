"""Fail-closed public-data PAPER supervisor, with atomic local health snapshots.

Default --once performs one supervised public-data paper cycle. --loop is opt-in;
it never creates real orders or uses private exchange endpoints.
"""

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trading_bot.paper_checkpoint import CheckpointInUseError, locked_checkpoint

from tools.upbit_paper_cycle import run_cycle


class PaperEmergencyStop(RuntimeError):
    """Local kill-switch marker denies any new public-data paper cycle."""


class PaperHaltLatched(RuntimeError):
    """Prior repeated failure must be acknowledged before running again."""


def _normalized_paths(archive, checkpoint, heartbeat, kill_switch):
    """Refuse data/STOP/lock aliases before creating any owner or output file."""
    archive, checkpoint, heartbeat = (
        Path(path).resolve() for path in (archive, checkpoint, heartbeat)
    )
    stop = (
        Path(kill_switch).resolve()
        if kill_switch is not None
        else checkpoint.with_name(checkpoint.name + ".STOP").resolve()
    )
    paths = (archive, checkpoint, heartbeat, stop)
    # These are the exact lock targets used by the supervisor, cycle, archive
    # capture and checkpoint writer. Resolve virtual targets too, as the lock
    # primitive does, before deriving their persistent .lock file names.
    targets = (
        archive,
        checkpoint,
        checkpoint.with_name("." + checkpoint.name + ".cycle").resolve(),
        checkpoint.with_name(checkpoint.name + ".supervisor").resolve(),
    )
    reserved = tuple(target.with_name("." + target.name + ".lock").resolve() for target in targets)
    names, inodes = set(), set()
    for path in paths + reserved:
        if path in names:
            raise ValueError("Supervisor data, STOP and lock paths must be distinct")
        names.add(path)
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in inodes:
            raise ValueError("Supervisor paths must not share a file inode")
        inodes.add(identity)
        # POSIX locks are named by canonical path; unlike symbolic links,
        # separate hardlink names cannot share a canonical owner lock.
        if path == checkpoint and metadata.st_nlink != 1:
            raise ValueError("Supervisor checkpoints must not have hardlink aliases")
    return paths


def _read_heartbeat(heartbeat):
    """Read bounded saved state without modifying evidence on malformed input."""
    try:
        with Path(heartbeat).open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return None
    if len(raw) > 65536:
        raise ValueError("Prior paper heartbeat exceeds size limit")
    saved = json.loads(raw)
    if (
        not isinstance(saved, dict)
        or saved.get("mode") != "paper"
        or saved.get("live_orders_enabled") is not False
    ):
        raise ValueError("Invalid prior paper heartbeat")
    status = saved.get("status")
    if status not in ("healthy", "risk_blocked", "degraded", "halted", "stopped"):
        raise ValueError("Invalid prior paper status")
    if "operator_stop" in saved and type(saved["operator_stop"]) is not bool:
        raise ValueError("Invalid prior operator stop marker")
    failures = saved.get("consecutive_failures", 1 if status == "degraded" else 0)
    if type(failures) is not int or not (0 <= failures <= 10):
        raise ValueError("Invalid prior consecutive failure count")
    if status == "degraded" and failures == 0:
        raise ValueError("Degraded heartbeat requires a failure count")
    if status in ("healthy", "risk_blocked") and failures:
        raise ValueError("Successful heartbeat cannot carry outstanding failures")
    return saved


def _check_prior_halt(heartbeat, max_failures):
    """Share durable failure history between one-shot, loop and stopped runs."""
    saved = _read_heartbeat(heartbeat)
    if saved is None:
        return 0
    if saved.get("operator_stop") is True:
        raise PaperEmergencyStop("Operator STOP still latched")
    if saved["status"] == "halted":
        raise PaperHaltLatched("Prior paper supervisor failure remains latched")
    failures = saved.get("consecutive_failures", 1 if saved["status"] == "degraded" else 0)
    if failures >= max_failures:
        _safe_heartbeat(heartbeat, dict(saved, status="halted"))
        raise PaperHaltLatched("Prior paper failure budget reached the configured threshold")
    return failures


def _safe_heartbeat(path, payload):
    """Atomically replace only our health file; keep it distinct from trading state."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".paper-health-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(payload, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run_supervised_once(
    *,
    archive,
    checkpoint,
    heartbeat,
    market,
    unit,
    count,
    balance,
    cycle=run_cycle,
    now=time.time,
    kill_switch=None,
    max_failures=3,
    _owner_locked=False,
):
    if type(max_failures) is not int or not (1 <= max_failures <= 10):
        raise ValueError("max_failures must be between 1 and 10")
    archive, checkpoint, heartbeat, kill_switch = _normalized_paths(
        archive, checkpoint, heartbeat, kill_switch
    )
    if not _owner_locked:
        # One-shot calls must be excluded while the daemon owns the portfolio,
        # including during its idle period; do not race heartbeat writes.
        owner = checkpoint.with_name(checkpoint.name + ".supervisor")
        with locked_checkpoint(owner):
            return run_supervised_once(
                archive=archive,
                checkpoint=checkpoint,
                heartbeat=heartbeat,
                market=market,
                unit=unit,
                count=count,
                balance=balance,
                cycle=cycle,
                now=now,
                kill_switch=kill_switch,
                max_failures=max_failures,
                _owner_locked=True,
            )
    observed = now()
    if not isinstance(observed, (int, float)) or not (0 < observed < 10**12):
        raise ValueError("Invalid supervisor clock")
    # A restart must not reset the failure threshold by replacing halted
    # state with a fresh healthy pulse. Preserve the old marker on refusal.
    previous_failures = _check_prior_halt(heartbeat, max_failures)
    base = {
        "mode": "paper",
        "live_orders_enabled": False,
        "market": market,
        "unit_minutes": unit,
        "last_attempt_utc": datetime.fromtimestamp(observed, tz=timezone.utc).isoformat(),
    }
    try:
        if kill_switch is not None and Path(kill_switch).exists():
            raise PaperEmergencyStop("Paper operator stop marker is present")
        result = cycle(
            Path(archive),
            Path(checkpoint),
            market=market,
            unit=unit,
            count=count,
            initial_balance=balance,
        )
        if (
            not isinstance(result, dict)
            or result.get("mode") != "paper"
            or result.get("live_orders_enabled") is not False
            or result.get("status") != "fixture_complete"
            or type(result.get("processed_bars")) is not int
            or type(result.get("appended_bars")) is not int
            or not 0 <= result["appended_bars"] <= result["processed_bars"]
            or type(result.get("daily_loss_halted")) is not bool
            or type(result.get("drawdown_halted")) is not bool
        ):
            raise ValueError("Paper cycle did not return a valid completed result")
        risk_blocked = result["daily_loss_halted"] or result["drawdown_halted"]
        heartbeat_state = dict(
            base,
            status="risk_blocked" if risk_blocked else "healthy",
            processed_bars=result["processed_bars"],
            appended_bars=result["appended_bars"],
            daily_loss_halted=result["daily_loss_halted"],
            drawdown_halted=result["drawdown_halted"],
            consecutive_failures=0,
        )
        _safe_heartbeat(heartbeat, heartbeat_state)
        return heartbeat_state
    except Exception as error:
        # Never persist broker credentials, payloads, URLs, or arbitrary error
        # strings to a report that might be shown in an operations dashboard.
        failure = dict(
            base,
            status="halted" if isinstance(error, PaperEmergencyStop) else "degraded",
            last_error_type=type(error).__name__,
        )
        if isinstance(error, PaperEmergencyStop):
            failure["operator_stop"] = True
            failure["consecutive_failures"] = previous_failures
        else:
            # Persist the failure budget in the SAME atomic error heartbeat.
            # A crash before the loop's exception handler cannot erase it.
            failure["consecutive_failures"] = previous_failures + 1
            if failure["consecutive_failures"] >= max_failures:
                failure["status"] = "halted"
        _safe_heartbeat(heartbeat, failure)
        raise


def run_loop(
    *,
    archive,
    checkpoint,
    heartbeat,
    market="KRW-BTC",
    unit=60,
    count=200,
    balance=Decimal("1000000"),
    interval_seconds=3600,
    max_failures=3,
    cycle=run_cycle,
    now=time.time,
    sleep=time.sleep,
    should_stop=lambda: False,
    kill_switch=None,
):
    if type(unit) is not int or unit not in (1, 3, 5, 10, 15, 30, 60, 240):
        raise ValueError("Unsupported paper candle minute unit")
    if type(interval_seconds) is not int or interval_seconds < unit * 60:
        raise ValueError("Paper polling interval must be at least a candle period")
    if type(max_failures) is not int or not (1 <= max_failures <= 10):
        raise ValueError("max_failures must be between 1 and 10")
    archive, checkpoint, heartbeat, kill_switch = _normalized_paths(
        archive, checkpoint, heartbeat, kill_switch
    )
    # Hold a distinct supervisor owner lock across every idle interval,
    # not merely while one cycle is running. This excludes a second daemon
    # which would otherwise race on heartbeat / retry history.
    owner = checkpoint.with_name(checkpoint.name + ".supervisor")
    with locked_checkpoint(owner):
        # Check latches BEFORE should_stop, so an immediate shutdown cannot
        # downgrade halted evidence or erase a stopped process's failure budget.
        try:
            consecutive = _check_prior_halt(heartbeat, max_failures)
        except PaperHaltLatched:
            return 2
        except PaperEmergencyStop:
            return 3
        while not should_stop():
            try:
                run_supervised_once(
                    archive=archive,
                    checkpoint=checkpoint,
                    heartbeat=heartbeat,
                    market=market,
                    unit=unit,
                    count=count,
                    balance=balance,
                    cycle=cycle,
                    now=now,
                    kill_switch=kill_switch,
                    max_failures=max_failures,
                    _owner_locked=True,
                )
                consecutive = 0
            except PaperHaltLatched:
                return 2
            except PaperEmergencyStop:
                return 3
            except Exception:
                # The cycle publishes its failure and threshold halt together.
                # If publication failed, do not retry against stale evidence.
                previous = _read_heartbeat(heartbeat)
                if previous is None or previous.get("consecutive_failures") != consecutive + 1:
                    raise
                if previous["status"] == "halted":
                    return 2
                if previous["status"] != "degraded":
                    raise
                consecutive = previous["consecutive_failures"]
            if should_stop():
                break
            # Keep owner lock during interruptible sleep too.
            sleep(interval_seconds)
        # Graceful shutdown must not leave a recent "healthy" pulse that
        # suggests the stopped process is still supervising the market.
        try:
            _check_prior_halt(heartbeat, max_failures)
        except PaperHaltLatched:
            return 2
        except PaperEmergencyStop:
            return 3
        previous = _read_heartbeat(heartbeat)
        if previous is not None:
            previous["status"] = "stopped"
            _safe_heartbeat(heartbeat, previous)
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
    parser.add_argument(
        "--kill-switch",
        type=Path,
        default=None,
        help="Local STOP marker file; existence prevents starting a new paper cycle",
    )
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
    kill_switch = args.kill_switch or paths[1].with_name(paths[1].name + ".STOP")
    if kill_switch.resolve() in set(paths):
        parser.error("Kill-switch path must differ from data and health files")
    try:
        if args.loop:
            stopped = threading.Event()

            def on_signal(signum, frame):
                stopped.set()

            signal.signal(signal.SIGTERM, on_signal)
            signal.signal(signal.SIGINT, on_signal)
            code = run_loop(
                archive=args.archive,
                checkpoint=args.checkpoint,
                heartbeat=args.heartbeat,
                market=args.market,
                unit=args.unit,
                count=args.count,
                balance=args.initial_balance,
                interval_seconds=interval,
                max_failures=args.max_failures,
                should_stop=stopped.is_set,
                sleep=stopped.wait,
                kill_switch=kill_switch,
            )
            return code
        status = run_supervised_once(
            archive=args.archive,
            checkpoint=args.checkpoint,
            heartbeat=args.heartbeat,
            market=args.market,
            unit=args.unit,
            count=args.count,
            balance=args.initial_balance,
            kill_switch=kill_switch,
            max_failures=args.max_failures,
        )
    except PaperEmergencyStop:
        print("Paper supervisor stopped by operator STOP marker.", file=sys.stderr)
        return 3
    except Exception as error:
        # A failed public API/lock/network operation may be a RuntimeError or
        # URLError. Always exit nonzero; never falsely report a healthy cycle.
        print(
            "Paper supervisor stopped safely (" + type(error).__name__ + "). Inspect local health.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(status, sort_keys=True))
    return 0 if status["status"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
