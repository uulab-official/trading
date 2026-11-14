"""Read-only projections of the paper journal; never migrate or expose payloads."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import sqlite3
import time

ACTIVE = {"reserved", "submitting", "unknown", "accepted", "partially_filled", "cancel_pending"}
TERMINAL = {"cancelled", "rejected", "filled"}
STATUSES = ACTIVE | TERMINAL
RECONCILE = {"submitting", "unknown", "cancel_pending"}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ORDERS = 10_000
MAX_PAYLOAD_BYTES = 16_384
MAX_METADATA = 16
MAX_VM_OPERATIONS = 1_000_000
MAX_WORK_SECONDS = 1.0


class JournalUnavailable(ValueError):
    """Safe public error: no filesystem paths, SQL or account payloads."""


def _text(value, maximum=128):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid text field")
    return value


def _amount(value):
    _text(value)
    result = Decimal(value)
    if not result.is_finite() or result < 0 or result > Decimal("1e18"):
        raise ValueError("invalid reservation")
    if result.as_tuple().exponent < -18 or len(result.as_tuple().digits) > 37:
        raise ValueError("unsupported precision")
    return result


def _bounded_column(name, maximum):
    """Do not transfer an oversized SQLite cell into Python or sort records."""
    # Preserve genuine NULL (allowed for from_status), but use an invalid text
    # type for overflows so they cannot masquerade as that optional value.
    return (f"CASE WHEN length(CAST({name} AS BLOB)) > {maximum} "
            f"THEN 0 ELSE {name} END AS {name}")


def _check_files(path):
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            size = candidate.stat().st_size
        except FileNotFoundError:
            if candidate == path:
                raise
            continue
        if size > MAX_FILE_BYTES:
            raise ValueError("journal file limit")


def read_snapshot(path, *, limit=100, offset=0, status=None, event_client_id=None):
    """All totals, rows and audit records share one SQLite read transaction.

    Only a caller-supplied local path is used. HTTP query strings cannot select a
    database. No raw order payload, account key, path or broker secret is returned.
    """
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError("invalid pagination")
    if status is not None and status not in STATUSES:
        raise ValueError("invalid status")
    try:
        deadline = time.monotonic() + MAX_WORK_SECONDS
        def check_work():
            if time.monotonic() >= deadline:
                raise ValueError("journal work limit")

        if path is None or not Path(path).is_file():
            raise JournalUnavailable("journal_unavailable")
        path = Path(path).resolve()
        _check_files(path)
        check_work()
        uri = path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)) as db, localcontext() as context:
            context.prec = 64
            db.row_factory = sqlite3.Row
            vm_operations = 0
            def progress():
                nonlocal vm_operations
                vm_operations += 1000
                return int(vm_operations >= MAX_VM_OPERATIONS or time.monotonic() >= deadline)
            db.set_progress_handler(progress, 1000)
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                             "AND name IN ('orders','order_journal_meta','order_events')")}
            if "orders" not in tables:
                raise ValueError("orders table absent")
            columns = {r[1] for r in db.execute("PRAGMA table_info(orders)")}
            if not {"client_id", "status", "reserved", "updated_at"} <= columns:
                raise ValueError("incompatible schema")
            source = "legacy_paper_journal"
            schema_version = 1
            if "order_journal_meta" in tables:
                meta = {}
                query = "SELECT " + ",".join(_bounded_column(f, 512) for f in ("key", "value"))
                for index, row in enumerate(db.execute(query + " FROM order_journal_meta LIMIT 17")):
                    check_work()
                    if index >= MAX_METADATA:
                        raise ValueError("journal metadata limit")
                    meta[_text(row["key"])] = _text(row["value"])
                if meta.get("schema_version") != "2" or meta.get("source") not in {"paper_journal", "synthetic_demo"}:
                    raise ValueError("unsupported journal metadata")
                source, schema_version = meta["source"], 2
            # Count only up to the sentinel; never sort/validate an unbounded table.
            size = db.execute("SELECT COUNT(*) FROM (SELECT 1 FROM orders LIMIT 10001)").fetchone()[0]
            if size > MAX_ORDERS:
                raise ValueError("journal order limit")
            fields = [_bounded_column("client_id", 512), _bounded_column("status", 128),
                      _bounded_column("reserved", 512), _bounded_column("updated_at", 256)]
            if "payload" in columns:
                # BLOB casts use the database encoding: UTF-16 storage can be
                # twice the UTF-8 size. Enforce the exact UTF-8 bound below.
                fields.append(_bounded_column("payload", MAX_PAYLOAD_BYTES * 2))
            counts = {key: 0 for key in sorted(STATUSES)}
            totals = {}
            projected = []
            total = 0
            latest = None
            # Decimal arithmetic rather than SQLite floating-point SUM(reserved).
            for row in db.execute("SELECT " + ",".join(fields) + " FROM orders ORDER BY julianday(updated_at) DESC,client_id"):
                check_work()
                key, state = _text(row["client_id"]), row["status"]
                if state not in STATUSES:
                    raise ValueError("unknown status")
                amount = _amount(row["reserved"])
                if state in TERMINAL and amount != 0:
                    raise ValueError("terminal reservation")
                updated = _text(row["updated_at"], 64)
                datetime.fromisoformat(updated.replace("Z", "+00:00"))
                latest = latest or updated
                if "payload" in columns:
                    raw_payload = row["payload"]
                    if not isinstance(raw_payload, str) or len(raw_payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                        raise ValueError("journal payload limit")
                    payload = json.loads(raw_payload)
                    check_work()
                else:
                    payload = {}
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
                currency = payload.get("currency", "UNSPECIFIED")
                if not isinstance(currency, str) or not currency.isascii() or not currency.isalpha() or not 2 <= len(currency) <= 16:
                    raise ValueError("invalid currency")
                currency = currency.upper()
                symbol = payload.get("symbol")
                if symbol is not None:
                    symbol = _text(symbol)
                counts[state] += 1
                if state in ACTIVE:
                    totals[currency] = totals.get(currency, Decimal("0")) + amount
                if status is None or state == status:
                    if offset <= total < offset + limit:
                        projected.append({"client_id": key, "status": state, "reserved": str(amount),
                                          "currency": currency, "symbol": symbol, "updated_at": updated})
                    total += 1
            events = []
            audit_available = "order_events" in tables
            if schema_version == 2 and not audit_available:
                raise ValueError("audit table absent")
            if audit_available:
                query = "SELECT " + ",".join(_bounded_column(field, maximum) for field, maximum in (
                    ("id", 128), ("client_id", 512), ("kind", 512), ("from_status", 128), ("to_status", 128),
                    ("reserved", 512), ("occurred_at", 256))) + " FROM order_events"
                args = ()
                if event_client_id is not None:
                    query += " WHERE client_id=?"
                    args = (_text(event_client_id),)
                query += " ORDER BY order_events.id DESC LIMIT 50"
                for row in db.execute(query, args):
                    check_work()
                    event = dict(row)
                    if type(event["id"]) is not int or event["id"] < 1:
                        raise ValueError("invalid audit identifier")
                    if event["to_status"] not in STATUSES or event["from_status"] not in STATUSES | {None}:
                        raise ValueError("invalid audit state")
                    _amount(event["reserved"])
                    _text(event["client_id"])
                    _text(event["kind"])
                    _text(event["occurred_at"], 64)
                    events.append(event)
            _check_files(path)
            check_work()
            return {
                "mode": "paper", "read_only": True, "live_enabled": False,
                "journal_connected": True, "accounting_available": False,
                "source": source, "schema_version": schema_version,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "summary": {"total_orders": sum(counts.values()),
                            "active_orders": sum(counts[s] for s in ACTIVE),
                            "needs_reconciliation": sum(counts[s] for s in RECONCILE),
                            "reserved_by_currency": {k: str(v) for k, v in sorted(totals.items())},
                            "status_counts": counts, "last_order_update": latest},
                "orders": projected,
                "pagination": {"limit": limit, "offset": offset, "total": total,
                               "has_more": offset + limit < total},
                "audit_available": audit_available, "events": events,
            }
    except (sqlite3.Error, OSError, ValueError, TypeError, InvalidOperation, OverflowError,
            RecursionError, UnicodeError) as error:
        raise JournalUnavailable("journal_unavailable") from error
