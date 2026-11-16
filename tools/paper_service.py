"""Configure, supervise, observe and generate an inactive local PAPER service."""
import argparse
import asyncio
from contextlib import contextmanager, redirect_stdout
import json
from pathlib import Path
import re
import signal
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
for component in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("paper_service_arguments_invalid")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("duplicate option")
        setattr(namespace, self.dest, values)


def _bounded_integer(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise ValueError("integer outside supported range")
        return number
    return parse


def _arguments(argv):
    # Even invalid/help-like requests receive the same one-JSON result contract.
    parser = _Parser(description=__doc__, allow_abbrev=False, add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", allow_abbrev=False, add_help=False)
    configure.add_argument("account")
    configure.add_argument("--directory", required=True, action=_Once)
    for name, low, high in (("startup-seconds", 10, 900),
                            ("heartbeat-seconds", 5, 120), ("stop-seconds", 1, 60)):
        configure.add_argument("--" + name, type=_bounded_integer(low, high), action=_Once)
    for command in ("run", "check", "unit"):
        child = commands.add_parser(command, allow_abbrev=False, add_help=False)
        child.add_argument("directory")
        if command == "unit":
            child.add_argument("--output", required=True, action=_Once)
    args = parser.parse_args(argv)
    if args.command == "configure":
        for name, default in (("startup_seconds", 180), ("heartbeat_seconds", 30),
                              ("stop_seconds", 15)):
            if getattr(args, name) is None:
                setattr(args, name, default)
    return args


def _failure(operation, code, error, *, reason=None):
    report = {
        "schema_version": 1,
        "source": "paper_service",
        "operation": operation,
        "mode": "paper",
        "live_enabled": False,
        "status": {2: "unavailable", 3: "refused", 4: "uncertain",
                   130: "interrupted", 143: "interrupted"}[code],
        "exit_code": code,
        "error": error,
        "service_id": None,
        "attempt_id": None,
        "attempt_number": None,
        "children": {},
        "output_may_exist": operation in ("configure", "run", "unit"),
        "boundaries": {
            "data_readiness": "not_assessed",
            "live_execution": False,
            "automatic_restart": False,
        },
    }
    if reason is not None:
        report["reason"] = (reason if type(reason) is str and
                            re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)
                            else "unavailable")
    return code, report


@contextmanager
def _signal_intent():
    received, previous = [], {}

    def on_signal(signum, frame):
        # Only record the first numeric intent. Cleanup belongs to normal flow.
        if not received:
            received.append(int(signum))

    def stop():
        return received[0] if received else 0

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, on_signal)
        yield stop
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _after_signal(code, report, stop):
    # The first signal must not erase native refusal, storage uncertainty or an
    # already confirmed interruption, including during final JSON serialization.
    signum = stop()
    if signum and code not in (3, 4, 130, 143):
        code = 128 + signum
        report = dict(report, exit_code=code, status="interrupted",
                      error="paper_service_interrupted")
    return code, report


def _operate(args, module, errors, stop):
    try:
        if stop():
            raise errors.ServiceInterrupted(stop())
        if args.command == "configure":
            report = asyncio.run(module.configure_service(
                args.account, args.directory, startup_seconds=args.startup_seconds,
                heartbeat_seconds=args.heartbeat_seconds,
                stop_seconds=args.stop_seconds, stop=stop))
        elif args.command == "run":
            report = module.run_service(args.directory, stop=stop)
        elif args.command == "check":
            report = module.probe_service(args.directory)
        else:
            report = module.render_systemd_unit(args.directory, args.output)
        if (not isinstance(report, dict) or type(report.get("exit_code")) is not int
                or report["exit_code"] not in (0, 1, 2, 3, 4, 130, 143)):
            raise RuntimeError("invalid service result")
    except errors.ServiceRefused as error:
        return _failure(args.command, 3, "paper_service_refused", reason=error.reason)
    except errors.ServiceUnavailable:
        return _failure(args.command, 2, "paper_service_unavailable")
    except (errors.ServiceStorageError, OSError):
        return _failure(args.command, 4, "paper_service_storage_failed")
    except errors.ServiceInterrupted as error:
        if type(error.signum) is not int or error.signum not in (2, 15):
            return _failure(args.command, 4, "paper_service_failed")
        return _failure(args.command, 128 + error.signum, "paper_service_interrupted")
    except (KeyboardInterrupt, asyncio.CancelledError):
        return _failure(args.command, 128 + (stop() or 2), "paper_service_interrupted")
    except Exception:
        return _failure(args.command, 4, "paper_service_failed")
    return _after_signal(report["exit_code"], report, stop)


def _main(argv, stop):
    args = None
    try:
        args = _arguments(argv)
    except ValueError:
        code, report = _failure("arguments", 2, "paper_service_arguments_invalid")
    else:
        try:
            with redirect_stdout(sys.stderr):
                from trading_bot import paper_service_state as errors
                if args.command == "unit":
                    from trading_bot import paper_service_unit as module
                else:
                    from trading_bot import paper_service as module
                code, report = _operate(args, module, errors, stop)
        except OSError:
            code, report = _failure(args.command, 4, "paper_service_storage_failed")
        except (KeyboardInterrupt, asyncio.CancelledError):
            code, report = _failure(args.command, 128 + (stop() or 2),
                                    "paper_service_interrupted")
        except Exception:
            code, report = _failure(args.command, 4, "paper_service_failed")
    code, report = _after_signal(code, report, stop)
    try:
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError, OverflowError, RecursionError):
        code, report = _failure(getattr(args, "command", "arguments"),
                                4, "paper_service_failed")
        encoded = json.dumps(report, sort_keys=True)
    final_code, final_report = _after_signal(code, report, stop)
    if final_code != code:
        code, report = final_code, final_report
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True)
    try:
        print(encoded, flush=True)
    except OSError:
        # Native cleanup and retained evidence precede this one public write.
        # An unusable sink cannot confirm delivery. Close its failed buffer so
        # interpreter finalization cannot replace the truthful uncertainty code.
        try:
            sys.stdout.close()
        except (OSError, ValueError):
            pass
        try:
            sys.stderr.write("paper_service_output_unconfirmed\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        return 4
    return code


def main(argv=None):
    # Keep numeric-only handlers through the one public result, then restore the
    # caller's handlers even when dispatch or result generation raises.
    with _signal_intent() as stop:
        return _main(argv, stop)


if __name__ == "__main__":
    raise SystemExit(main())
