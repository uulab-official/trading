"""Local paper-only supervisor consuming the verified Binance archive.

It never starts a broker, fetches private data or enables live trading.
The public archive collector is a separate process.
"""

import argparse
import fcntl
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import binance_public_archive as archive

MAX_REPORT_BYTES = 8 * 1024 * 1024
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
STATUSES = {"initializing", "running", "paper_current", "degraded", "halted", "stopped"}


class SupervisorError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SupervisorError(message)


def regular(path):
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_session_file")
    return Path(path).resolve(strict=True)


def load_json(path):
    with open(path, "rb") as handle:
        raw = handle.read(MAX_REPORT_BYTES + 1)
    require(len(raw) <= MAX_REPORT_BYTES, "oversized_session_state")
    return json.loads(raw, object_pairs_hook=archive.unique_object)


@dataclass(frozen=True)
class Session:
    archive_path: Path
    directory: Path
    binary: Path
    template: dict
    start_ms: int
    max_failures: int

    @property
    def state_path(self):
        return Path(f"{self.directory}.forward-status.json")

    @property
    def stop_path(self):
        return Path(f"{self.directory}.forward.STOP")

    @property
    def lock_path(self):
        return Path(f"{self.directory}.forward.owner")

    @property
    def identity(self):
        return {
            "archive_path": str(self.archive_path),
            "directory": str(self.directory),
            "binary": str(self.binary),
            "template_sha256": archive.digest("paper-forward-template-v1", self.template),
            "start_ms": self.start_ms,
            "max_failures": self.max_failures,
        }


def make_session(archive_path, directory, binary, template_path, start_ms, max_failures):
    archive_path = archive.existing_archive(archive_path)
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "invalid_session_directory")
    directory = directory.resolve(strict=True)
    # Resolve the explicitly supplied executable (which may be an installed link).
    binary = Path(binary).resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK), "invalid_paper_binary")
    template_path = regular(template_path)
    require(type(start_ms) is int and start_ms > 0, "invalid_session_start")
    require(type(max_failures) is int and 1 <= max_failures <= 10, "invalid_failure_budget")
    template = archive.read_template(template_path)
    require(isinstance(template, dict), "invalid_template")
    session = Session(archive_path, directory, binary, template, start_ms, max_failures)
    inputs = [archive_path, binary, template_path]
    reserved = [session.state_path, session.lock_path, session.stop_path]
    for item in inputs:
        require(item not in reserved and not item.is_relative_to(directory), "session_path_overlap")
    for item in reserved:
        require(item not in inputs, "session_path_overlap")
        if os.path.lexists(item):
            regular(item)
    return session


