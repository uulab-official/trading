"""Offline acceptance drill for PAPER monitor history, queue and freshness gate.

Uses only freshly generated synthetic status files and an ephemeral journal.
Never accesses accounts, providers, broker APIs or external notification systems.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

TOOLS = Path(__file__).resolve().parent
MAX_OUTPUT = 16384


class DrillError(RuntimeError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DrillError("invalid_arguments")


def _tools_present():
    for name in ("paper_monitor_events.py", "paper_alert_queue.py", "paper_monitor_gate.py"):
        path = TOOLS / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise DrillError("tool_unavailable")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _call(script, arguments, expected):
    command = [sys.executable, "-I", "-S", "-B", str(TOOLS / script), *arguments]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=12)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrillError("tool_unavailable") from exc
    if len(result.stdout) > MAX_OUTPUT or len(result.stderr) > 4096:
        raise DrillError("output_unbounded")
    try:
        report = json.loads(result.stdout, object_pairs_hook=_unique_pairs,
                            parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DrillError("output_invalid") from exc
    sources = {
        "paper_monitor_events.py": "paper_monitor_events",
        "paper_alert_queue.py": "paper_alert_queue",
        "paper_monitor_gate.py": "paper_monitor_gate",
    }
    if type(report) is not dict:
        raise DrillError("output_invalid")
    boundaries = report.get("boundaries")
    safe_flags = {
        "paper_monitor_events.py": ("live_enabled", "orders_submitted",
                                    "notification_delivered", "production_ready"),
        "paper_alert_queue.py": ("live_enabled", "external_delivery",
                                 "broker_orders", "production_ready"),
        "paper_monitor_gate.py": ("external_notification_delivered",
                                  "live_execution", "production_ready"),
    }
    if (type(boundaries) is not dict
            or any(boundaries.get(flag) is not False
                   for flag in safe_flags[script])
            or (script == "paper_monitor_gate.py"
                and boundaries.get("read_only") is not True)):
        raise DrillError("unsafe_boundary")
    if (type(report) is not dict or type(report.get("exit_code")) is not int
            or report["exit_code"] != result.returncode or result.returncode != expected
            or report.get("source") != sources.get(script)
            or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or type(report.get("boundaries")) is not dict
            or report["boundaries"].get("production_ready") is not False):
        raise DrillError("result_unexpected")
    return report


def _snapshot(degraded=False):
    return {
        "schema_version": 1, "source": "paper_monitor",
        "status": "attention_required" if degraded else "healthy",
        "exit_code": 1 if degraded else 0,
        "checks": {
            "service": {"status": "running", "healthy": True, "exit_code": 0},
            "capture": {"status": "stale" if degraded else "current",
                        "healthy": not degraded, "exit_code": 1 if degraded else 0},
            "account": {"status": "current", "healthy": True, "exit_code": 0},
        },
    }


def _write_snapshot(root, name, report):
    target = root / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if os.write(fd, raw) != len(raw):
            raise DrillError("write_unconfirmed")
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def _require(report, field, value):
    # Equality alone admits True == 1 and False == 0 in Python.
    # Evidence fields must preserve both the expected type and value.
    if (type(report) is not dict or field not in report
            or type(report[field]) is not type(value)
            or report[field] != value):
        raise DrillError("unexpected_state")


def _require_reasons(report, required, forbidden=()):
    reasons = report.get("reasons") if type(report) is dict else None
    if (type(reasons) is not list or any(type(item) is not str for item in reasons)
            or len(reasons) != len(set(reasons))
            or not set(required).issubset(reasons)
            or any(reason in reasons for reason in forbidden)):
        raise DrillError("unexpected_reasons")


def run():
    _tools_present()
    stages = []
    with tempfile.TemporaryDirectory(prefix="paper-monitor-drill-") as tmp:
        root = Path(tmp) / "history"
        root.mkdir(mode=0o700)
        gate_args = ["--directory", str(root), "--max-age-seconds", "120"]

        def record(filename, report):
            path = _write_snapshot(Path(tmp), filename, report)
            return _call("paper_monitor_events.py",
                         ["record", "--directory", str(root), "--input", str(path)], 0)

        def sync():
            return _call("paper_alert_queue.py", ["sync", "--directory", str(root)], 0)

        def gate(code):
            return _call("paper_monitor_gate.py", gate_args, code)

        _require(record("initial.json", _snapshot()), "event_sequence", 1)
        _require(sync(), "pending", 1)
        _require(gate(0), "status", "healthy")
        repeated = record("healthy-repeated.json", _snapshot())
        _require(repeated, "event_sequence", 1)
        _require(repeated, "event_created", False)
        _require(gate(0), "status", "healthy")
        stages.append("healthy_observed")

        _require(record("fault.json", _snapshot(True)), "event_sequence", 2)
        _require(gate(2), "status", "unavailable")
        _require(sync(), "pending", 2)
        degraded = gate(1)
        _require_reasons(degraded, ("current_degraded", "unacknowledged_fault"))
        stages.append("fault_detected")

        _require(record("recovered.json", _snapshot()), "event_sequence", 3)
        _require(sync(), "pending", 3)
        unresolved = gate(1)
        _require_reasons(unresolved, ("unacknowledged_fault",),
                         ("current_degraded",))
        stages.append("recovery_unacknowledged")

        _require(_call("paper_alert_queue.py",
                       ["ack", "--directory", str(root), "--sequence", "2",
                        "--reason", "synthetic incident reviewed"], 0),
                 "acknowledged_sequence", 2)
        healthy = gate(0)
        _require(healthy, "unacknowledged_faults", 0)
        _require(healthy, "pending_acknowledgements", 2)
        _call("paper_alert_queue.py",
              ["ack", "--directory", str(root), "--sequence", "2",
               "--reason", "synthetic incident reviewed"], 2)
        stages.append("operator_acknowledged")

        saved = _call("paper_monitor_events.py",
                      ["check", "--directory", str(root)], 0)
        _require(saved, "event_count", 3)
        _require(saved, "observation_count", 4)
        stages.append("journal_consistent")

    return {
        "schema_version": 1, "source": "paper_monitor_drill",
        "status": "verified_synthetic", "exit_code": 0,
        "stages": stages, "events_verified": 3,
        "boundaries": {
            "synthetic_only": True, "provider_requests": False,
            "broker_orders": False, "service_activated": False,
            "external_notification_delivered": False, "production_ready": False,
        },
    }


def main(argv=None):
    parser = Parser(add_help=False, allow_abbrev=False)
    try:
        parser.parse_args(argv)
        result = run()
        code = 0
    except (DrillError, ValueError, OSError):
        code = 2
        result = {"schema_version": 1, "source": "paper_monitor_drill",
                  "status": "unavailable", "exit_code": code}
    except Exception:
        code = 4
        result = {"schema_version": 1, "source": "paper_monitor_drill",
                  "status": "uncertain", "exit_code": code}
    result.setdefault("boundaries", {
        "synthetic_only": True, "provider_requests": False,
        "broker_orders": False, "service_activated": False,
        "external_notification_delivered": False, "production_ready": False,
    })
    try:
        print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    except OSError:
        return 4
    return code


if __name__ == "__main__":
    raise SystemExit(main())
