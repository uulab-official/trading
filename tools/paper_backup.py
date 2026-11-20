"""Atomic, read-only backup of a validated offline paper archive/checkpoint pair.

No broker, credentials, network, restore, or live-trading function exists here.
Backups are immutable new directories, never overwritten.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
               ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(source))

from tools.paper_checkpoint_audit import audit
from trading_bot.paper_checkpoint import locked_checkpoint, CheckpointInUseError

MAX_ARCHIVE_BYTES = 32_000_000
MAX_CHECKPOINT_BYTES = 32_000_000


def _read_bounded(path, maximum):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Paper state file exceeds backup safety limit")
    return raw


def backup_pair(archive: Path, checkpoint: Path, destination: Path):
    """Snapshot a fully verified pair; refuse concurrent or mismatched state."""
    archive, checkpoint, destination = (Path(v).resolve() for v in (archive, checkpoint, destination))
    if len({archive, checkpoint, destination}) != 3:
        raise ValueError("Backup, archive and checkpoint paths must differ")
    if destination.exists():
        raise FileExistsError("Backup destination already exists: never overwrite")
    if not archive.is_file() or not checkpoint.is_file():
        raise ValueError("Backup requires both existing paper source files")
    # Verify economic/risk replay against the archive before acquiring both
    # locks; repeat source identity checks under locks to defeat TOCTOU.
    verified = audit(archive, checkpoint)
    with ExitStack() as locks:
        # Cycle captures archive first, then advances checkpoint. Lock in the
        # same order and release in reverse order.
        locked_archive = locks.enter_context(locked_checkpoint(archive))
        locked_checkpoint_file = locks.enter_context(locked_checkpoint(checkpoint))
        archive_raw = _read_bounded(locked_archive, MAX_ARCHIVE_BYTES)
        checkpoint_raw = _read_bounded(locked_checkpoint_file, MAX_CHECKPOINT_BYTES)
        if hashlib.sha256(archive_raw).hexdigest() != verified["fixture_sha256"]:
            raise ValueError("Archive changed after independent replay verification")
        if hashlib.sha256(checkpoint_raw).hexdigest() != verified["checkpoint_sha256"]:
            raise ValueError("Checkpoint changed after independent replay verification")
        fixture = json.loads(archive_raw)
        state = json.loads(checkpoint_raw)
        if fixture.get("mode") != "paper" or state.get("mode") != "paper":
            raise ValueError("Backup source must be explicit paper mode")
        if state["next_index"] != verified["processed_bars"]:
            raise ValueError("Checkpoint progress changed")
        manifest = {
            "schema_version": 1,
            "type": "offline_paper_pair_backup",
            "live_orders_enabled": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "market": verified["symbol"],
            "timeframe": verified["timeframe"],
            "processed_bars": verified["processed_bars"],
            "available_bars": verified["available_bars"],
            "archive_sha256": verified["fixture_sha256"],
            "checkpoint_sha256": verified["checkpoint_sha256"],
            "restoration": "manual_operator_review_only",
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".paper-backup-", dir=destination.parent))
        try:
            os.chmod(staging, 0o700)
            for name, raw in (("archive.json", archive_raw), ("checkpoint.json", checkpoint_raw)):
                with (staging / name).open("xb") as stream:
                    os.chmod(staging / name, 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            with (staging / "manifest.json").open("x", encoding="utf-8") as stream:
                os.chmod(staging / "manifest.json", 0o600)
                json.dump(manifest, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            # A nonempty backup directory becomes visible only after all files
            # were written. Destination is never replaced if already present.
            if destination.exists():
                raise FileExistsError("Backup destination already exists")
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return manifest


def verify_backup(directory: Path, *, deep=False):
    """Check bytes against the manifest; does not restore or trust profitability."""
    directory = Path(directory)
    manifest = json.loads(_read_bounded(directory / "manifest.json", 65536))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("type") != "offline_paper_pair_backup" or manifest.get("live_orders_enabled") is not False:
        raise ValueError("Invalid offline-paper backup manifest")
    verified_bytes = {}
    for name, key, maximum in (
        ("archive.json", "archive_sha256", MAX_ARCHIVE_BYTES),
        ("checkpoint.json", "checkpoint_sha256", MAX_CHECKPOINT_BYTES),
    ):
        content = _read_bounded(directory / name, maximum)
        if hashlib.sha256(content).hexdigest() != manifest.get(key):
            raise ValueError("Backup file differs from manifest")
        verified_bytes[name] = content
    if deep:
        # The auditor uses POSIX locks and creates persistent .lock files.
        # Replay private copies instead; never introduce new files inside an
        # immutable-by-policy backup directory while verifying it.
        with tempfile.TemporaryDirectory(prefix="paper-deep-audit-") as workspace:
            isolated = Path(workspace)
            for name, content in verified_bytes.items():
                (isolated / name).write_bytes(content)
            verification = audit(isolated / "archive.json", isolated / "checkpoint.json")
        if (verification["fixture_sha256"] != manifest["archive_sha256"]
                or verification["checkpoint_sha256"] != manifest["checkpoint_sha256"]
                or verification["processed_bars"] != manifest["processed_bars"]
                or verification["symbol"] != manifest["market"]):
            raise ValueError("Deep backup replay does not match signed-off manifest fields")
    return {"status": "backup_deep_verified" if deep else "backup_bytes_verified",
            "mode": "paper", "live_orders_enabled": False, "market": manifest["market"],
            "processed_bars": manifest["processed_bars"],
            "economic_replay_verified": bool(deep),
            "note": "Even deep replay does not validate real exchange orders or profitability"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--checkpoint", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--deep", action="store_true",
                        help="Independently replay backed-up paper economic state")
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            result = backup_pair(args.archive, args.checkpoint, args.destination)
        else:
            result = verify_backup(args.directory, deep=args.deep)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, CheckpointInUseError):
        print("Paper backup refused invalid or changing state; no source files modified.", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
