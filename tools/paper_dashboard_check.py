"""Check the local read-only dashboard; never follows redirects or uses proxies."""
import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import socket
import threading
import time

MAX_RESPONSE_BYTES = 8192
SOURCE_NAMES = frozenset(("journal", "replay", "research", "paper_session"))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("invalid_number")


def _settings(port, timeout):
    if (type(port) is not int or not 1 <= port <= 65535
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not .1 <= timeout <= 10):
        raise ValueError("invalid_arguments")


def _validate(value, http_status, *, liveness):
    fields = {"schema_version", "source", "mode", "read_only", "live_enabled",
              "scope", "status", "observed_at"}
    if not liveness:
        fields.add("sources")
    if (type(value) is not dict or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["source"] != "local_paper_dashboard" or value["mode"] != "paper"
            or value["read_only"] is not True or value["live_enabled"] is not False
            or type(value["observed_at"]) is not str or len(value["observed_at"]) > 64):
        raise ValueError("wrong_probe_contract")
    observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("invalid_observation_time")
    if liveness:
        if value["scope"] != "observer_process" or value["status"] != "alive" or http_status != 200:
            raise ValueError("wrong_probe_status")
        return 0
    sources = value["sources"]
    if (value["scope"] != "configured_source_readability" or type(sources) is not dict
            or set(sources) != SOURCE_NAMES
            or any(type(state) is not str or state not in {"available", "unavailable", "not_configured"}
                   for state in sources.values())):
        raise ValueError("invalid_sources")
    configured = [state for state in sources.values() if state != "not_configured"]
    status = ("not_configured" if not configured else
              "degraded" if "unavailable" in configured else "ready")
    if value["status"] != status or http_status != (200 if status == "ready" else 503):
        raise ValueError("wrong_probe_status")
    return 0 if status == "ready" else 1


def probe(*, port=8765, timeout=5.0, liveness=False):
    """Observe the fixed loopback service within one overall network deadline."""
    _settings(port, timeout)
    if type(liveness) is not bool:
        raise ValueError("invalid_arguments")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    deadline = time.monotonic() + timeout
    expired = threading.Event()
    timer = None
    try:
        connection.connect()
        transport = connection.sock

        def expire():
            expired.set()
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            transport.close()

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("probe_timeout")
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        route = "/api/liveness" if liveness else "/api/readiness"
        connection.request("GET", route, headers={"Accept": "application/json", "Connection": "close"})
        with connection.getresponse() as response:
            if response.status not in {200, 503}:
                raise ValueError("unexpected_status")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ValueError("unexpected_content_type")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES or expired.is_set() or time.monotonic() >= deadline:
                raise ValueError("invalid_probe_response")
            value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            result = _validate(value, response.status, liveness=liveness)
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("probe_timeout")
            return result, value
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(.2)
        connection.close()


def _error(reason):
    return dict(schema_version=1, source="local_paper_dashboard_probe", mode="paper",
                read_only=True, live_enabled=False, status="unavailable", error=reason)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Local dashboard port (1..65535)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Total network deadline (0.1..10 seconds)")
    parser.add_argument("--liveness", action="store_true", help="Check process response without source reads")
    try:
        args = parser.parse_args(argv)
        _settings(args.port, args.timeout)
    except ValueError:
        print(json.dumps(_error("invalid_arguments"), sort_keys=True))
        return 2
    try:
        code, data = probe(port=args.port, timeout=args.timeout, liveness=args.liveness)
    except KeyboardInterrupt:
        print(json.dumps(_error("probe_interrupted"), sort_keys=True))
        return 130
    except (OSError, http.client.HTTPException, ValueError, TypeError, UnicodeError,
            RecursionError, OverflowError):
        print(json.dumps(_error("probe_failed"), sort_keys=True))
        return 2
    print(json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
