"""Credential-free Binance spot history archive, recovery and bounded polling.

This tool never reads API keys, submits orders or starts a trading worker.
Only the fixed production public-data host and two GET routes are available.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HOST = "https://data-api.binance.vision"
SOURCE = "binance-spot-public-utc-v1"
INTERVALS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}
MAX_ROWS = 100_000
MAX_BODY = 2 * 1024 * 1024
CLOSE_GRACE_MS = 2_000
MAX_CLOCK_SKEW_MS = 5_000
DECIMAL = re.compile(r"[0-9]{1,32}(?:\.[0-9]{1,18})?\Z")
SYMBOL = re.compile(r"[A-Z0-9]{2,32}\Z")


class ArchiveError(ValueError):
    def __init__(self, reason, *, permanent=True):
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent


def require(condition, reason):
    if not condition:
        raise ArchiveError(reason)


def now_ms():
    return time.time_ns() // 1_000_000


def canonical(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest(*parts):
    h = hashlib.sha256()
    for part in parts:
        raw = canonical(part).encode("ascii")
        h.update(len(raw).to_bytes(8, "big"))
        h.update(raw)
    return h.hexdigest()


def decimal_text(value, *, positive=False):
    require(isinstance(value, str) and DECIMAL.fullmatch(value), "invalid_decimal")
    whole, _, fraction = value.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    result = whole + ("." + fraction if fraction else "")
    require(not positive or result != "0", "invalid_price")
    return result


def validated_bar(row, step):
    require(isinstance(row, (list, tuple)) and len(row) == 7, "invalid_bar_shape")
    stamp, end, opening, high, low, close, volume = row
    require(
        type(stamp) is int
        and 0 < stamp <= 253_402_300_799_999
        and stamp % step == 0
        and type(end) is int
        and end == stamp + step - 1,
        "invalid_bar_time",
    )
    prices = [decimal_text(value, positive=True) for value in (opening, high, low, close)]
    o, h, low_value, c = map(Decimal, prices)
    require(low_value <= min(o, c) <= max(o, c) <= h, "invalid_ohlc")
    return (stamp, end, *prices, decimal_text(volume))


def parse_page(raw, first, count, step):
    require(isinstance(raw, list) and len(raw) == count, "incomplete_page")
    result = []
    for index, item in enumerate(raw):
        require(isinstance(item, list) and len(item) == 12, "invalid_kline_shape")
        row = validated_bar((item[0], item[6], *item[1:6]), step)
        require(row[0] == first + index * step, "missing_or_reordered_bar")
        result.append(row)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, "duplicate_json_key")
        obj[key] = value
    return obj


def public_get(route, params):
    require(route in ("time", "klines"), "unsupported_public_route")
    # No environment proxy credentials, redirect following, or private endpoints.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    query = urlencode(params)
    url = f"{HOST}/api/v3/{route}" + (f"?{query}" if query else "")
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=10) as response:
            require(response.status == 200, "unexpected_http_status")
            body = response.read(MAX_BODY + 1)
    except HTTPError as exc:
        if exc.code in (418, 429):
            raise ArchiveError("rate_limit_operator_review") from None
        raise ArchiveError("http_failure", permanent=exc.code < 500) from None
    except (URLError, OSError):
        raise ArchiveError("transport_failure", permanent=False) from None
    require(len(body) <= MAX_BODY, "oversized_response")
    try:
        return json.loads(body, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        raise ArchiveError("invalid_json") from None


def config(symbol, interval, start_ms, max_failures):
    require(isinstance(symbol, str) and SYMBOL.fullmatch(symbol), "invalid_symbol")
    require(interval in INTERVALS, "unsupported_fixed_interval")
    step = INTERVALS[interval]
    require(
        type(start_ms) is int and 0 < start_ms <= 253_402_300_799_999 and start_ms % step == 0,
        "invalid_start",
    )
    require(type(max_failures) is int and 1 <= max_failures <= 10, "invalid_failure_budget")
    return {
        "source": SOURCE,
        "symbol": symbol,
        "interval": interval,
        "start_ms": start_ms,
        "max_failures": max_failures,
    }


def reserve_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)


@contextmanager
def connection(path, *, readonly=False):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_archive_path")
    mode = "ro" if readonly else "rw"
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        if readonly:
            db.execute("PRAGMA query_only = ON")
        else:
            db.execute("PRAGMA synchronous = FULL")
        yield db
    finally:
        db.close()


def create(path, symbol, interval, start_ms, max_failures=3):
    cfg = config(symbol, interval, start_ms, max_failures)
    reserve_file(path)
    with connection(path) as db, db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE meta (
            id INTEGER PRIMARY KEY CHECK (id = 1), config TEXT NOT NULL,
            count INTEGER NOT NULL, head TEXT NOT NULL, failures INTEGER NOT NULL,
            status TEXT NOT NULL, reason TEXT, server_ms INTEGER,
            target_ms INTEGER, last_success_ms INTEGER
            )"""
        )
        db.execute(
            """CREATE TABLE bars (
            open_ms INTEGER PRIMARY KEY, close_ms INTEGER NOT NULL,
            open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL,
            close TEXT NOT NULL, volume TEXT NOT NULL, hash TEXT NOT NULL
            )"""
        )
        db.execute(
            "INSERT INTO meta VALUES (1, ?, 0, ?, 0, 'initializing', NULL, NULL, NULL, NULL)",
            (canonical(cfg), digest("archive-genesis", cfg)),
        )


