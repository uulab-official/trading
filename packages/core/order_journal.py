"""SQLite-backed paper order journal; no live broker access.

Single-writer transaction model. Unknown submission outcomes require reconciliation.
"""
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path


class OrderJournal:
    def __init__(self, path: str):
        self.path = str(Path(path))
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS orders (
            client_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
            status TEXT NOT NULL, reserved TEXT NOT NULL,
            venue_id TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reserve(self, client_id: str, payload: dict, notional: Decimal, max_reserved: Decimal):
        """Reserve an order atomically; caller must independently validate risk."""
        if not client_id or not notional.is_finite() or notional <= 0:
            raise ValueError("invalid order reservation")
        if not max_reserved.is_finite() or max_reserved < 0:
            raise ValueError("invalid capacity")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.transaction():
            previous = self.db.execute(
                "SELECT payload,status,reserved FROM orders WHERE client_id=?", (client_id,)
            ).fetchone()
            if previous:
                if previous[0] != encoded or (previous[1] != "cancelled" and Decimal(previous[2]) != notional):
                    raise ValueError("idempotency key reused with different payload")
                return previous[1]
            rows = self.db.execute(
                "SELECT reserved FROM orders WHERE status IN ('reserved','submitting','unknown','accepted','partially_filled','cancel_pending')"
            ).fetchall()
            total = sum((Decimal(row[0]) for row in rows), Decimal("0"))
            if total + notional > max_reserved:
                raise ValueError("reservation capacity exceeded")
            self.db.execute(
                "INSERT INTO orders(client_id,payload,status,reserved) VALUES(?,?,?,?)",
                (client_id, encoded, "reserved", str(notional)),
            )
            return "reserved"

    def mark_submitting(self, client_id: str):
        with self.transaction():
            cur = self.db.execute(
                "UPDATE orders SET status='submitting',updated_at=CURRENT_TIMESTAMP WHERE client_id=? AND status='reserved'",
                (client_id,),
            )
            if cur.rowcount != 1:
                raise ValueError("order is not reserved")

    def mark_unknown(self, client_id: str):
        with self.transaction():
            cur = self.db.execute(
                "UPDATE orders SET status='unknown',updated_at=CURRENT_TIMESTAMP WHERE client_id=? AND status='submitting'",
                (client_id,),
            )
            if cur.rowcount != 1:
                raise ValueError("order is not submitting")

    def confirm_cancelled(self, client_id: str):
        """Only call after broker cancellation has been independently confirmed."""
        with self.transaction():
            cur = self.db.execute(
                "UPDATE orders SET status='cancelled',reserved='0',updated_at=CURRENT_TIMESTAMP WHERE client_id=? AND status IN ('reserved','unknown','accepted','cancel_pending')",
                (client_id,),
            )
            if cur.rowcount != 1:
                raise ValueError("cancellation requires reconciliation")

    def pending_reconciliation(self):
        return self.db.execute(
            "SELECT client_id,status FROM orders WHERE status IN ('submitting','unknown','cancel_pending') ORDER BY client_id"
        ).fetchall()

    def reserved_total(self):
        rows = self.db.execute(
            "SELECT reserved FROM orders WHERE status IN ('reserved','submitting','unknown','accepted','partially_filled','cancel_pending')"
        ).fetchall()
        return sum((Decimal(row[0]) for row in rows), Decimal("0"))

    def close(self):
        self.db.close()
