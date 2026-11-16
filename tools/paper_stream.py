"""Explicit continuous paper run, verified resume, passive check and prefix audit."""
import argparse
import asyncio
from contextlib import redirect_stdout
from decimal import Decimal, DecimalException
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
for component in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("paper_stream_arguments_invalid")


def _bounded_integer(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise ValueError("integer outside supported range")
        return number
    return parse


def _decimal_option(value):
    if len(value) > 128:
        raise ValueError("decimal is too long")
    try:
        number = Decimal(value)
    except DecimalException:
        raise ValueError("invalid decimal") from None
    if not number.is_finite():
        raise ValueError("nonfinite decimal")
    return number


def _arguments(argv):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False,
        help="Create a NEW paper account from a current closed-candle capture")
    run.add_argument("--capture", required=True)
    run.add_argument("--directory", required=True)
    run.add_argument("--poll-seconds", type=_bounded_integer(5, 300), default=5)
    run.add_argument("--max-polls", type=_bounded_integer(1, 10000))
    run.add_argument("--strategy", choices=("ma_cross", "rsi"), default=argparse.SUPPRESS)
    for name in ("initial-balance", "position-size-pct", "fee-pct", "slippage-pct",
                 "max-daily-loss-pct", "max-drawdown-pct"):
        run.add_argument("--" + name, type=_decimal_option, default=argparse.SUPPRESS)
    for name in ("fast-period", "slow-period", "rsi-period"):
        run.add_argument("--" + name, type=int, default=argparse.SUPPRESS)
    resume = commands.add_parser("resume", allow_abbrev=False,
        help="Verify the complete archived prefix, then resume frozen configuration")
    resume.add_argument("directory")
    resume.add_argument("--max-polls", type=_bounded_integer(1, 10000))
    check = commands.add_parser("check", allow_abbrev=False,
        help="Read a bounded account head; no strategy execution or repair")
    check.add_argument("directory")
    audit = commands.add_parser("audit", allow_abbrev=False,
        help="Verify a stopped account's full economic prefix without changing it")
    audit.add_argument("directory")
    return parser.parse_args(argv)


def _operate(args, module):
    received, previous = [], {}

    def on_signal(signum, frame):
        # Only record numeric intent. No Event, I/O, locks or persistence here.
        if not received:
            received.append(int(signum))

    def stop():
        return received[0] if received else 0
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, on_signal)
        if args.command == "check":
            report = module.stream_status(args.directory)
            if received:
                return 128 + received[0], {"error": "paper_stream_interrupted_check_stream"}
            return (0 if report["status"] == "current" else 1), report
        if args.command == "audit":
            result = asyncio.run(module.audit_stream(args.directory, stop=stop))
        else:
            options = dict(max_polls=args.max_polls, stop=stop)
            if args.command == "run":
                keys = ("strategy", "initial_balance", "position_size_pct", "fee_pct",
                        "slippage_pct", "max_daily_loss_pct", "max_drawdown_pct",
                        "fast_period", "slow_period", "rsi_period")
                options.update(capture=args.capture, poll_seconds=args.poll_seconds,
                    config_options={key: getattr(args, key) for key in keys if hasattr(args, key)})
            result = asyncio.run(module.run_stream(args.directory, **options))
        return result["exit_code"], dict(
            result, operation=args.command, mode="paper",
            read_only=args.command == "audit", live_enabled=False,
        )
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    try:
        args = _arguments(argv)
    except ValueError:
        print(json.dumps({"error": "paper_stream_arguments_invalid"}))
        return 2
    try:
        with redirect_stdout(sys.stderr):
            from trading_bot import paper_stream as module
            code, report = _operate(args, module)
    except ValueError:
        code, report = 2, {"error": "paper_stream_unavailable"}
    except OSError:
        code, report = 4, {"error": "paper_stream_storage_failed"}
    except (KeyboardInterrupt, asyncio.CancelledError):
        code, report = 130, {"error": "paper_stream_interrupted_check_stream"}
    except Exception:
        code, report = 4, {"error": "paper_stream_failed_check_stream"}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
