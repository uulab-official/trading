"""Offline stock-order journal backup, verification and new candidate restoration.

Stop all journal writers first. A successful replay verifies local consistency,
not broker evidence, account binding, economic reconciliation or order safety.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal

MAX_DB_BYTES = 100_000_000
MAX_MANIFEST_BYTES = 65_536
_FILES = {"journal.sqlite", "manifest.json"}
_SIDECARS = ("-wal", "-shm", "-journal")
_REFUSAL = "Offline stock-order backup refused; existing state was not replaced."
_FALSE_FLAGS = (
    "live_orders_enabled",
    "account_binding_verified",
    "economic_reconciliation_verified",
    "lifecycle_confirmation_verified",
    "safe_to_submit",
)
_MANIFEST_FIELDS = {
    "schema_version",
    "kind",
    "mode",
    "account_ref",
    "created_at_utc",
    "database_filename",
    "database_bytes",
    "database_sha256",
    "snapshot_sha256",
    "event_count",
    "execution_count",
    "order_count",
    "pending_order_count",
    "replay_verified",
    "candidate_restore_only",
    *_FALSE_FLAGS,
}


@contextmanager
def _static_refusal():
    try:
        yield
    except (OSError, ValueError, TypeError, ArithmeticError, RecursionError, sqlite3.Error):
        raise ValueError(_REFUSAL) from None


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _account(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", value) is None:
        raise ValueError(_REFUSAL)
    return value


def _path(value):
    """Resolve existing parents, retaining the final entry for no-follow checks."""
    path = Path(os.path.abspath(os.fspath(value)))
    try:
        return path.parent.resolve(strict=True) / path.name
    except RuntimeError:
        # Python 3.10-3.12 report parent symlink loops with a path-bearing
        # RuntimeError instead of OSError; keep API diagnostics static too.
        raise ValueError(_REFUSAL) from None


def _identity(info):
    return info.st_dev, info.st_ino


def _signature(info):
    return (*_identity(info), info.st_size, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


def _regular(info, limit):
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ValueError(_REFUSAL)


def _check_directory(path, descriptor):
    current = path.lstat()
    if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(os.fstat(descriptor)):
        raise ValueError(_REFUSAL)


@contextmanager
def _directory(path):
    before = path.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError(_REFUSAL)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if _identity(before) != _identity(os.fstat(descriptor)):
            raise ValueError(_REFUSAL)
        _check_directory(path, descriptor)
        yield descriptor
        _check_directory(path, descriptor)
    finally:
        os.close(descriptor)


def _entries(descriptor, expected):
    if set(os.listdir(descriptor)) != set(expected):
        raise ValueError(_REFUSAL)


def _read_file(descriptor, name, limit):
    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    _regular(info, limit)
    handle = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        dir_fd=descriptor,
    )
    with os.fdopen(handle, "rb") as source:
        opened = os.fstat(source.fileno())
        _regular(opened, limit)
        if _signature(opened) != _signature(info):
            raise ValueError(_REFUSAL)
        raw = source.read(limit + 1)
        if len(raw) != info.st_size or _signature(os.fstat(source.fileno())) != _signature(info):
            raise ValueError(_REFUSAL)
    if _signature(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != _signature(info):
        raise ValueError(_REFUSAL)
    return raw, _signature(info)


def _write_new_file(descriptor, name, raw):
    handle = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=descriptor,
    )
    with os.fdopen(handle, "wb") as target:
        os.fchmod(target.fileno(), 0o600)
        identity = _identity(os.fstat(target.fileno()))
        if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != identity:
            raise ValueError(_REFUSAL)
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
        if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != identity:
            raise ValueError(_REFUSAL)
        written = _signature(os.fstat(target.fileno()))
    # Failed writes retain evidence. Never unlink a possibly replaced pathname.
    return written


def _source_info(database):
    info = database.lstat()
    _regular(info, MAX_DB_BYTES)
    for suffix in _SIDECARS:
        try:
            sidecar = Path(str(database) + suffix).lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(sidecar.st_mode) or sidecar.st_size > MAX_DB_BYTES:
            raise ValueError(_REFUSAL)
    return info


def _temporary_file(directory, raw=b""):
    path = Path(directory) / "journal.sqlite"
    with _directory(Path(directory)) as descriptor:
        _write_new_file(descriptor, path.name, raw)
    return path


def _capture_database(database):
    """SQLite's native backup includes committed WAL pages, then exports DELETE."""
    with tempfile.TemporaryDirectory(prefix="stock-order-capture-") as temporary:
        path = _temporary_file(temporary)
        with (
            closing(
                sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5.0)
            ) as source,
            closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5.0)) as target,
        ):
            source.execute("PRAGMA query_only=ON")
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            pages = source.execute("PRAGMA page_count").fetchone()[0]
            if pages * page_size > MAX_DB_BYTES:
                raise ValueError(_REFUSAL)
            deadline = time.monotonic() + 10.0

            def progress(status, remaining, total):
                # SQLite's stable BUSY=5 / LOCKED=6 result codes are not
                # exported by every supported Python sqlite3 module.
                if (
                    total * page_size > MAX_DB_BYTES
                    or time.monotonic() > deadline
                    or status in (5, 6)
                ):
                    raise ValueError(_REFUSAL)

            source.backup(target, pages=128, progress=progress, sleep=0.01)
            if target.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise ValueError(_REFUSAL)
        with _directory(Path(temporary)) as descriptor:
            _entries(descriptor, {"journal.sqlite"})
            raw, _signature_value = _read_file(descriptor, path.name, MAX_DB_BYTES)
        return raw


