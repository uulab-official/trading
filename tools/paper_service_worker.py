"""Internal fixed PAPER worker entrypoint; invoke only through the supervisor."""
import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for component in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("paper_service_worker_arguments_invalid")


def _parent(value):
    if len(value) > 10:
        raise ValueError("paper_service_worker_arguments_invalid")
    number = int(value)
    if not 1 <= number <= 2**31 - 1:
        raise ValueError("paper_service_worker_arguments_invalid")
    return number


def main(argv=None):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--role", required=True, choices=("capture", "stream"))
    parser.add_argument("--directory", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--parent-pid", required=True, type=_parent)
    try:
        args = parser.parse_args(argv)
    except (ValueError, TypeError):
        return 2
    try:
        from trading_bot.paper_service_worker import worker_main
        return worker_main(args.role, args.directory, args.attempt, args.parent_pid)
    except BaseException:
        # No traceback, private path or unframed document may reach stdout.
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
