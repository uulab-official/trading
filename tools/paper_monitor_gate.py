"""Read-only PAPER monitor freshness and acknowledgement gate.

A local operator inspection, not a scheduler, delivery service or live-trading
authorization. The gate fails closed when the last snapshot is old.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

# Python -I omits the entrypoint directory from sys.path. Only load adjacent
# reviewed local tooling; never import from PYTHONPATH or site packages.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_alert_queue as queue


class GateUnavailable(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise GateUnavailable("invalid_arguments")


def _parse(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--max-age-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if type(args.max_age_seconds) is not int or not 30 <= args.max_age_seconds <= 86400:
        raise GateUnavailable("invalid_age")
    return args


def _stamp(value):
    if type(value) is not str or not 20 <= len(value) <= 64:
        raise GateUnavailable("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GateUnavailable("invalid_timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateUnavailable("naive_timestamp")
    return parsed.astimezone(timezone.utc)


def inspect(directory, max_age_seconds, *, now=None):
    """Assess one already-complete journal; no writes to any trading store."""
    root = queue._root(directory)
    db = root / queue.DB_NAME
    identity = queue._open_existing(db)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise GateUnavailable("naive_now")
    current = current.astimezone(timezone.utc)
    with closing(queue._connect(db, read_only=True)) as conn:
        queue._require_synced(conn)
        row = conn.execute(
            "SELECT status, signature, observed_at, observations FROM state "
            "WHERE singleton=1"
        ).fetchone()
        if row is None or type(row[3]) is not int or row[3] < 1:
            raise GateUnavailable("missing_state")
        last = _stamp(row[2])
        age = (current - last).total_seconds()
        if age < 0:
            raise GateUnavailable("future_observation")
        if age > 2**31:
            raise GateUnavailable("age_out_of_range")

        # An unseen fault must not be hidden by a later healthy snapshot.
        unacknowledged_faults = conn.execute(
            "SELECT COUNT(*) FROM alert_queue "
            "WHERE status='attention_required' AND acknowledged_at IS NULL"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM alert_queue WHERE acknowledged_at IS NULL"
        ).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if (type(unacknowledged_faults) is not int or type(pending) is not int
                or not 0 <= unacknowledged_faults <= pending <= count <= queue.MAX_EVENTS):
            raise GateUnavailable("invalid_counts")

        if queue._open_existing(db) != identity:
            raise GateUnavailable("journal_replaced")

    reasons = []
    if age > max_age_seconds:
        reasons.append("stale_observation")
    if row[0] != "healthy":
        reasons.append("current_degraded")
    if unacknowledged_faults:
        reasons.append("unacknowledged_fault")
    return {
        "schema_version": 1,
        "source": "paper_monitor_gate",
        "status": "healthy" if not reasons else "attention_required",
        "exit_code": 0 if not reasons else 1,
        "reasons": reasons,
        "last_observed_at": row[2],
        "age_seconds": round(age, 3),
        "max_age_seconds": max_age_seconds,
        "observations": row[3],
        "event_count": count,
        "pending_acknowledgements": pending,
        "unacknowledged_faults": unacknowledged_faults,
        "boundaries": {
            "read_only": True,
            "external_notification_delivered": False,
            "live_execution": False,
            "production_ready": False,
        },
    }


def main(argv=None):
    try:
        args = _parse(argv)
        report = inspect(args.directory, args.max_age_seconds)
        code = report["exit_code"]
    except (GateUnavailable, queue.Refused, OSError, sqlite3.Error, ValueError):
        code = 2
        report = {
            "schema_version": 1, "source": "paper_monitor_gate",
            "status": "unavailable", "exit_code": code, "reasons": [],
            "boundaries": {
                "read_only": True, "external_notification_delivered": False,
                "live_execution": False, "production_ready": False,
            },
        }
    except Exception:
        code = 4
        report = {
            "schema_version": 1, "source": "paper_monitor_gate",
            "status": "uncertain", "exit_code": code, "reasons": [],
            "boundaries": {
                "read_only": True, "external_notification_delivered": False,
                "live_execution": False, "production_ready": False,
            },
        }
    try:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)
    except OSError:
        return 4
    return code


if __name__ == "__main__":
    raise SystemExit(main())