def audit(db):
    rows = db.execute("SELECT * FROM meta LIMIT 2").fetchall()
    require(len(rows) == 1 and rows[0]["id"] == 1, "invalid_metadata")
    meta = dict(rows[0])
    cfg = json.loads(meta["config"])
    require(isinstance(cfg, dict), "invalid_metadata")
    expected = config(cfg["symbol"], cfg["interval"], cfg["start_ms"], cfg["max_failures"])
    require(canonical(expected) == meta["config"], "invalid_identity")
    require(type(meta["count"]) is int and 0 <= meta["count"] <= MAX_ROWS, "invalid_count")
    require(
        type(meta["failures"]) is int and 0 <= meta["failures"] <= cfg["max_failures"],
        "invalid_failure_state",
    )
    require(
        meta["status"] in ("initializing", "caught_up", "catching_up", "degraded", "halted"),
        "invalid_status",
    )
    for field in ("server_ms", "target_ms", "last_success_ms"):
        require(
            meta[field] is None or (type(meta[field]) is int and meta[field] > 0),
            "invalid_metadata_time",
        )
    require(
        (meta["server_ms"] is None)
        == (meta["target_ms"] is None)
        == (meta["last_success_ms"] is None),
        "incomplete_metadata_time",
    )
    require(
        (meta["status"] == "halted" or meta["failures"] < cfg["max_failures"]),
        "invalid_failure_state",
    )
    step = INTERVALS[cfg["interval"]]
    head = digest("archive-genesis", cfg)
    last = None
    count = 0
    for stored in db.execute("SELECT * FROM bars ORDER BY open_ms LIMIT ?", (MAX_ROWS + 1,)):
        row = tuple(stored)[:-1]
        require(validated_bar(row, step) == row, "noncanonical_saved_bar")
        require(row[0] == cfg["start_ms"] + count * step, "saved_archive_gap")
        head = digest("archive-bar", head, row)
        require(stored["hash"] == head, "saved_archive_hash_mismatch")
        last = row
        count += 1
    require(count == meta["count"] and head == meta["head"], "saved_archive_head_mismatch")
    if meta["server_ms"] is not None:
        target = (meta["server_ms"] - CLOSE_GRACE_MS) // step * step - step
        require(meta["target_ms"] == target and last is not None, "invalid_saved_target")
        require(last[0] <= target, "saved_archive_ahead_of_target")
        if meta["status"] == "caught_up":
            require(last[0] == target and meta["failures"] == 0, "invalid_ready_state")
        if meta["status"] == "catching_up":
            require(last[0] < target and meta["failures"] == 0, "invalid_catchup_state")
    return cfg, meta, last


def read_clock(fetch, clock):
    before = clock()
    result = fetch("time", {})
    after = clock()
    require(
        isinstance(result, dict) and type(result.get("serverTime")) is int,
        "invalid_server_time",
    )
    server = result["serverTime"]
    require(
        type(before) is int
        and type(after) is int
        and 0 < before <= after
        and after - before <= 20_000
        and before - MAX_CLOCK_SKEW_MS <= server <= after + MAX_CLOCK_SKEW_MS,
        "clock_skew_or_regression",
    )
    return server


def stop_requested(path):
    return os.path.lexists(f"{path}.STOP")