@contextmanager
def owner(session):
    fd = os.open(session.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_session_owner")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SupervisorError("forward_owner_active") from None
        yield
    finally:
        os.close(fd)


def new_state(session):
    return {
        "version": 1,
        "mode": "paper_forward_supervisor",
        "live_orders_enabled": False,
        "identity": session.identity,
        "status": "initializing",
        "consecutive_failures": 0,
        "reason": None,
        "updated_at_ms": None,
        "last_success_ms": None,
        "last_report": None,
    }


def read_state(session):
    if not os.path.lexists(session.state_path):
        return new_state(session)
    state = load_json(regular(session.state_path))
    require(isinstance(state, dict) and set(state) == set(new_state(session)), "invalid_state")
    require(
        state["version"] == 1
        and state["mode"] == "paper_forward_supervisor"
        and state["live_orders_enabled"] is False
        and state["identity"] == session.identity
        and state["status"] in STATUSES,
        "session_identity_or_state_mismatch",
    )
    failures = state["consecutive_failures"]
    require(
        type(failures) is int and 0 <= failures <= session.max_failures,
        "invalid_saved_failure_count",
    )
    require(
        state["status"] == "halted" or failures < session.max_failures,
        "invalid_saved_halt_state",
    )
    for field in ("updated_at_ms", "last_success_ms"):
        require(
            state[field] is None or (type(state[field]) is int and state[field] > 0),
            "invalid_saved_timestamp",
        )
    report = state["last_report"]
    require((report is None) == (state["last_success_ms"] is None), "invalid_saved_report")
    if report is not None:
        require(
            isinstance(report, dict)
            and report.get("mode") == "offline_paper_forward"
            and report.get("live_orders_enabled") is False
            and isinstance(report.get("session_sha256"), str)
            and DIGEST.fullmatch(report["session_sha256"]),
            "invalid_saved_report",
        )
    return state


def write_state(session, state):
    if os.path.lexists(session.state_path):
        regular(session.state_path)
    data = archive.canonical(state).encode("ascii")
    require(len(data) <= MAX_REPORT_BYTES, "oversized_session_state")
    fd, name = tempfile.mkstemp(prefix=".forward-status-", dir=session.directory.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, session.state_path)
        parent = os.open(session.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish(session, state, status, reason, clock, **updates):
    result = {
        **state,
        **updates,
        "status": status,
        "reason": reason,
        "updated_at_ms": clock(),
    }
    write_state(session, result)
    return result


def fail(session, state, reason, retryable, clock):
    failures = min(state["consecutive_failures"] + 1, session.max_failures)
    status = "degraded" if retryable and failures < session.max_failures else "halted"
    return publish(session, state, status, reason, clock, consecutive_failures=failures)


def invoke_paper_binary(session, envelope, initialize):
    with tempfile.TemporaryDirectory(prefix="paper-forward-input-") as temporary:
        directory = Path(temporary)
        input_path = directory / "scenario.json"
        input_path.write_text(archive.canonical(envelope), encoding="ascii")
        output_path = directory / "report.json"
        command = [
            str(session.binary),
            "paper-forward",
            "--input",
            str(input_path),
            "--output-dir",
            str(session.directory),
        ]
        if initialize:
            command.append("--initialize")
        try:
            with open(output_path, "wb") as output:
                result = subprocess.run(
                    command,
                    cwd=directory,
                    env={},
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=180,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            raise SupervisorError("paper_replay_timeout") from None
        require(result.returncode == 0, "paper_replay_refused")
        return load_json(output_path)


def verify_report(envelope, report, previous):
    require(
        isinstance(report, dict)
        and report.get("mode") == "offline_paper_forward"
        and report.get("live_orders_enabled") is False
        and all(
            report.get(key) == envelope[key] for key in ("source", "source_id", "archive_head_sha256")
        )
        and report.get("input_bar_count") == len(envelope["scenario"]["bars"]),
        "invalid_paper_report_provenance",
    )
    session_id = report.get("session_sha256")
    require(isinstance(session_id, str) and DIGEST.fullmatch(session_id), "invalid_report_session")
    if previous is not None:
        require(previous["session_sha256"] == session_id, "changed_report_session")
    expected = [item["id"] for item in envelope["scenario"]["strategies"]]
    accounts = report.get("accounts")
    replay = report.get("replay")
    require(isinstance(accounts, list) and isinstance(replay, dict), "invalid_report_accounts")
    results = replay.get("results")
    require(
        isinstance(results, list)
        and [r["id"] for r in results] == expected
        and [a["strategy_id"] for a in accounts] == expected,
        "report_strategy_mismatch",
    )
    for item in accounts:
        checkpoint = item["checkpoint"]
        head = checkpoint["head"]
        require(
            checkpoint["ledger_id"] == f"forward-{session_id}-{item['strategy_id']}"
            and type(head["sequence"]) is int
            and 1 <= head["sequence"] <= 2048
            and isinstance(head["hash"], str)
            and DIGEST.fullmatch(head["hash"])
            and item["remaining_event_capacity"] == 2048 - head["sequence"],
            "invalid_report_checkpoint",
        )
    return report


def cycle(session, *, initialize=False, exporter=None, runner=None, clock=archive.now_ms):
    """Caller must hold the local session owner lock.

    Only an already-current verified archive can advance the simulated accounts.
    Once a fatal error or failure budget latches halted, never retry implicitly.
    """
    exporter = exporter or archive.export_forward
    runner = runner or invoke_paper_binary
    state = read_state(session)
    if state["status"] == "halted":
        return state
    if os.path.lexists(session.stop_path):
        return publish(session, state, "halted", "operator_stop", clock)
    if initialize:
        require(state["last_report"] is None, "session_already_initialized")
        require(not any(session.directory.iterdir()), "initialization_requires_empty_directory")
    state = publish(session, state, "running", None, clock)
    try:
        envelope = exporter(session.archive_path, session.start_ms, session.template, clock=clock)
        if os.path.lexists(session.stop_path):
            return publish(session, state, "halted", "operator_stop", clock)
        report = verify_report(
            envelope, runner(session, envelope, initialize), state["last_report"]
        )
    except archive.ArchiveError as error:
        retryable = error.reason == "capture_not_current"
        return fail(session, state, error.reason, retryable, clock)
    except SupervisorError as error:
        return fail(session, state, str(error), False, clock)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return fail(session, state, "forward_operation_refused", False, clock)
    return publish(
        session,
        state,
        "halted" if os.path.lexists(session.stop_path) else "paper_current",
        "operator_stop" if os.path.lexists(session.stop_path) else None,
        clock,
        consecutive_failures=0,
        last_success_ms=clock(),
        last_report=report,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--start-open-ms", type=int, required=True)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        require(60 <= args.poll_seconds <= 3600, "invalid_poll_interval")
        require(not (args.initialize and args.loop), "initialize_requires_one_shot")
        session = make_session(
            args.archive,
            args.session_dir,
            args.binary,
            args.template,
            args.start_open_ms,
            args.max_failures,
        )
        with owner(session):
            try:
                while True:
                    state = cycle(session, initialize=args.initialize)
                    print(
                        archive.canonical(
                            {
                                "mode": state["mode"],
                                "live_orders_enabled": False,
                                "status": state["status"],
                                "reason": state["reason"],
                                "consecutive_failures": state["consecutive_failures"],
                                "state_path": str(session.state_path),
                            }
                        ),
                        flush=True,
                    )
                    if state["status"] == "halted" or not args.loop:
                        return 0 if state["status"] == "paper_current" else 2
                    delay = min(3600, args.poll_seconds * 2 ** state["consecutive_failures"])
                    time.sleep(delay)
            except KeyboardInterrupt:
                state = read_state(session)
                if state["status"] != "halted":
                    publish(session, state, "stopped", "operator_interrupt", archive.now_ms)
                return 130
    except (SupervisorError, archive.ArchiveError, OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Paper forward supervisor refused; inspect local state and configuration.\n")


if __name__ == "__main__":
    raise SystemExit(main())
