"""Create, check and verify restoration of non-executing PAPER backup bundles."""
import argparse
import asyncio
from contextlib import redirect_stdout
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
        raise ValueError("paper_backup_arguments_invalid")


class _Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error("duplicate output option")
        setattr(namespace, self.dest, values)


def _arguments(argv):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", allow_abbrev=False,
        help="Verify a quiescent account and source into a NEW private backup bundle")
    create.add_argument("account")
    create.add_argument("--output", required=True, action=_Once)
    check = commands.add_parser("check", allow_abbrev=False,
        help="Verify the complete archived prefix without changing the backup")
    check.add_argument("directory")
    restore = commands.add_parser("verify-restore", allow_abbrev=False,
        help="Copy and verify a NEW non-executing recovery bundle; never start an account")
    restore.add_argument("directory")
    restore.add_argument("--output", required=True, action=_Once)
    return parser.parse_args(argv)


def _failure(args, message):
    return {"error": message, "output_may_exist": args.command != "check"}


def _operate(args, module, errors):
    received, previous = [], {}

    def on_signal(signum, frame):
        # Record intent only. Files, locks and publication belong to normal flow.
        if not received:
            received.append(int(signum))

    def stop():
        return received[0] if received else 0

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, on_signal)
        try:
            if received:
                raise errors.BackupInterrupted(received[0])
            if args.command == "create":
                report = asyncio.run(module.create_backup(args.account, args.output, stop=stop))
            elif args.command == "check":
                report = asyncio.run(module.check_backup(args.directory, stop=stop))
            else:
                report = asyncio.run(module.verify_restore(args.directory, args.output, stop=stop))
        except errors.BackupInterrupted as error:
            return 128 + error.signum, _failure(args, "paper_backup_interrupted")
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 128 + (stop() or 2), _failure(args, "paper_backup_interrupted")
        # A late stop can follow a confirmed publication. Do not erase artifacts
        # or report uninterrupted success. Storage exceptions never reach here.
        if received:
            return 128 + received[0], _failure(args, "paper_backup_interrupted")
        return report["exit_code"], report
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    try:
        args = _arguments(argv)
    except ValueError:
        print(json.dumps({"error": "paper_backup_arguments_invalid"}))
        return 2
    try:
        with redirect_stdout(sys.stderr):
            from trading_bot import paper_backup as module
            from trading_bot import paper_backup_io as errors
            code, report = _operate(args, module, errors)
    except ValueError:
        code, report = 2, _failure(args, "paper_backup_unavailable")
    except OSError:
        code, report = 4, _failure(args, "paper_backup_storage_failed")
    except (KeyboardInterrupt, asyncio.CancelledError):
        code, report = 130, _failure(args, "paper_backup_interrupted")
    except Exception:
        code, report = 4, _failure(args, "paper_backup_failed")
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
