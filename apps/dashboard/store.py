"""Read-only projections of the paper journal; never migrate or expose payloads."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import sqlite3

ACTIVE = {"reserved", "submitting", "unknown", "accepted", "partially_filled", "cancel_pending"}
TERMINAL = {"cancelled", "rejected", "filled"}
STATUSES = ACTIVE | TERMINAL
RECONCILE = {"submitting", "unknown", "cancel_pending"}


class JournalUnavailable(ValueError):
    """Safe public error: no filesystem paths, SQL or account payloads."""


def _text(value, maximum=128):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid text field")
    return value


def _amount(value):
    if not isinstance(value, str):
        raise ValueError("amount must be stored as text")
    result = Decimal(value)
    if not result.is_finite() or result < 0 or result > Decimal("1e18"):
        raise ValueError("invalid reservation")
    if result.as_tuple().exponent < -18 or len(result.as_tuple().digits) > 37:
        raise ValueError("unsupported precision")
    return result


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
        if path is None or not Path(path).is_file():
            raise JournalUnavailable("journal_unavailable")
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)) as db, localcontext() as context:
            context.prec = 64
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "orders" not in tables:
                raise ValueError("orders table absent")
            columns = {r[1] for r in db.execute("PRAGMA table_info(orders)")}
            if not {"client_id", "status", "reserved", "updated_at"} <= columns:
                raise ValueError("incompatible schema")
            source = "legacy_paper_journal"
            schema_version = 1
            if "order_journal_meta" in tables:
                meta = dict(db.execute("SELECT key,value FROM order_journal_meta"))
                if meta.get("schema_version") != "2" or meta.get("source") not in {"paper_journal", "synthetic_demo"}:
                    raise ValueError("unsupported journal metadata")
                source, schema_version = meta["source"], 2
            fields = ["client_id", "status", "reserved", "updated_at"]
            fields += [f for f in ("payload", "original_notional") if f in columns]
            counts = {key: 0 for key in sorted(STATUSES)}
            totals = {}
            projected = []
            latest = None
            # Decimal arithmetic rather than SQLite floating-point SUM(reserved).
            for row in db.execute("SELECT " + ",".join(fields) + " FROM orders ORDER BY julianday(updated_at) DESC,client_id"):
                key, state = _text(row["client_id"]), row["status"]
                if state not in STATUSES:
                    raise ValueError("unknown status")
                amount = _amount(row["reserved"])
                if state in TERMINAL and amount != 0:
                    raise ValueError("terminal reservation")
                updated = _text(row["updated_at"], 64)
                datetime.fromisoformat(updated.replace("Z", "+00:00"))
                latest = latest or updated
                payload = json.loads(row["payload"]) if "payload" in columns else {}
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
                    projected.append({"client_id": key, "status": state, "reserved": str(amount),
                                      "currency": currency, "symbol": symbol, "updated_at": updated})
            total = len(projected)
            events = []
            audit_available = "order_events" in tables
            if schema_version == 2 and not audit_available:
                raise ValueError("audit table absent")
            if audit_available:
                query = "SELECT id,client_id,kind,from_status,to_status,reserved,occurred_at FROM order_events"
                args = ()
                if event_client_id is not None:
                    query += " WHERE client_id=?"
                    args = (_text(event_client_id),)
                query += " ORDER BY id DESC LIMIT 50"
                for row in db.execute(query, args):
                    event = dict(row)
                    if event["to_status"] not in STATUSES or event["from_status"] not in STATUSES | {None}:
                        raise ValueError("invalid audit state")
                    _amount(event["reserved"])
                    _text(event["client_id"])
                    events.append(event)
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
                "orders": projected[offset:offset + limit],
                "pagination": {"limit": limit, "offset": offset, "total": total,
                               "has_more": offset + limit < total},
                "audit_available": audit_available, "events": events,
            }
    except (sqlite3.Error, OSError, ValueError, TypeError, InvalidOperation, OverflowError) as error:
        raise JournalUnavailable("journal_unavailable") from error
