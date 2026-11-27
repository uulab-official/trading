"""Verified, portable backup and isolated restore of a closed-bar paper session.

All data is local. The only executable invoked is the explicit paper-forward
audit-only CLI. Never submit orders, contact an exchange, or start a supervisor.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import binance_paper_forward_supervisor as supervisor
from tools import binance_public_archive as archive

MAX_FILE = 128 * 1024 * 1024
MAX_BUNDLE = 1024 * 1024 * 1024
BLOCK = 1024 * 1024
MAX_MEMBERS = 12  # archive, template, report, manifest, up to 8 strategy databases
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


def require(condition, reason):
    if not condition:
        raise supervisor.SupervisorError(reason)


def sha_file(path, limit):
    path = supervisor.regular(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(BLOCK):
            size += len(block)
            require(size <= limit, "backup_size_limit")
            digest.update(block)
    return {"size": size, "sha256": digest.hexdigest()}


def checked_copy(source, destination, limit, expected=None):
    """Read once into an exclusive file; check bounds and digest during copying."""
    source = supervisor.regular(source)
    with source.open("rb") as src:
        return copy_stream(src, destination, limit, expected)


def copy_stream(source, destination, limit, expected=None):
    digest = hashlib.sha256()
    size = 0
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        while block := source.read(BLOCK):
            size += len(block)
            require(size <= limit, "backup_size_limit")
            output.write(block)
            digest.update(block)
        output.flush()
        os.fsync(output.fileno())
    actual = {"size": size, "sha256": digest.hexdigest()}
    if expected is not None:
        require(actual == expected, "backup_member_digest_mismatch")
    return actual


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    raw = archive.canonical(value).encode("ascii")
    require(len(raw) <= supervisor.MAX_REPORT_BYTES, "backup_json_size_limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def strategy_files(template):
    ids = [item["id"] for item in template["strategies"]]
    require(
        1 <= len(ids) <= 8
        and all(isinstance(item, str) and IDENTIFIER.fullmatch(item) for item in ids)
        and len({item.lower() for item in ids}) == len(ids),
        "invalid_backup_strategy_identity",
    )
    return [f"accounts/forward-{item}.sqlite" for item in ids]


def snapshot_account(path, destination):
    """Native SQLite backup, including committed WAL content."""
    with archive.connection(path, readonly=True) as source:
        source.execute("BEGIN")
        # Establish a read snapshot before copying.
        source.execute("SELECT COUNT(*) FROM paper_meta").fetchone()
        archive.reserve_file(destination)
        with archive.connection(destination) as target:
            source.backup(target)
            require(
                target.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete",
                "backup_not_standalone",
            )
            require(
                target.execute("PRAGMA quick_check").fetchall()[0][0] == "ok",
                "backup_sqlite_check_failed",
            )
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())


def historical_envelope(session, report, archive_head=None):
    """Rebuild the reported fixed-start prefix, independent of wall-clock freshness.

    The full archive snapshot may be newer than the last successful paper report.
    Bind both heads, and refuse an archive fork or a missing prefix.
    """
    require(report.get("source") == archive.SOURCE, "invalid_backup_source")
    with archive.connection(session.archive_path, readonly=True) as db:
        db.execute("BEGIN")
        cfg, meta, last = archive.audit(db)
        require(last is not None, "empty_backup_archive")
        if archive_head is not None:
            require(meta["head"] == archive_head, "backup_archive_head_mismatch")
        count = report["input_bar_count"]
        require(
            type(count) is int and 2 <= count <= archive.MAX_FORWARD_BARS,
            "invalid_backup_bar_count",
        )
        step = archive.INTERVALS[cfg["interval"]]
        start = session.start_ms
        end = start + (count - 1) * step
        require(
            start >= cfg["start_ms"] and start % step == 0 and end <= last[0],
            "backup_prefix_outside_archive",
        )
        head = db.execute("SELECT hash FROM bars WHERE open_ms=?", (end,)).fetchone()
        require(
            head is not None and head[0] == report["archive_head_sha256"],
            "backup_archive_prefix_mismatch",
        )
        bars = [
            dict(row)
            for row in db.execute(
                "SELECT open_ms, open, high, low, close, volume FROM bars "
                "WHERE open_ms BETWEEN ? AND ? ORDER BY open_ms",
                (start, end),
            )
        ]
        require(len(bars) == count, "incomplete_backup_prefix")
        envelope = {
            "version": 1,
            "source": archive.SOURCE,
            "source_id": archive.digest("paper-forward-source-v1", cfg),
            "archive_head_sha256": head[0],
            "scenario": archive.scenario_from_template(cfg, bars, session.template),
            "trusted_checkpoints": [item["checkpoint"] for item in report["accounts"]],
        }
        supervisor.verify_report(envelope, report, None)
        return envelope, meta["head"]


def audit_report(session, report, *, archive_head=None, runner=None):
    runner = runner or supervisor.invoke_paper_binary
    envelope, actual_head = historical_envelope(session, report, archive_head)
    verified = runner(session, envelope, False, audit_only=True)
    supervisor.verify_report(envelope, verified, report)
    require(
        verified["replay"] == report["replay"]
        and [item["checkpoint"] for item in verified["accounts"]]
        == [item["checkpoint"] for item in report["accounts"]],
        "backup_economic_replay_mismatch",
    )
    return actual_head


def session_at(directory, binary, start_ms, max_failures):
    return supervisor.make_session(
        directory / "archive.sqlite",
        directory / "accounts",
        binary,
        directory / "template.json",
        start_ms,
        max_failures,
    )


def pack(directory, destination):
    """Exclusive publication of a complete, fsynced ZIP_STORED file."""
    fd, name = tempfile.mkstemp(prefix=".paper-bundle-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w+b") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_STORED) as output:
                for path in sorted(directory.rglob("*")):
                    if path.is_file():
                        # The staging directory is private and contains only our files.
                        output.write(path, path.relative_to(directory).as_posix())
            handle.flush()
            os.fsync(handle.fileno())
        evidence = sha_file(temporary, MAX_BUNDLE)
        os.link(temporary, destination)  # never replace an existing file
        temporary.unlink()
        fsync_directory(destination.parent)
        return evidence
    finally:
        if temporary.exists():
            temporary.unlink()


def capture_locked(session, destination, *, runner=None):
    """Caller holds the supervisor's owner lock; archive may still be collecting."""
    destination = archive.new_archive_path(destination)
    require(
        not destination.is_relative_to(session.directory)
        and destination
        not in (session.archive_path, session.state_path, session.lock_path, session.stop_path),
        "backup_path_overlap",
    )
    state = supervisor.read_state(session)
    report = state["last_report"]
    require(report is not None, "no_successful_paper_report")
    files = strategy_files(session.template)
    with tempfile.TemporaryDirectory(prefix="paper-bundle-staging-") as temp:
        staging = Path(temp)
        (staging / "accounts").mkdir(mode=0o700)
        archive.backup(session.archive_path, staging / "archive.sqlite")
        write_json(staging / "template.json", session.template)
        write_json(staging / "report.json", report)
        for name in files:
            snapshot_account(session.directory / Path(name).name, staging / name)
        copy = session_at(staging, session.binary, session.start_ms, session.max_failures)
        head = audit_report(copy, report, runner=runner)
        # SQLite read-only audit may need sidecars. Bundle only named, standalone
        # snapshots and refuse any unexpected staging files.
        members = ["archive.sqlite", "template.json", "report.json", *files]
        require(
            {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
            == set(members),
            "unexpected_backup_staging_file",
        )
        manifest = {
            "version": 1,
            "mode": "paper_recovery_bundle",
            "live_orders_enabled": False,
            "start_ms": session.start_ms,
            "max_failures": session.max_failures,
            "archive_head_sha256": head,
            "report_archive_head_sha256": report["archive_head_sha256"],
            "session_sha256": report["session_sha256"],
            "original_status": state["status"],
            "original_failures": state["consecutive_failures"],
            "last_success_ms": state["last_success_ms"],
            "files": {name: sha_file(staging / name, MAX_FILE) for name in members},
        }
        write_json(staging / "manifest.json", manifest)
        evidence = pack(staging, destination)
        return {
            "mode": "paper_recovery_bundle",
            "live_orders_enabled": False,
            "bundle": str(destination),
            "bundle_sha256": evidence["sha256"],
            "report_sha256": manifest["files"]["report.json"]["sha256"],
            "archive_head_sha256": head,
            "session_sha256": manifest["session_sha256"],
        }


def capture(session, destination, *, runner=None):
    with supervisor.owner(session):
        return capture_locked(session, destination, runner=runner)


def validate_manifest(manifest):
    require(
        isinstance(manifest, dict)
        and manifest.get("version") == 1
        and manifest.get("mode") == "paper_recovery_bundle"
        and manifest.get("live_orders_enabled") is False
        and type(manifest.get("start_ms")) is int
        and manifest["start_ms"] > 0
        and type(manifest.get("max_failures")) is int
        and 1 <= manifest["max_failures"] <= 10
        and manifest.get("original_status") in supervisor.STATUSES
        and type(manifest.get("original_failures")) is int
        and 0 <= manifest["original_failures"] <= manifest["max_failures"]
        and type(manifest.get("last_success_ms")) is int
        and manifest["last_success_ms"] > 0,
        "invalid_bundle_manifest",
    )
    for key in ("archive_head_sha256", "report_archive_head_sha256", "session_sha256"):
        require(
            isinstance(manifest.get(key), str) and supervisor.DIGEST.fullmatch(manifest[key]),
            "invalid_manifest_digest",
        )
    members = manifest["files"]
    require(
        isinstance(members, dict)
        and 4 <= len(members) < MAX_MEMBERS
        and {"archive.sqlite", "template.json", "report.json"}.issubset(members),
        "invalid_bundle_members",
    )
    for name, evidence in members.items():
        require(
            name in ("archive.sqlite", "template.json", "report.json")
            or (
                name.startswith("accounts/forward-")
                and name.endswith(".sqlite")
                and IDENTIFIER.fullmatch(name[len("accounts/forward-") : -len(".sqlite")])
            ),
            "unsafe_bundle_member",
        )
        require(
            isinstance(evidence, dict)
            and set(evidence) == {"size", "sha256"}
            and type(evidence["size"]) is int
            and 0 < evidence["size"] <= MAX_FILE
            and isinstance(evidence["sha256"], str)
            and supervisor.DIGEST.fullmatch(evidence["sha256"]),
            "invalid_bundle_member_evidence",
        )
    return members


def unpack(bundle, expected_sha256, staging):
    """Authenticate the bytes against an external digest before ZIP parsing.

    Use a private copy so the input cannot change between validation and reading.
    Only explicitly named, stored regular members are accepted; never extractall.
    """
    require(
        isinstance(expected_sha256, str) and supervisor.DIGEST.fullmatch(expected_sha256),
        "invalid_expected_bundle_digest",
    )
    local = staging / "source.zip"
    evidence = checked_copy(bundle, local, MAX_BUNDLE)
    require(evidence["sha256"] == expected_sha256, "external_bundle_digest_mismatch")
    data = staging / "data"
    data.mkdir(mode=0o700)
    (data / "accounts").mkdir(mode=0o700)
    with zipfile.ZipFile(local) as source:
        entries = source.infolist()
        names = [item.filename for item in entries]
        require(
            5 <= len(names) <= MAX_MEMBERS
            and len(names) == len(set(names))
            and "manifest.json" in names,
            "invalid_zip_members",
        )
        for item in entries:
            require(
                not item.is_dir()
                and stat.S_ISREG(item.external_attr >> 16)
                and item.compress_type == zipfile.ZIP_STORED
                and 0 < item.file_size <= MAX_FILE
                and item.compress_size == item.file_size,
                "invalid_zip_member_type",
            )
        require(sum(item.file_size for item in entries) <= MAX_BUNDLE, "backup_size_limit")
        with source.open("manifest.json") as handle:
            copy_stream(handle, data / "manifest.json", 1024 * 1024)
        manifest = supervisor.load_json(data / "manifest.json")
        members = validate_manifest(manifest)
        require(set(names) == set(members) | {"manifest.json"}, "unexpected_zip_members")
        for name, member in members.items():
            require(source.getinfo(name).file_size == member["size"], "backup_member_size_mismatch")
            with source.open(name) as handle:
                copy_stream(handle, data / name, MAX_FILE, member)
    template = supervisor.load_json(data / "template.json")
    require(
        set(members) == {"archive.sqlite", "template.json", "report.json", *strategy_files(template)},
        "bundle_strategy_set_mismatch",
    )
    return data, manifest


def verify_staged(data, manifest, binary, *, runner=None):
    session = session_at(data, binary, manifest["start_ms"], manifest["max_failures"])
    report = supervisor.load_json(data / "report.json")
    require(
        report["session_sha256"] == manifest["session_sha256"]
        and report["archive_head_sha256"] == manifest["report_archive_head_sha256"],
        "manifest_report_mismatch",
    )
    audit_report(session, report, archive_head=manifest["archive_head_sha256"], runner=runner)
    return session, report


def verify(bundle, expected_sha256, binary, *, runner=None):
    with tempfile.TemporaryDirectory(prefix="paper-bundle-verify-") as temp:
        data, manifest = unpack(bundle, expected_sha256, Path(temp))
        verify_staged(data, manifest, binary, runner=runner)
        return {
            "mode": "paper_recovery_verified",
            "live_orders_enabled": False,
            "bundle_sha256": expected_sha256,
            "session_sha256": manifest["session_sha256"],
            "report_sha256": manifest["files"]["report.json"]["sha256"],
            "archive_head_sha256": manifest["archive_head_sha256"],
        }


def restore(bundle, expected_sha256, binary, destination, *, runner=None, clock=archive.now_ms):
    """Verify first; create a NEW stopped candidate. Never activate either process."""
    destination = Path(destination)
    destination = destination.parent.resolve(strict=True) / destination.name
    require(not os.path.lexists(destination), "restore_destination_exists")
    with tempfile.TemporaryDirectory(prefix="paper-bundle-restore-") as temp:
        data, manifest = unpack(bundle, expected_sha256, Path(temp))
        _, report = verify_staged(data, manifest, binary, runner=runner)
        destination.mkdir(mode=0o700)
        (destination / "accounts").mkdir(mode=0o700)
        # Install STOP markers before copying accounts. A partial candidate must
        # never look ready to run. REVIEW.json is written only after full audit.
        for marker in ("archive.sqlite.STOP", "accounts.forward.STOP"):
            archive.reserve_file(destination / marker)
        for name, evidence in manifest["files"].items():
            checked_copy(data / name, destination / name, MAX_FILE, evidence)
        restored = session_at(destination, binary, manifest["start_ms"], manifest["max_failures"])
        audit_report(restored, report, archive_head=manifest["archive_head_sha256"], runner=runner)
        state = supervisor.new_state(restored)
        supervisor.publish(
            restored,
            state,
            "halted",
            "restored_review_required",
            clock,
            consecutive_failures=manifest["original_failures"],
            last_success_ms=manifest["last_success_ms"],
            last_report=report,
        )
        review = {
            "mode": "paper_restore_candidate",
            "live_orders_enabled": False,
            "bundle_sha256": expected_sha256,
            "session_sha256": manifest["session_sha256"],
            "report_sha256": manifest["files"]["report.json"]["sha256"],
            "archive_head_sha256": manifest["archive_head_sha256"],
            "operator_review_required": True,
        }
        write_json(destination / "REVIEW.json", review)
        fsync_directory(destination / "accounts")
        fsync_directory(destination)
        fsync_directory(destination.parent)
        return review


def review_resume(session, expected_report_sha256, *, runner=None, clock=archive.now_ms):
    """Clear only the local supervisor halt after explicit independently pinned review.

    Keep all economic risk latches, last successful report and trusted floors.
    This is not a process start, and does not remove either STOP marker.
    """
    require(
        isinstance(expected_report_sha256, str)
        and supervisor.DIGEST.fullmatch(expected_report_sha256),
        "invalid_expected_report_digest",
    )
    with supervisor.owner(session):
        state = supervisor.read_state(session)
        require(state["status"] in ("halted", "stopped"), "session_not_stopped")
        require(
            not os.path.lexists(session.stop_path) and not archive.stop_requested(session.archive_path),
            "operator_stop",
        )
        report = state["last_report"]
        require(report is not None, "no_successful_paper_report")
        actual = hashlib.sha256(archive.canonical(report).encode("ascii")).hexdigest()
        require(actual == expected_report_sha256, "external_report_digest_mismatch")
        audit_report(session, report, runner=runner)
        return supervisor.publish(
            session, state, "stopped", "operator_reviewed", clock, consecutive_failures=0
        )


def add_session_arguments(parser):
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--start-open-ms", type=int, required=True)
    parser.add_argument("--max-failures", type=int, default=3)


def cli_session(args):
    return supervisor.make_session(
        args.archive,
        args.session_dir,
        args.binary,
        args.template,
        args.start_open_ms,
        args.max_failures,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("backup")
    add_session_arguments(save)
    save.add_argument("--destination", type=Path, required=True)
    resume = sub.add_parser("review-resume")
    add_session_arguments(resume)
    resume.add_argument("--expected-report-sha256", required=True)
    for name in ("verify", "restore"):
        action = sub.add_parser(name)
        action.add_argument("--bundle", type=Path, required=True)
        action.add_argument("--expected-sha256", required=True)
        action.add_argument("--binary", type=Path, required=True)
        if name == "restore":
            action.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = capture(cli_session(args), args.destination)
        elif args.command == "review-resume":
            state = review_resume(cli_session(args), args.expected_report_sha256)
            result = {"status": state["status"], "reason": state["reason"], "live_orders_enabled": False}
        elif args.command == "verify":
            result = verify(args.bundle, args.expected_sha256, args.binary)
        else:
            result = restore(args.bundle, args.expected_sha256, args.binary, args.destination)
        print(archive.canonical(result))
        return 0
    except KeyboardInterrupt:
        return 130
    except (
        supervisor.SupervisorError,
        archive.ArchiveError,
        OSError,
        sqlite3.Error,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        zipfile.BadZipFile,
        RuntimeError,
    ):
        parser.exit(2, "Paper recovery refused; preserve existing data and review the inputs.\n")


if __name__ == "__main__":
    raise SystemExit(main())
