"""Transactional paper-admission journal. No broker calls or fill accounting.

One journal represents one reservation currency. A cancelled intent keeps its
immutable notional; unknown outcomes never release capacity or permit resubmit.
"""
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, localcontext
from pathlib import Path

ACTIVE = frozenset({"reserved", "submitting", "unknown", "accepted",
                    "partially_filled", "cancel_pending"})
TERMINAL = frozenset({"cancelled", "rejected", "filled"})
RECONCILE = frozenset({"submitting", "unknown", "cancel_pending"})
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


def _amount(value, *, positive=False):
    # Fixed bounds keep aggregation exact even if a caller changes Decimal context.
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("amount must be a finite Decimal")
    if value < 0 or (positive and value == 0) or value > Decimal("1e18"):
        raise ValueError("amount is outside the supported range")
    if value.as_tuple().exponent < -18 or len(value.as_tuple().digits) > 37:
        raise ValueError("amount has unsupported precision")
    return value


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid order identifier")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid order identifier")
    return value


def _payload(value):
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid order payload") from error
    if len(encoded.encode("utf-8")) > 16384:
        raise ValueError("payload is too large")
    currency = value.get("currency", "UNSPECIFIED")
    if not isinstance(currency, str) or not currency.isascii() or not currency.isalpha() or not 2 <= len(currency) <= 16:
        raise ValueError("currency must be a short alphabetic code")
    return encoded, currency.upper()


