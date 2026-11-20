"""Run targeted offline safety/replay checks with all socket/DNS access forbidden."""

from pathlib import Path
import argparse
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Script execution sets sys.path[0] to tools/, not the repository root.
# Server regression tests import tools.* directly; restore project-root imports.
sys.path.insert(0, str(ROOT))
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
        ("packages/core/tests", "test_order_intent_journal.py"),
        ("packages/core/tests", "test_sandbox_order_emulator.py"),
        ("packages/core/tests", "test_stock_paper.py"),
        ("packages/core/tests", "test_stock_paper_ledger.py"),
        ("packages/core/tests", "test_daily_loss_contract.py"),
        ("packages/core/tests", "test_entry_equity_guard.py"),
        ("packages/core/tests", "test_zero_mark_valuation.py"),
        ("packages/core/tests", "test_utc_daily_boundary.py"),
        ("packages/core/tests", "test_legacy_checkpoint.py"),
        ("packages/data/tests", "test_offline_data.py"),
        ("packages/data/tests", "test_toss_stock_candles.py"),
        ("packages/strategy/tests", "test_complete_candles.py"),
        ("packages/monitor/tests", "test_dashboard_marks.py"),
        ("apps/server/tests", "test_legacy_close_boundary.py"),
        ("apps/server/tests", "test_legacy_zero_mark.py"),
        ("apps/server/tests", "test_legacy_restart.py"),
        ("apps/server/tests", "test_legacy_lifecycle.py"),
        ("apps/server/tests", "test_paper_runner.py"),
        ("apps/server/tests", "test_paper_checkpoint_audit.py"),
        ("apps/server/tests", "test_paper_report_html.py"),
        ("apps/server/tests", "test_import_ohlcv_csv.py"),
        ("apps/server/tests", "test_upbit_public_paper_snapshot.py"),
        ("apps/server/tests", "test_upbit_paper_archive.py"),
        ("apps/server/tests", "test_upbit_paper_backfill.py"),
        ("apps/server/tests", "test_review_paper_backfill.py"),
        ("apps/server/tests", "test_upbit_paper_cycle.py"),
        ("apps/server/tests", "test_paper_supervisor.py"),
        ("apps/server/tests", "test_paper_healthcheck.py"),
        ("apps/server/tests", "test_paper_backup.py"),
        ("apps/server/tests", "test_stock_paper_cli.py"),
        ("apps/server/tests", "test_toss_stock_fixture.py"),
        ("apps/server/tests", "test_toss_readonly_snapshot.py"),
        ("apps/server/tests", "test_stock_paper_backtest.py"),
        ("apps/server/tests", "test_stock_paper_forward.py"),
        ("apps/server/tests", "test_stock_paper_report_html.py"),
        ("apps/server/tests", "test_stock_paper_backup.py"),
        ("apps/server/tests", "test_paper_portfolio_status.py"),
        ("apps/server/tests", "test_stock_paper_smoke.py"),
        ("apps/server/tests", "test_order_intent_preflight.py"),
        ("apps/server/tests", "test_paper_currency.py"),
        ("apps/server/tests", "test_paper_checkpoint.py"),
        ("apps/server/tests", "test_paper_cli.py"),
        ("apps/server/tests", "test_offline_trial_launcher.py"),
        ("apps/server/tests", "test_paper_shutdown.py"),
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
