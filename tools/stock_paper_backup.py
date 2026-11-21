"""Consistent offline STOCK PAPER SQLite backup, with separate audit fixture.

Never places orders or restores production state. A stopped operator creates
a new backup directory; SQLite backup() handles WAL snapshot consistency.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for package in ("core", "data"):
    sys.path.insert(0, str(ROOT / "packages" / package))
sys.path.insert(0, str(ROOT))
from tools.stock_paper_forward import audit_forward

MAX_FIXTURE_BYTES = 10_000_000
MAX_DB_BYTES = 100_000_000


def _bounded(path, limit):
    with Path(path).open("rb") as file:
        contents = file.read(limit + 1)
    if len(contents) > limit:
        raise ValueError("Paper backup file exceeds configured limit")
    return contents


def _digest(contents):
    return hashlib.sha256(contents).hexdigest()


def create_backup(fixture, database, destination, *, initial_cash,
                  fee_pct="0.1", fast=5, slow=20, allocation_pct="20"):
    fixture, database, destination = [
        Path(value).resolve() for value in (fixture, database, destination)
    ]
    if len({fixture, database, destination}) != 3 or destination.exists():
        raise ValueError("Backup paths must be unique and destination must be new")
    if not fixture.is_file() or not database.is_file():
        raise ValueError("Existing stock paper fixture and database required")
    fixture_bytes = _bounded(fixture, MAX_FIXTURE_BYTES)
    payload = json.loads(fixture_bytes)
    params = {
        "initial_cash": str(initial_cash), "fee_pct": str(fee_pct),
        "fast": fast, "slow": slow, "allocation_pct": str(allocation_pct)
    }
    # Refuse malformed economic state before writing any backup.
    before = audit_forward(payload, database, **params)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".stock-paper-backup-", dir=destination.parent))
    try:
        os.chmod(staging, 0o700)
        dbcopy = staging / "stock.sqlite"
        # Native SQLite backup API obtains an internally consistent snapshot
        # even when the source uses write-ahead logging.
        with closing(sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, timeout=5.0
        )) as source, closing(sqlite3.connect(str(dbcopy), timeout=5.0)) as target:
            source.backup(target)
            # backup() copies the source's WAL journal mode as well as its
            # pages. Export a standalone database before hashing/auditing:
            # the published backup must never depend on unmanifested WAL.
            if target.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("Could not finalize a standalone stock backup")
        if dbcopy.stat().st_size > MAX_DB_BYTES:
            raise ValueError("Stock paper database exceeds backup safety limit")
        os.chmod(dbcopy, 0o600)
        fixture_copy = staging / "candles.json"
        with fixture_copy.open("xb") as target:
            os.chmod(fixture_copy, 0o600)
            target.write(fixture_bytes)
            target.flush()
            os.fsync(target.fileno())
        db_bytes = _bounded(dbcopy, MAX_DB_BYTES)
        # Independently replay backup state before it becomes operator-visible.
        restored = audit_forward(payload, dbcopy, **params)
        if before != restored:
            raise ValueError("Stock paper backup changed economic replay state")
        # Refuse any replacement of source fixture during the backup window.
        if _bounded(fixture, MAX_FIXTURE_BYTES) != fixture_bytes:
            raise ValueError("Stock paper fixture changed during backup")
        manifest = {
            "schema_version": 1, "mode": "paper", "live_orders_enabled": False,
            "kind": "stock_paper_sqlite_backup",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "fixture_sha256": _digest(fixture_bytes),
            "database_sha256": _digest(db_bytes),
            "strategy_parameters": params,
            "audit_status": restored["status"],
            "bars": restored["bars"],
            "simulated_fills": restored["simulated_fills"],
            "market": restored["symbol"],
            "quote_currency": restored["quote_currency"],
            "manual_restore_only": True,
        }
        with (staging / "manifest.json").open("x", encoding="utf-8") as target:
            os.chmod(staging / "manifest.json", 0o600)
            json.dump(manifest, target, sort_keys=True, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        if destination.exists():
            raise FileExistsError("Destination already exists")
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def verify_backup(directory, *, deep=True):
    directory = Path(directory).resolve()
    # Reading only stock.sqlite would silently ignore committed changes in
    # a WAL. Such a directory is no longer the standalone snapshot we made.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = directory / ("stock.sqlite" + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError("Paper backup contains an unmanifested SQLite sidecar")
    manifest = json.loads(_bounded(directory / "manifest.json", 65536))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("kind") != "stock_paper_sqlite_backup"
            or manifest.get("mode") != "paper"
            or manifest.get("live_orders_enabled") is not False):
        raise ValueError("Invalid paper backup manifest")
    fixture_raw = _bounded(directory / "candles.json", MAX_FIXTURE_BYTES)
    db_raw = _bounded(directory / "stock.sqlite", MAX_DB_BYTES)
    if (_digest(fixture_raw) != manifest["fixture_sha256"]
            or _digest(db_raw) != manifest["database_sha256"]):
        raise ValueError("Paper backup hashes disagree with manifest")
    result = {
        "mode": "paper", "live_orders_enabled": False,
        "status": "stock_paper_backup_bytes_verified",
        "economic_replay_verified": False, "manual_restore_only": True,
    }
    if deep:
        # SQLite readonly WAL probing might create sidecars on some runtimes;
        # deep audit private temporary copies, never mutate backup directory.
        with tempfile.TemporaryDirectory(prefix="stock-paper-audit-") as folder:
            isolated = Path(folder)
            (isolated / "stock.sqlite").write_bytes(db_raw)
            payload = json.loads(fixture_raw)
            reviewed = audit_forward(
                payload, isolated / "stock.sqlite",
                **manifest["strategy_parameters"]
            )
        if (reviewed["bars"] != manifest["bars"]
                or reviewed["simulated_fills"] != manifest["simulated_fills"]
                or reviewed["symbol"] != manifest["market"]
                or reviewed["quote_currency"] != manifest["quote_currency"]):
            raise ValueError("Stock paper backup replay differs from manifest")
        result["economic_replay_verified"] = True
        result["status"] = "stock_paper_backup_deep_verified"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--fixture", required=True, type=Path)
    create.add_argument("--database", required=True, type=Path)
    create.add_argument("--destination", required=True, type=Path)
    create.add_argument("--initial-cash", required=True)
    create.add_argument("--fee-pct", default="0.1")
    create.add_argument("--fast", default=5, type=int)
    create.add_argument("--slow", default=20, type=int)
    create.add_argument("--allocation-pct", default="20")
    verify = commands.add_parser("verify")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--bytes-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(
                args.fixture, args.database, args.destination,
                initial_cash=args.initial_cash, fee_pct=args.fee_pct,
                fast=args.fast, slow=args.slow,
                allocation_pct=args.allocation_pct
            )
        else:
            result = verify_backup(args.directory, deep=not args.bytes_only)
    except (ValueError, OSError, TypeError, KeyError, ArithmeticError,
            json.JSONDecodeError, sqlite3.Error):
        print("Stock PAPER backup rejected. No original trading state was replaced.",
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
