"""Run targeted offline safety/replay checks with all socket/DNS access forbidden."""

from pathlib import Path
import argparse
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for source in [ROOT / "packages/core", ROOT / "packages/data", ROOT / "packages/strategy", ROOT / "apps/server"]:
    sys.path.insert(0, str(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-backtest", action="store_true",
                        help="also run the existing offline backtest engine/broker checks (needs pandas/numpy)")
    args = parser.parse_args()
    checks = [
        ("packages/core/tests", "*execution_safety.py"),
        ("packages/core/tests", "test_paper_ledger.py"),
        ("packages/data/tests", "test_offline_data.py"),
        ("packages/strategy/tests", "test_complete_candles.py"),
        ("apps/server/tests", "test_paper_runner.py"),
        ("apps/server/tests", "test_paper_checkpoint.py"),
    ]
    if args.include_backtest:
        sys.path.insert(0, str(ROOT / "packages/backtest"))
        checks.append(("packages/backtest/tests", "test_*.py"))
    with patch("socket.socket.connect", side_effect=AssertionError("Offline checks forbid socket connections")), patch("socket.create_connection", side_effect=AssertionError("Offline checks forbid network clients")), patch("socket.getaddrinfo", side_effect=AssertionError("Offline checks forbid DNS")):
        suite = unittest.TestSuite()
        for directory, pattern in checks:
            suite.addTests(unittest.TestLoader().discover(str(ROOT / directory), pattern=pattern))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
