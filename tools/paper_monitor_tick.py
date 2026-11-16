"""One explicit PAPER observation and atomic local journal/alert-queue update.

No scheduler, external network, provider orders, worker restart or notification.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import paper_monitor
import paper_monitor_events
import paper_alert_queue


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("paper_monitor_tick_arguments_invalid")


def _args(argv):
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("service", "capture", "account", "directory"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 60:
        raise ValueError("invalid_timeout")
    for name in ("service", "capture", "account", "directory"):
        path = Path(getattr(args, name))
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("invalid_path")
    return args


def _operate(args):
    """Read three native probes first; append observation and queue in one txn."""
    probe_args = SimpleNamespace(
        service=args.service, capture=args.capture, account=args.account,
        timeout_seconds=args.timeout_seconds)
    observed = paper_monitor.inspect(probe_args)
    status, signature = paper_monitor_events._observe(observed)
    if set(observed["checks"]) != {"service", "capture", "account"}:
        raise ValueError("incomplete_probes")

    directory = paper_monitor_events._private_directory(args.directory)
    db = paper_monitor_events._open_database(directory)
    before = os.stat(db, follow_symlinks=False)
    with closing(paper_alert_queue._connect(db)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            recording = paper_monitor_events.record_in_transaction(conn, status, signature)
            queue = paper_alert_queue._sync(conn)
            after = os.stat(db, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("journal_replaced")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    return {
        "schema_version": 1,
        "source": "paper_monitor_tick",
        "status": status,
        "exit_code": 0 if status == "healthy" else 1,
        "checks": observed["checks"],
        "event_sequence": recording["event_sequence"],
        "event_created": recording["event_created"],
        "observation_count": recording["observation_count"],
        "pending_acknowledgements": queue["pending"],
        "boundaries": {
            "live_enabled": False,
            "external_notification_delivered": False,
            "orders_submitted": False,
            "service_restarted": False,
            "production_ready": False,
        },
    }


def main(argv=None):
    try:
        report = _operate(_args(argv))
        code = report["exit_code"]
    except (ValueError, paper_monitor_events.Invalid, paper_alert_queue.Refused):
        code, report = 2, {"status": "unavailable"}
    except (OSError, sqlite3.Error):
        code, report = 4, {"status": "uncertain"}
    except Exception:
        code, report = 4, {"status": "uncertain"}
    if code in (2, 4):
        report = {
            "schema_version": 1,
            "source": "paper_monitor_tick",
            "status": report["status"],
            "exit_code": code,
            "boundaries": {
                "live_enabled": False,
                "external_notification_delivered": False,
                "orders_submitted": False,
                "service_restarted": False,
                "production_ready": False,
            },
        }
    try:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, allow_nan=False),
              flush=True)
    except OSError:
        return 4
    return code


if __name__ == "__main__":
    raise SystemExit(main())