def _replay_bytes(raw, account_ref):
    """Audit private WAL copies; never open the stored backup with SQLite."""
    with tempfile.TemporaryDirectory(prefix="stock-order-replay-") as temporary:
        path = _temporary_file(temporary, raw)
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5.0)
        ) as database:
            if database.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise ValueError(_REFUSAL)
            if database.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise ValueError(_REFUSAL)
        journal = StockOrderJournal.open_existing(path, account_ref=account_ref, read_only=True)
        return journal.snapshot()


def _summary(snapshot):
    return {
        "snapshot_sha256": _digest(_canonical(snapshot)),
        "event_count": snapshot["event_count"],
        "execution_count": snapshot["execution_count"],
        "order_count": len(snapshot["orders"]),
        "pending_order_count": len(snapshot["pending_client_order_ids"]),
    }


def _result(status, account_ref, snapshot):
    return {
        "status": status,
        "mode": "paper",
        "account_ref": account_ref,
        **{flag: False for flag in _FALSE_FLAGS},
        "replay_verified": True,
        "candidate_restore_only": True,
        "snapshot": snapshot,
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_REFUSAL)
        result[key] = value
    return result


def _integer(text):
    if len(text) > 10:
        raise ValueError(_REFUSAL)
    return int(text)


def _invalid_number(text):
    raise ValueError(_REFUSAL)


def _manifest(raw, account_ref):
    value = json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_int=_integer,
        parse_float=_invalid_number,
        parse_constant=_invalid_number,
    )
    if type(value) is not dict or set(value) != _MANIFEST_FIELDS:
        raise ValueError(_REFUSAL)
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "stock_order_journal_backup"
        or value["mode"] != "paper"
        or value["database_filename"] != "journal.sqlite"
        or value["account_ref"] != account_ref
        or any(value[flag] is not False for flag in _FALSE_FLAGS)
        or value["replay_verified"] is not True
        or value["candidate_restore_only"] is not True
    ):
        raise ValueError(_REFUSAL)
    for field in ("database_sha256", "snapshot_sha256"):
        if type(value[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[field]) is None:
            raise ValueError(_REFUSAL)
    for field in (
        "database_bytes",
        "event_count",
        "execution_count",
        "order_count",
        "pending_order_count",
    ):
        if type(value[field]) is not int or not 0 <= value[field] <= MAX_DB_BYTES:
            raise ValueError(_REFUSAL)
    if (
        value["database_bytes"] == 0
        or value["execution_count"] > value["event_count"]
        or value["order_count"] > value["event_count"]
        or value["pending_order_count"] > value["order_count"]
    ):
        raise ValueError(_REFUSAL)
    clock = value["created_at_utc"]
    if (
        type(clock) is not str
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00", clock
        )
        is None
        or datetime.fromisoformat(clock).isoformat(timespec="microseconds") != clock
    ):
        raise ValueError(_REFUSAL)
    return value


