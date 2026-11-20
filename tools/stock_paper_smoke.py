"""Offline end-to-end STOCK PAPER smoke: strategy -> SQLite -> audit -> backup -> HTML.

Uses an explicitly SYNTHETIC fixture bundled in the repository, NO API
keys or network requests, and only private temporary files.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for path in ("core", "data"):
    sys.path.insert(0, str(ROOT / "packages" / path))
from tools.stock_paper_forward import replay_forward, audit_forward
from tools.stock_paper_backup import create_backup, verify_backup
from tools.stock_paper_report_html import generate


def run_smoke():
    fixture_path = ROOT / "config/examples/synthetic_toss_stock_paper_1m.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if (fixture.get("provider") != "synthetic_fixture"
            or fixture.get("source") != "bundled_demo_not_real_toss_prices"
            or fixture.get("mode") != "paper"):
        raise ValueError("Refusing non-synthetic stock demonstration input")
    opts = {
        "initial_cash": "1000000",
        "fee_pct": "0.1",
        "fast": 5,
        "slow": 20,
        "allocation_pct": "20",
    }
    with TemporaryDirectory(prefix="trading-offline-stock-smoke-") as directory:
        root = Path(directory)
        db = root / "strategy.sqlite"
        backup_path = root / "backup"
        first = replay_forward(fixture, db, **opts)
        second = replay_forward(fixture, db, **opts)
        if second["new_simulated_fills"] != 0 or second["appended_bars"] != 0:
            raise ValueError("Duplicate replay created extra stock fills")
        verified = audit_forward(fixture, db, **opts)
        saved = create_backup(fixture_path, db, backup_path, **opts)
        copy_audit = verify_backup(backup_path, deep=True)
        page = generate(fixture, db, **opts)
        if ("PAPER ONLY" not in page or "<script" in page.lower()
                or copy_audit["economic_replay_verified"] is not True):
            raise ValueError("Static paper dashboard or backup audit failed")
        if (first["total_simulated_fills"] != verified["simulated_fills"]
                or saved["simulated_fills"] != verified["simulated_fills"]):
            raise ValueError("Stock paper flow reported inconsistent fill counts")
        return {
            "status": "synthetic_stock_paper_smoke_verified",
            "mode": "paper",
            "live_orders_enabled": False,
            "fixture": "synthetic_not_real_market_data",
            "bars": verified["bars"],
            "simulated_fills": verified["simulated_fills"],
            "backup_verified": copy_audit["economic_replay_verified"],
            "static_html_verified": True,
            "duplicate_replay_prevented": True,
            "warning": "No actual Toss/KIS API calls, portfolio gains or broker orders",
        }


def main():
    try:
        result = run_smoke()
    except (ValueError, OSError, TypeError, KeyError, ArithmeticError):
        print("Synthetic paper smoke failed; no real orders attempted.", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
