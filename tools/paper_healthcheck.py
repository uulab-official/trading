"""Read-only monitor for a local paper supervisor heartbeat (no orders/network).

Exit 0 healthy and fresh, 2 stale/degraded/halted/invalid, 4 unreadable/missing.
Use in local health probes, scheduled jobs, and operator-run diagnostics.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_HEALTH_BYTES = 65536


def inspect(path, *, max_age_seconds=7200, now=None):
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError("max_age_seconds must be a positive integer")
    path = Path(path)
    with path.open("rb") as stream:
        content = stream.read(MAX_HEALTH_BYTES + 1)
    if len(content) > MAX_HEALTH_BYTES:
        raise ValueError("Heartbeat exceeds size limit")
    data = json.loads(content)
    if (
        not isinstance(data, dict)
        or data.get("mode") != "paper"
        or data.get("live_orders_enabled") is not False
    ):
        raise ValueError("Invalid supervisor mode or live safety marker")
    status = data.get("status")
    if status not in ("healthy", "risk_blocked", "degraded", "halted", "stopped"):
        raise ValueError("Invalid supervisor status")
    stamp = data.get("last_attempt_utc")
    if not isinstance(stamp, str):
        raise ValueError("Missing attempt timestamp")
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ValueError("Invalid timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Heartbeat timestamp must be timezone aware")
    epoch = moment.astimezone(timezone.utc).timestamp()
    observed = time.time() if now is None else now
    if not isinstance(observed, (float, int)) or not (0 <= observed < 10**12):
        raise ValueError("Invalid monitoring clock")
    age = observed - epoch
    if age < -60:
        raise ValueError("Heartbeat is from the future; clock synchronization required")
    if age > max_age_seconds:
        result = "stale"
    else:
        result = status
    return {
        "mode": "paper",
        "live_orders_enabled": False,
        "status": result,
        "heartbeat_age_seconds": max(0, int(age)),
        "last_attempt_utc": stamp,
        "market": data.get("market"),
        "operator_action_required": result != "healthy",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("heartbeat", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=7200)
    args = parser.parse_args(argv)
    try:
        report = inspect(args.heartbeat, max_age_seconds=args.max_age_seconds)
    except (OSError, UnicodeError):
        print("Paper heartbeat unavailable", file=sys.stderr)
        return 4
    except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
        print("Paper heartbeat invalid or clock skew detected", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
