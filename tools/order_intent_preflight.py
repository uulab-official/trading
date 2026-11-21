"""Offline read-only SQLite order-intent preflight; never submits orders."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.order_intent_journal import OrderIntentJournal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path, help="Existing local SQLite journal")
    args = parser.parse_args(argv)
    try:
        report = OrderIntentJournal.open_existing_readonly(args.journal).audit_integrity()
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error):
        print(
            "Intent preflight refused invalid or unreadable state; no orders authorized.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    # An unresolved order blocks the next action even if the database
    # structural integrity checks passed.
    return 0 if report["unresolved_count"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
