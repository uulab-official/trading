"""Create an offline KIS VTS limit-order plan; never contact a broker or send orders."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# This pure module needs only the standard library. Avoid importing the core
# package initializer, which loads unrelated exchange and notification clients.
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from kis_vts_order import build_order_plan


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes arbitrary supplied values and paths.
        raise ValueError("Invalid offline order-plan arguments")


def _share_count(value):
    # Bound text before int() on every supported Python version, including 3.10.
    if not 1 <= len(value) <= 10 or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ValueError("Quantity must be a bounded positive integer")
    return int(value)


def _write_new_plan(target, serialized):
    # Do not resolve the leaf: O_EXCL must reject even a dangling output symlink.
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            current = target.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                target.unlink()
        except OSError:
            pass
        raise


def main(argv=None):
    parser = _SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--client-order-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", required=True, choices=("buy", "sell"))
    parser.add_argument("--quantity", required=True, type=_share_count)
    parser.add_argument("--limit-price", required=True)
    parser.add_argument("--max-notional", required=True)
    parser.add_argument(
        "--output", required=True, type=Path, help="New JSON file in an existing directory"
    )
    try:
        args = parser.parse_args(argv)
        result = build_order_plan(
            client_order_id=args.client_order_id,
            symbol=args.symbol,
            side=args.side,
            quantity=args.quantity,
            limit_price=args.limit_price,
            max_notional=args.max_notional,
        )
        serialized = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        _write_new_plan(args.output, serialized)
    except Exception:
        # Never include supplied values, output paths, environment or exception text.
        print("Offline VTS order plan refused; no broker order sent.", file=sys.stderr)
        return 2
    print("Offline VTS order plan created; no broker order sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
