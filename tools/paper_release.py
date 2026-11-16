"""Build, externally verify and extract the fixed minimal PAPER source runtime."""

import argparse
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

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RESULT_BYTES = 16 * 1024
_STATUSES = {"build": "built", "check": "verified_archive",
             "extract": "extracted", "verify": "verified_tree"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("paper_release_arguments_invalid")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("duplicate option")
        setattr(namespace, self.dest, values)


def _digest(value):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError("paper_release_arguments_invalid")
    return value


def _arguments(argv):
    parser = _Parser(description=__doc__, add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", add_help=False, allow_abbrev=False)
    build.add_argument("--output", required=True, action=_Once)
    for name in ("check", "extract", "verify"):
        command = commands.add_parser(name, add_help=False, allow_abbrev=False)
        command.add_argument("directory" if name == "verify" else "archive")
        command.add_argument("--sha256", required=True, type=_digest, action=_Once)
        if name == "extract":
            command.add_argument("--directory", required=True, action=_Once)
    return parser.parse_args(argv)


@contextmanager
def _signal_intent():
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("paper_release_main_thread_required")
    received = []
    previous = {}

    def remember(signum, frame):
        if not received:
            received.append(int(signum))

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, remember)
        yield lambda: received[0] if received else 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _failure(operation, code, label):
    return code, {
        "schema_version": 1, "source": "paper_release", "operation": operation,
        "mode": "paper", "live_enabled": False,
        "status": "interrupted" if code in (130, 143) else "failed",
        "exit_code": code, "error": label,
        "boundaries": {"live_execution": False, "service_activation": False,
                       "data_readiness": "not_assessed", "interpreter_packaged": False},
    }


def _after_signal(code, report, stop):
    signum = stop()
    if signum and code not in (4, 130, 143):
        return _failure(report["operation"], 128 + signum, "paper_release_interrupted")
    return code, report


def _validate_result(report, operation):
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1 or report.get("source") != "paper_release"
            or report.get("operation") != operation or report.get("status") != _STATUSES[operation]
            or type(report.get("exit_code")) is not int or report["exit_code"] != 0):
        raise ValueError("invalid release result")
    for field in ("archive_sha256", "manifest_sha256", "execution_fingerprint"):
        _digest(report.get(field))
    for field in ("file_count", "payload_bytes"):
        if type(report.get(field)) is not int or report[field] < 0:
            raise ValueError("invalid release result")
    boundaries = report.get("boundaries")
    if (not isinstance(boundaries, dict) or boundaries.get("live_execution") is not False
            or boundaries.get("service_activation") is not False
            or boundaries.get("interpreter_packaged") is not False):
        raise ValueError("invalid release result")


def _operate(args, module, stop):
    try:
        if stop():
            raise module.ReleaseInterrupted(stop())
        if args.command == "build":
            report = module.build_release(args.output, stop=stop)
        elif args.command == "check":
            report = module.check_release(args.archive, expected_sha256=args.sha256, stop=stop)
        elif args.command == "extract":
            report = module.extract_release(args.archive, args.directory,
                expected_sha256=args.sha256, stop=stop)
        else:
            report = module.verify_release(args.directory, expected_sha256=args.sha256, stop=stop)
        _validate_result(report, args.command)
    except module.ReleaseUnavailable:
        return _failure(args.command, 2, "paper_release_unavailable")
    except (module.ReleaseStorageError, OSError):
        return _failure(args.command, 4, "paper_release_storage_unconfirmed")
    except module.ReleaseInterrupted as error:
        if type(error.signum) is not int or error.signum not in (2, 15):
            return _failure(args.command, 4, "paper_release_failed")
        return _failure(args.command, 128 + error.signum, "paper_release_interrupted")
    except KeyboardInterrupt:
        return _failure(args.command, 128 + (stop() or 2), "paper_release_interrupted")
    except Exception:
        return _failure(args.command, 4, "paper_release_failed")
    return _after_signal(0, report, stop)


def _encode(report):
    encoded = json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_RESULT_BYTES:
        raise ValueError("release result exceeds bound")
    return encoded


def _main(argv, stop):
    operation = "arguments"
    try:
        args = _arguments(argv)
    except ValueError:
        code, report = _failure(operation, 2, "paper_release_arguments_invalid")
    else:
        operation = args.command
        try:
            with redirect_stdout(sys.stderr):
                from trading_bot import paper_release
                code, report = _operate(args, paper_release, stop)
        except KeyboardInterrupt:
            code, report = _failure(operation, 128 + (stop() or 2), "paper_release_interrupted")
        except Exception:
            code, report = _failure(operation, 4, "paper_release_failed")
    code, report = _after_signal(code, report, stop)
    try:
        encoded = _encode(report)
    except (TypeError, ValueError, OverflowError, RecursionError):
        code, report = _failure(operation, 4, "paper_release_failed")
        encoded = _encode(report)
    final_code, final_report = _after_signal(code, report, stop)
    if final_code != code:
        code, report = final_code, final_report
        encoded = _encode(report)
    try:
        print(encoded, flush=True)
    except OSError:
        # The point-in-time filesystem operation has finished. Delivery failure
        # must not repeat it, erase its output or escape as a shutdown traceback.
        try:
            sys.stdout.close()
        except (OSError, ValueError):
            pass
        try:
            sys.stderr.write("paper_release_output_unconfirmed\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        return 4
    return code


def main(argv=None):
    with _signal_intent() as stop:
        return _main(argv, stop)


if __name__ == "__main__":
    raise SystemExit(main())
