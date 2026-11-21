"""Offline stock-order events: create, record and read-only audit; no broker calls."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Directly import the pure module so the standalone command needs no packages.
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal

MAX_JSON_BYTES = 65536


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid offline journal arguments")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite JSON number")


def read_bounded_json(path):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds the size bound")
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_nonfinite)


def write_new_json(path, payload):
    """Exclusive private output; never remove an observed replacement on failure."""
    text = json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    target = Path(path)
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    identity = None
    try:
        identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            current = target.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("Reserved output path was replaced")
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
            current = target.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("Reserved output path was replaced")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    target.unlink()
            except OSError:
                pass
        raise


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("paper",))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "record", "status"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--database", required=True, type=Path)
        command.add_argument("--account-ref", required=True)
        if name == "record":
            command.add_argument("--event", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == "init":
            journal = StockOrderJournal.create(args.database, account_ref=args.account_ref)
            snapshot = journal.snapshot()
        elif args.command == "record":
            event = read_bounded_json(args.event)
            journal = StockOrderJournal.open_existing(args.database, account_ref=args.account_ref)
            snapshot = journal.record(event)
        else:
            journal = StockOrderJournal.open_existing(
                args.database,
                account_ref=args.account_ref,
                read_only=True,
            )
            snapshot = journal.snapshot()
    except Exception:
        print("Offline stock order journal refused; no broker order sent.", file=sys.stderr)
        return 2
    print(json.dumps(snapshot, sort_keys=True, allow_nan=False))
    return 3 if args.command == "status" and snapshot["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