def sync_once(path, *, fetch=public_get, clock=now_ms, pages=3, page_size=1000):
    """Advance one bounded contiguous prefix, checking the last saved overlap.

    Hold a SQLite writer transaction across the bounded read-only requests.
    All pages are validated before any insert. Failure updates only monitoring
    state; database failures roll back the complete transaction.
    """
    require(type(pages) is int and 1 <= pages <= 10, "invalid_page_budget")
    require(type(page_size) is int and 2 <= page_size <= 1000, "invalid_page_size")
    with connection(path) as db, db:
        db.execute("BEGIN IMMEDIATE")
        cfg, meta, last = audit(db)
        if meta["status"] == "halted":
            return meta["status"]
        try:
            require(not stop_requested(path), "operator_stop")
            server = read_clock(fetch, clock)
            require(
                meta["server_ms"] is None or server >= meta["server_ms"],
                "server_time_regression",
            )
            step = INTERVALS[cfg["interval"]]
            target = (server - CLOSE_GRACE_MS) // step * step - step
            first = cfg["start_ms"] if last is None else last[0]
            require(target >= first, "no_completed_bar")
            capacity = MAX_ROWS - meta["count"]
            overlap = int(last is not None)
            total = (target - first) // step + 1
            limit = min(total, pages * page_size, capacity + overlap)
            require(limit >= 1 and (limit > overlap or total == overlap), "archive_capacity")
            accepted = []
            cursor = first
            while len(accepted) < limit:
                require(not stop_requested(path), "operator_stop")
                count = min(page_size, limit - len(accepted))
                raw = fetch(
                    "klines",
                    {
                        "symbol": cfg["symbol"],
                        "interval": cfg["interval"],
                        "startTime": cursor,
                        "endTime": cursor + count * step - 1,
                        "limit": count,
                        "timeZone": "0",
                    },
                )
                accepted.extend(parse_page(raw, cursor, count, step))
                cursor += count * step
            if last is not None:
                require(accepted[0] == last, "saved_overlap_conflict")
                accepted = accepted[1:]
            require(not stop_requested(path), "operator_stop")
        except ArchiveError as error:
            failures = min(meta["failures"] + 1, cfg["max_failures"])
            halted = error.permanent or failures >= cfg["max_failures"]
            status = "halted" if halted else "degraded"
            db.execute(
                "UPDATE meta SET failures=?, status=?, reason=? WHERE id=1",
                (failures, status, error.reason),
            )
            return status

        head = meta["head"]
        for row in accepted:
            head = digest("archive-bar", head, row)
            db.execute("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*row, head))
        latest = accepted[-1][0] if accepted else last[0]
        status = "caught_up" if latest == target else "catching_up"
        db.execute(
            """UPDATE meta SET count=?, head=?, failures=0, status=?, reason=NULL,
            server_ms=?, target_ms=?, last_success_ms=? WHERE id=1""",
            (meta["count"] + len(accepted), head, status, server, target, clock()),
        )
        audit(db)
        return status


def status(path, *, clock=now_ms, max_age_ms=180_000, expected_head=None):
    require(type(max_age_ms) is int and 1_000 <= max_age_ms <= 86_400_000, "invalid_age")
    with connection(path, readonly=True) as db:
        db.execute("BEGIN")
        cfg, meta, last = audit(db)
        if expected_head is not None:
            require(meta["head"] == expected_head, "external_checkpoint_mismatch")
        now = clock()
        age = None if meta["last_success_ms"] is None else now - meta["last_success_ms"]
        current_target = (now - CLOSE_GRACE_MS) // INTERVALS[cfg["interval"]] * INTERVALS[
            cfg["interval"]
        ] - INTERVALS[cfg["interval"]]
        ready = (
            meta["status"] == "caught_up"
            and not stop_requested(path)
            and age is not None
            and 0 <= age <= max_age_ms
            and last is not None
            and last[0] >= current_target
        )
        return {
            "mode": "public_history_only",
            "live_orders_enabled": False,
            "identity": cfg,
            "status": meta["status"],
            "reason": meta["reason"],
            "consecutive_failures": meta["failures"],
            "count": meta["count"],
            "head_sha256": meta["head"],
            "last_open_ms": None if last is None else last[0],
            "target_open_ms": meta["target_ms"],
            "last_success_ms": meta["last_success_ms"],
            "age_ms": age,
            "capture_current": ready,
        }


@contextmanager
def owner(path):
    lock_path = f"{Path(path).resolve()}.owner"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_owner_lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArchiveError("archive_owner_active") from None
        yield
    finally:
        os.close(fd)


def resume(path, expected_head):
    """Explicit operator review only; never remove STOP or clear a corrupt history."""
    with owner(path), connection(path) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _, meta, _ = audit(db)
        require(meta["status"] == "halted", "archive_not_halted")
        require(meta["head"] == expected_head, "external_checkpoint_mismatch")
        require(not stop_requested(path), "operator_stop")
        db.execute("UPDATE meta SET failures=0, status='initializing', reason=NULL WHERE id=1")


def backup(path, destination):
    """Native SQLite snapshot; never copy a live WAL file or overwrite a path."""
    with connection(path, readonly=True) as source:
        source.execute("BEGIN")
        cfg, meta, _ = audit(source)
        reserve_file(destination)
        with connection(destination) as target:
            source.backup(target)
    result = status(destination, expected_head=meta["head"])
    require(result["identity"] == cfg, "backup_identity_mismatch")
    return result