class OrderJournal:
    def __init__(self, path: str, *, source=None):
        if source not in (None, "paper_journal", "synthetic_demo"):
            raise ValueError("unsupported journal source")
        self.path = str(Path(path))
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            self.db.execute("PRAGMA busy_timeout=10000")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self._migrate(source)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _migrate(self, source):
        with self.transaction():
            self.db.execute("""CREATE TABLE IF NOT EXISTS orders (
                client_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL, reserved TEXT NOT NULL, venue_id TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                original_notional TEXT
            )""")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(orders)")}
            required = {"client_id", "payload", "status", "reserved", "venue_id", "updated_at"}
            if not required <= columns:
                raise ValueError("incompatible journal schema")
            self.db.execute("CREATE TABLE IF NOT EXISTS order_journal_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            version = self.db.execute("SELECT value FROM order_journal_meta WHERE key='schema_version'").fetchone()
            if version and version[0] != "2":
                raise ValueError("unsupported journal version")
            if "original_notional" not in columns:
                self.db.execute("ALTER TABLE orders ADD COLUMN original_notional TEXT")
                # A terminal legacy row has lost its original amount. Do not guess it.
                for key, status, reserved in self.db.execute("SELECT client_id,status,reserved FROM orders").fetchall():
                    if status in ACTIVE - {"partially_filled"}:
                        amount = _amount(Decimal(reserved), positive=True)
                        self.db.execute("UPDATE orders SET original_notional=? WHERE client_id=?", (str(amount), key))
            self.db.execute(f"""CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL REFERENCES orders(client_id),
                kind TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
                reserved TEXT NOT NULL, occurred_at TEXT NOT NULL DEFAULT ({NOW})
            )""")
            self.db.execute("CREATE INDEX IF NOT EXISTS order_events_client ON order_events(client_id,id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS orders_updated ON orders(updated_at,client_id)")
            existing_source = self.db.execute("SELECT value FROM order_journal_meta WHERE key='source'").fetchone()
            if existing_source and source is not None and existing_source[0] != source:
                raise ValueError("journal source cannot be changed")
            self.db.execute("INSERT OR IGNORE INTO order_journal_meta VALUES('source',?)", (source or "paper_journal",))
            self.db.execute("INSERT OR IGNORE INTO order_journal_meta VALUES('schema_version','2')")
            for key, status, reserved in self.db.execute("SELECT client_id,status,reserved FROM orders WHERE NOT EXISTS (SELECT 1 FROM order_events e WHERE e.client_id=orders.client_id)").fetchall():
                self._event(key, "migration", None, status, reserved)
            self.reserved_total()  # Reject corrupt migrated amounts/statuses, atomically.

    def _event(self, key, kind, before, after, reserved):
        self.db.execute("INSERT INTO order_events(client_id,kind,from_status,to_status,reserved) VALUES(?,?,?,?,?)",
                        (key, kind, before, after, str(reserved)))

    def reserve(self, client_id: str, payload: dict, notional: Decimal, max_reserved: Decimal):
        """Atomically reserve capacity; callers still enforce account/fees/risk.

        Replaying an immutable intent returns its existing state, not a new order.
        Legacy terminal intents without a recoverable original amount fail closed.
        """
        _identifier(client_id)
        _amount(notional, positive=True)
        _amount(max_reserved)
        encoded, currency = _payload(payload)
        with self.transaction(), localcontext() as context:
            context.prec = 64
            previous = self.db.execute("SELECT payload,status,original_notional FROM orders WHERE client_id=?", (client_id,)).fetchone()
            if previous:
                if previous[1] not in ACTIVE | TERMINAL:
                    raise ValueError("unknown journal status")
                if previous[2] is None:
                    raise ValueError("legacy order identity requires reconciliation")
                if previous[0] != encoded or _amount(Decimal(previous[2]), positive=True) != notional:
                    raise ValueError("idempotency key reused with different payload or amount")
                return previous[1]
            # A scalar max_reserved cannot compare KRW to USDT. Use separate journals.
            for row in self.db.execute("SELECT payload FROM orders"):
                if _payload(json.loads(row[0]))[1] != currency:
                    raise ValueError("use one reservation currency per journal")
            if self.reserved_total() + notional > max_reserved:
                raise ValueError("reservation capacity exceeded")
            self.db.execute(f"INSERT INTO orders(client_id,payload,status,reserved,original_notional,updated_at) VALUES(?,?,'reserved',?,?,{NOW})",
                            (client_id, encoded, str(notional), str(notional)))
            self._event(client_id, "reserve", None, "reserved", notional)
            return "reserved"

    def _transition(self, key, target, allowed, *, venue_id=None, idempotent=True):
        _identifier(key)
        if venue_id is not None:
            _identifier(venue_id)
        with self.transaction():
            row = self.db.execute("SELECT status,reserved,venue_id FROM orders WHERE client_id=?", (key,)).fetchone()
            if row is None:
                raise ValueError("unknown order")
            current, reserved, existing_venue = row
            amount = _amount(Decimal(reserved))
            if venue_id is not None and existing_venue is not None and venue_id != existing_venue:
                raise ValueError("conflicting venue order identifier")
            if current in TERMINAL and amount != 0:
                raise ValueError("terminal order still reserves capacity")
            if current == target and idempotent:
                if venue_id is not None and existing_venue is None:
                    self.db.execute(f"UPDATE orders SET venue_id=?,updated_at={NOW} WHERE client_id=?", (venue_id, key))
                    self._event(key, "reconciliation", current, target, amount)
                return
            if current not in allowed:
                raise ValueError(f"invalid transition: {current} -> {target}")
            if target in TERMINAL:
                amount = Decimal("0")
            self.db.execute(f"UPDATE orders SET status=?,reserved=?,venue_id=?,updated_at={NOW} WHERE client_id=?",
                            (target, str(amount), venue_id or existing_venue, key))
            self._event(key, "transition", current, target, amount)

    def mark_submitting(self, client_id: str):
        # A repeated submission is not an acknowledgement and is NOT idempotent.
        self._transition(client_id, "submitting", {"reserved"}, idempotent=False)

    def mark_unknown(self, client_id: str):
        self._transition(client_id, "unknown", {"submitting", "cancel_pending"})

    def confirm_accepted(self, client_id: str, venue_order_id: str):
        """Record externally verified acknowledgement; does not contact a broker."""
        _identifier(venue_order_id)
        self._transition(client_id, "accepted", {"submitting", "unknown"}, venue_id=venue_order_id)

    def request_cancel(self, client_id: str):
        self._transition(client_id, "cancel_pending", {"accepted"})

    def confirm_cancelled(self, client_id: str):
        """Release only unfilled intents after verified cancel (or local unsent cancel)."""
        self._transition(client_id, "cancelled", {"reserved", "unknown", "accepted", "cancel_pending"})

    def confirm_rejected(self, client_id: str):
        """A timeout is never proof of rejection."""
        self._transition(client_id, "rejected", {"reserved", "submitting", "unknown"})

    def pending_reconciliation(self):
        return self.db.execute("SELECT client_id,status FROM orders WHERE status IN ('submitting','unknown','cancel_pending') ORDER BY client_id").fetchall()

    def reserved_total(self):
        with localcontext() as context:
            context.prec = 64
            total = Decimal("0")
            for status, reserved in self.db.execute("SELECT status,reserved FROM orders"):
                if status not in ACTIVE | TERMINAL:
                    raise ValueError("unknown journal status")
                value = _amount(Decimal(reserved))
                if status in TERMINAL and value != 0:
                    raise ValueError("terminal order still reserves capacity")
                if status in ACTIVE:
                    total += value
            return total

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