def _verified_backup(directory, account_ref):
    with _directory(directory) as descriptor:
        _entries(descriptor, _FILES)
        manifest_raw, manifest_signature = _read_file(
            descriptor, "manifest.json", MAX_MANIFEST_BYTES
        )
        manifest = _manifest(manifest_raw, account_ref)
        raw, database_signature = _read_file(descriptor, "journal.sqlite", MAX_DB_BYTES)
        if len(raw) != manifest["database_bytes"] or _digest(raw) != manifest["database_sha256"]:
            raise ValueError(_REFUSAL)
        snapshot = _replay_bytes(raw, account_ref)
        if any(manifest[key] != value for key, value in _summary(snapshot).items()):
            raise ValueError(_REFUSAL)
        _entries(descriptor, _FILES)
        for name, expected in (
            ("manifest.json", manifest_signature),
            ("journal.sqlite", database_signature),
        ):
            if _signature(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != expected:
                raise ValueError(_REFUSAL)
        return snapshot, raw


def create_backup(database, destination, *, account_ref):
    """Create a new private two-file snapshot after matching source and copy replay."""
    with _static_refusal():
        account_ref = _account(account_ref)
        database, destination = _path(database), _path(destination)
        if destination in {database, *(Path(str(database) + suffix) for suffix in _SIDECARS)}:
            raise ValueError(_REFUSAL)
        source_identity = _identity(_source_info(database))
        journal = StockOrderJournal.open_existing(database, account_ref=account_ref, read_only=True)
        before = journal.snapshot()
        raw = _capture_database(database)
        snapshot = _replay_bytes(raw, account_ref)
        if (
            snapshot != before
            or journal.snapshot() != before
            or _identity(_source_info(database)) != source_identity
        ):
            raise ValueError(_REFUSAL)
        # Capture and audit before publishing any files. mkdir is exclusive and
        # no failure cleanup removes user-visible paths or observed replacements.
        with _directory(destination.parent) as parent:
            os.mkdir(destination.name, 0o700, dir_fd=parent)
            _check_directory(destination.parent, parent)
            with _directory(destination) as descriptor:
                os.fchmod(descriptor, 0o700)
                _entries(descriptor, set())
                database_signature = _write_new_file(descriptor, "journal.sqlite", raw)
                _check_directory(destination, descriptor)
                _entries(descriptor, {"journal.sqlite"})
                if (
                    journal.snapshot() != before
                    or _identity(_source_info(database)) != source_identity
                ):
                    raise ValueError(_REFUSAL)
                manifest = {
                    "schema_version": 1,
                    "kind": "stock_order_journal_backup",
                    "mode": "paper",
                    "account_ref": account_ref,
                    **{flag: False for flag in _FALSE_FLAGS},
                    "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    "database_filename": "journal.sqlite",
                    "database_bytes": len(raw),
                    "database_sha256": _digest(raw),
                    **_summary(snapshot),
                    "replay_verified": True,
                    "candidate_restore_only": True,
                }
                manifest_signature = _write_new_file(
                    descriptor,
                    "manifest.json",
                    _canonical(manifest) + b"\n",
                )
                _entries(descriptor, _FILES)
                os.fsync(descriptor)
                verified, _raw = _verified_backup(destination, account_ref)
                if verified != before:
                    raise ValueError(_REFUSAL)
                _check_directory(destination.parent, parent)
                # Flush publication of the new directory entry as well as its
                # already-flushed contents before reporting verified success.
                os.fsync(parent)
                _entries(descriptor, _FILES)
                for name, expected in (
                    ("manifest.json", manifest_signature),
                    ("journal.sqlite", database_signature),
                ):
                    if (
                        _signature(os.stat(name, dir_fd=descriptor, follow_symlinks=False))
                        != expected
                    ):
                        raise ValueError(_REFUSAL)
        return _result("stock_order_backup_created", account_ref, verified)


def verify_backup(directory, *, account_ref):
    """Read exact files, strict manifest and full replay without changing backup contents."""
    with _static_refusal():
        account_ref = _account(account_ref)
        snapshot, _raw = _verified_backup(_path(directory), account_ref)
        return _result("stock_order_backup_verified", account_ref, snapshot)


def restore_candidate(directory, destination, *, account_ref):
    """Restore only a new paper candidate; never switch active paths or clear blocks."""
    with _static_refusal():
        account_ref = _account(account_ref)
        directory, destination = _path(directory), _path(destination)
        if directory == destination.parent or directory in destination.parent.parents:
            raise ValueError(_REFUSAL)
        if destination.name.endswith(_SIDECARS):
            raise ValueError(_REFUSAL)
        with _directory(destination.parent) as parent:
            if stat.S_IMODE(os.fstat(parent).st_mode) & 0o077:
                raise ValueError(_REFUSAL)
            snapshot, raw = _verified_backup(directory, account_ref)
            # Reuse the core's exclusive main/sidecar-preserving initializer.
            candidate = StockOrderJournal.create(destination, account_ref=account_ref)
            identity = _identity(destination.lstat())
            candidate._require_unused_sidecars()
            # A read-only WAL connection can leave fresh sidecars. Audit only
            # this newly owned empty candidate with the core's write-capable
            # transaction, without recording anything, so closing checkpoints
            # its own transient sidecars before the standalone bytes are copied.
            with candidate._transaction(write=True) as database:
                replay, _previous = candidate._replay(database)
                empty = replay.snapshot()
            if empty["event_count"] != 0 or empty["orders"]:
                raise ValueError(_REFUSAL)
            candidate._require_unused_sidecars()
            _check_directory(destination.parent, parent)
            handle = os.open(
                destination.name, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent
            )
            with os.fdopen(handle, "wb") as target:
                if _identity(os.fstat(target.fileno())) != identity:
                    raise ValueError(_REFUSAL)
                target.write(raw)
                target.truncate()
                target.flush()
                os.fsync(target.fileno())
                if _identity(destination.lstat()) != identity:
                    raise ValueError(_REFUSAL)
            candidate._require_unused_sidecars()
            with closing(
                sqlite3.connect(destination.as_uri() + "?mode=rw", uri=True, timeout=5.0)
            ) as database:
                if _identity(destination.lstat()) != identity:
                    raise ValueError(_REFUSAL)
                if database.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                    raise ValueError(_REFUSAL)
            restored = candidate.snapshot()
            if restored != snapshot or _identity(destination.lstat()) != identity:
                raise ValueError(_REFUSAL)
            os.fsync(parent)
            if _identity(destination.lstat()) != identity:
                raise ValueError(_REFUSAL)
            return _result("stock_order_restore_candidate_verified", account_ref, restored)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(_REFUSAL)


def main(argv=None):
    parser = _SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("paper",))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify", "restore"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--account-ref", required=True)
        if name == "create":
            command.add_argument("--database", required=True, type=Path)
        else:
            command.add_argument("--backup", required=True, type=Path)
        if name != "verify":
            command.add_argument("--destination", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == "create":
            result = create_backup(args.database, args.destination, account_ref=args.account_ref)
        elif args.command == "verify":
            result = verify_backup(args.backup, account_ref=args.account_ref)
        else:
            result = restore_candidate(args.backup, args.destination, account_ref=args.account_ref)
    except Exception:
        print(_REFUSAL, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