def export_window(path, count, *, end_open_ms=None, template=None):
    """Exact decimal rows for the Rust replay scenario (no market-rule inference)."""
    require(type(count) is int and 2 <= count <= 240, "invalid_export_count")
    with connection(path, readonly=True) as db:
        db.execute("BEGIN")
        cfg, meta, last = audit(db)
        require(last is not None, "archive_empty")
        end = last[0] if end_open_ms is None else end_open_ms
        step = INTERVALS[cfg["interval"]]
        require(type(end) is int and end % step == 0 and end <= last[0], "invalid_export_end")
        first = end - (count - 1) * step
        require(first >= cfg["start_ms"], "insufficient_export_history")
        bars = []
        for row in db.execute(
            "SELECT open_ms, open, high, low, close, volume FROM bars "
            "WHERE open_ms BETWEEN ? AND ? ORDER BY open_ms",
            (first, end),
        ):
            bars.append(dict(row))
        require(len(bars) == count, "incomplete_export")
        if template is None:
            return {"identity": cfg, "archive_head_sha256": meta["head"], "bars": bars}
        require(isinstance(template, dict) and template.get("version") == 1, "invalid_template")
        require(
            isinstance(template.get("market"), dict)
            and isinstance(template.get("account"), dict)
            and isinstance(template["market"].get("base_asset"), str)
            and isinstance(template["account"].get("quote_asset"), str)
            and template["market"]["base_asset"] + template["account"]["quote_asset"]
            == cfg["symbol"]
            and template.get("timeframe") == cfg["interval"]
            and "bars" in template,
            "replay_template_identity_mismatch",
        )
        return {**template, "bars": bars}


def read_template(path):
    with open(path, "rb") as handle:
        raw = handle.read(MAX_BODY + 1)
    require(len(raw) <= MAX_BODY, "oversized_template")
    return json.loads(raw, object_pairs_hook=unique_object)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("archive", type=Path)
    init.add_argument("--symbol", required=True)
    init.add_argument("--interval", choices=INTERVALS, required=True)
    init.add_argument("--start-ms", type=int, required=True)
    init.add_argument("--max-failures", type=int, default=3)
    sync = sub.add_parser("sync")
    sync.add_argument("archive", type=Path)
    sync.add_argument("--pages", type=int, default=3)
    sync.add_argument("--loop", action="store_true")
    sync.add_argument("--poll-seconds", type=int, default=60)
    show = sub.add_parser("status")
    show.add_argument("archive", type=Path)
    show.add_argument("--expected-head")
    show.add_argument("--max-age-ms", type=int, default=180_000)
    recover = sub.add_parser("resume")
    recover.add_argument("archive", type=Path)
    recover.add_argument("--expected-head", required=True)
    save = sub.add_parser("backup")
    save.add_argument("archive", type=Path)
    save.add_argument("destination", type=Path)
    export = sub.add_parser("export")
    export.add_argument("archive", type=Path)
    export.add_argument("--count", type=int, required=True)
    export.add_argument("--end-open-ms", type=int)
    export.add_argument("--template", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            create(args.archive, args.symbol, args.interval, args.start_ms, args.max_failures)
            result = status(args.archive)
        elif args.command == "sync":
            require(60 <= args.poll_seconds <= 86_400, "invalid_poll_interval")
            with owner(args.archive):
                while True:
                    outcome = sync_once(args.archive, pages=args.pages)
                    result = status(args.archive)
                    print(canonical(result), flush=True)
                    if outcome == "halted" or not args.loop:
                        return 2 if outcome in ("halted", "degraded") else 0
                    delay = min(3600, args.poll_seconds * 2 ** result["consecutive_failures"])
                    time.sleep(max(args.poll_seconds, delay))
        elif args.command == "status":
            result = status(
                args.archive, max_age_ms=args.max_age_ms, expected_head=args.expected_head
            )
            print(canonical(result))
            return 0 if result["capture_current"] else 2
        elif args.command == "resume":
            resume(args.archive, args.expected_head)
            result = status(args.archive)
        elif args.command == "backup":
            result = backup(args.archive, args.destination)
        else:
            template = read_template(args.template) if args.template else None
            result = export_window(
                args.archive, args.count, end_open_ms=args.end_open_ms, template=template
            )
        print(canonical(result))
        return 0
    except KeyboardInterrupt:
        return 130
    except (ArchiveError, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        reason = exc.reason if isinstance(exc, ArchiveError) else "archive_operation_failed"
        parser.exit(2, f"Public history refused: {reason}\n")


if __name__ == "__main__":
    raise SystemExit(main())
