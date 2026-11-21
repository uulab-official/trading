"""Offline end-to-end STOCK PAPER smoke: strategy -> SQLite -> audit -> backup -> HTML.

Uses explicitly SYNTHETIC fixtures bundled in the repository, NO API
keys or network requests. Optional output retains private local evidence.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
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


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid synthetic stock smoke arguments")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _write_private(path, text):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    identity = None
    try:
        identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    path.unlink()
            except OSError:
                pass
        raise


def _exercise(root, fixture, opts, retained):
    fixture_path = root / "candles.json"
    _write_private(fixture_path, _json(fixture))
    db = root / "strategy.sqlite"
    # Reserve the database privately before SQLite initializes it.
    _write_private(db, "")
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
    # Publish a standalone closed database, without transient WAL sidecars.
    # replay_forward re-enables its usual WAL mode if resumed later.
    with closing(sqlite3.connect(db, timeout=5)) as database:
        if database.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise ValueError("Could not finalize a standalone stock smoke ledger")
    _write_private(root / "audit.json", _json(verified))
    _write_private(root / "report.html", page)
    summary = {
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
        "quote_currency": verified["quote_currency"],
        "symbol": verified["symbol"],
        "settings": opts,
        "artifacts_retained": retained,
    }
    # This final artifact marks completion; earlier failures retain only partial evidence.
    _write_private(root / "smoke.json", _json(summary))
    return summary


def run_smoke(output_dir=None, *, currency="KRW"):
    if type(currency) is not str or currency not in ("KRW", "USD"):
        raise ValueError("Synthetic stock smoke currency must be KRW or USD")
    if currency == "KRW":
        name, source, symbol = ("synthetic_toss_stock_paper_1m.json",
                                "bundled_demo_not_real_toss_prices", "005930")
    else:
        name, source, symbol = ("synthetic_us_stock_paper_1m.json",
                                "bundled_demo_not_real_us_prices", "DEMO")
    fixture = json.loads((ROOT / "config/examples" / name).read_text(encoding="utf-8"))
    if (type(fixture) is not dict or fixture.get("provider") != "synthetic_fixture"
            or fixture.get("source") != source or fixture.get("mode") != "paper"
            or fixture.get("no_live_orders") is not True
            or fixture.get("quote_currency") != currency or fixture.get("symbol") != symbol
            or type(fixture.get("candles")) is not list or len(fixture["candles"]) != 55):
        raise ValueError("Refusing non-synthetic stock demonstration input")
    opts = {
        "initial_cash": "1000000" if currency == "KRW" else "10000",
        "fee_pct": "0.1",
        "fast": 5,
        "slow": 20,
        "allocation_pct": "20",
    }
    if output_dir is None:
        with TemporaryDirectory(prefix="trading-offline-stock-smoke-") as directory:
            return _exercise(Path(directory), fixture, opts, False)
    destination = Path(output_dir)
    # Do not resolve the final component: even dangling symlinks are collisions.
    # The trusted parent must already exist; never reuse an existing destination.
    os.mkdir(destination, 0o700)
    return _exercise(destination, fixture, opts, True)


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-dir", type=Path,
                        help="Optional new private directory in an existing trusted parent")
    parser.add_argument("--currency", choices=("KRW", "USD"), default="KRW",
                        help="Independent synthetic stock portfolio denomination")
    try:
        args = parser.parse_args(argv)
        result = run_smoke(args.output_dir, currency=args.currency)
    except Exception:
        print("Synthetic paper smoke failed; no real orders attempted. Retained evidence may be incomplete.",
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
