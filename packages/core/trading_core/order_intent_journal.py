"""Durable order-intent journal for future BROKER SANDBOX integration.

This module contains NO HTTP/API client and never submits or retries an order.
An ambiguous order outcome must be reconciled against independent broker
evidence, never guessed from a network timeout.
"""
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sqlite3


FINAL = frozenset(("filled", "canceled", "rejected"))
PENDING = frozenset(("reserved", "uncertain"))


def _quantity(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ValueError("Quantity must be a finite positive decimal") from error
    if not number.is_finite() or number <= 0:
        raise ValueError("Quantity must be a finite positive decimal")
    # normalize() applies the ambient decimal precision and exponent limits,
    # which can round distinct order quantities into the same intent value.
    # Strip coefficient zeros without arithmetic, then render every digit.
    sign, digits, exponent = number.as_tuple()
    end = len(digits)
    while digits[end - 1] == 0:
        end -= 1
    canonical = Decimal((sign, digits[:end], exponent + len(digits) - end))
    # Explicit G also keeps exponent casing independent of context.capitals.
    return format(canonical, "G")


def _identifier(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        raise ValueError(label + " must be 1..120 characters")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError(label + " contains unsupported characters")
    return value


class OrderIntentJournal:
    """SQLite WAL journal, single-writer transaction, no broker adapter."""

    def __init__(self, path):
        self._read_only = False
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Journal must be a regular file")
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS intents (
                    client_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('reserved','uncertain','reconciled')),
                    outcome TEXT,
                    evidence_ref TEXT,
                    broker_order_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);
                -- A broker execution ID must never be treated as evidence
                -- for two distinct local client intents.
                CREATE UNIQUE INDEX IF NOT EXISTS ux_intents_broker_order
                    ON intents(broker_order_id)
                    WHERE broker_order_id IS NOT NULL;
            """)

    @classmethod
    def open_existing_readonly(cls, path):
        """Open an existing journal for reads without creating a missing file.

        Mutating methods raise PermissionError before opening a connection.
        """
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError("Existing SQLite order intent journal required")
        instance = object.__new__(cls)
        instance.path = resolved
        instance._read_only = True
        return instance

    def _connect(self, *, read_only=False):
        if self._read_only or read_only:
            # No implicit schema creation, data mutations, or journal
            # mode writes during a safety preflight.
            con = sqlite3.connect(self.path.as_uri() + "?mode=ro",
                                  uri=True, timeout=5.0, isolation_level=None)
            con.execute("PRAGMA query_only=ON")
        else:
            con = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            # Set busy handling before attempting the WAL mode transition
            # so competing writers wait rather than fail spuriously.
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
        con.row_factory = sqlite3.Row
        return con

    def _require_writable(self):
        if self._read_only:
            raise PermissionError("Read-only order intent journal cannot be modified")

    @staticmethod
    def _row(row):
        return dict(row) if row is not None else None

    def reserve(self, client_id, symbol, side, quantity):
        """Commit intent first; caller must never infer permission to send.

        Duplicate exact IDs return existing state without submitting an order.
        Distinct new intents are blocked while any unresolved intent exists.
        """
        self._require_writable()
        client_id = _identifier(client_id, "client_id")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,20}/(?:KRW|USDT)", symbol):
            raise ValueError("Journal supports explicit KRW/USDT spot symbols only")
        base, quote = symbol.split("/")
        if base == quote:
            raise ValueError("Spot base and quote currencies must differ")
        if side not in ("buy", "sell"):
            raise ValueError("Invalid spot order side")
        qty = _quantity(quantity)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                old = db.execute("SELECT * FROM intents WHERE client_id=?", (client_id,)).fetchone()
                if old is not None:
                    if (old["symbol"], old["side"], old["quantity"]) != (symbol, side, qty):
                        raise ValueError("Client intent ID reused with different order details")
                    db.execute("COMMIT")
                    return self._row(old)
                active = db.execute(
                    "SELECT client_id FROM intents WHERE status IN ('reserved','uncertain') LIMIT 1"
                ).fetchone()
                if active is not None:
                    raise ValueError("Unresolved broker intent blocks new order intents")
                db.execute(
                    "INSERT INTO intents (client_id,symbol,side,quantity,status) VALUES (?,?,?,?,?)",
                    (client_id, symbol, side, qty, "reserved"),
                )
                inserted = db.execute(
                    "SELECT * FROM intents WHERE client_id=?", (client_id,)
                ).fetchone()
                db.execute("COMMIT")
                return self._row(inserted)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def mark_uncertain(self, client_id):
        """Ambiguous submission/ACK: never automatically retry or erase."""
        self._require_writable()
        client_id = _identifier(client_id, "client_id")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT status FROM intents WHERE client_id=?", (client_id,)).fetchone()
                if row is None or row["status"] == "reconciled":
                    raise ValueError("Cannot mark missing or reconciled intent uncertain")
                db.execute("UPDATE intents SET status='uncertain' WHERE client_id=?", (client_id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return self.get(client_id)

    def reconcile(self, client_id, *, outcome, evidence_ref, broker_order_id=None):
        """Record EXTERNAL verified evidence; does not query broker itself."""
        self._require_writable()
        client_id = _identifier(client_id, "client_id")
        if outcome not in FINAL:
            raise ValueError("Outcome must be filled, canceled or rejected")
        evidence_ref = _identifier(evidence_ref, "evidence_ref")
        if broker_order_id is not None:
            broker_order_id = _identifier(broker_order_id, "broker_order_id")
        if outcome == "filled" and not broker_order_id:
            raise ValueError("Filled orders require a confirmed broker order ID")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM intents WHERE client_id=?", (client_id,)).fetchone()
                if row is None:
                    raise ValueError("Cannot reconcile an unknown client intent")
                if row["status"] == "reconciled":
                    if (row["outcome"], row["evidence_ref"], row["broker_order_id"]) != (
                        outcome, evidence_ref, broker_order_id
                    ):
                        raise ValueError("Final broker evidence is immutable")
                else:
                    db.execute(
                        "UPDATE intents SET status='reconciled', outcome=?, evidence_ref=?, "
                        "broker_order_id=? WHERE client_id=?",
                        (outcome, evidence_ref, broker_order_id, client_id),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return self.get(client_id)

    def get(self, client_id):
        client_id = _identifier(client_id, "client_id")
        with closing(self._connect(read_only=True)) as db:
            return self._row(db.execute(
                "SELECT * FROM intents WHERE client_id=?", (client_id,)
            ).fetchone())

    def unresolved(self):
        with closing(self._connect(read_only=True)) as db:
            return [self._row(row) for row in db.execute(
                "SELECT * FROM intents WHERE status IN ('reserved','uncertain') ORDER BY client_id"
            ).fetchall()]

    def audit_integrity(self):
        """Read-only preflight: reject corrupt or contradictory journal state.

        This is not proof broker evidence is authentic. Never submit orders
        based only on this local database report.
        """
        with closing(self._connect(read_only=True)) as db:
            # Keep a consistent SQLite read snapshot while validating both
            # the storage integrity and the order records.
            db.execute("BEGIN")
            try:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite integrity check failed")
                records = [dict(row) for row in db.execute(
                    "SELECT * FROM intents ORDER BY client_id"
                ).fetchall()]
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        pending = []
        order_ids = set()
        for row in records:
            _identifier(row["client_id"], "client_id")
            if not re.fullmatch(r"[A-Z0-9]{2,20}/(?:KRW|USDT)", row["symbol"]):
                raise ValueError("Unrecognized journal symbol")
            asset, quoted = row["symbol"].split("/")
            if asset == quoted:
                raise ValueError("Invalid self-quoted spot symbol")
            if row["side"] not in ("buy", "sell"):
                raise ValueError("Unrecognized journal side")
            if _quantity(row["quantity"]) != row["quantity"]:
                raise ValueError("Noncanonical journal quantity")
            if row["status"] in PENDING:
                if any(row[field] is not None for field in
                       ("outcome", "evidence_ref", "broker_order_id")):
                    raise ValueError("Unresolved intent carries final evidence")
                pending.append(row["client_id"])
            elif row["status"] == "reconciled":
                if row["outcome"] not in FINAL:
                    raise ValueError("Reconciled intent lacks final outcome")
                _identifier(row["evidence_ref"], "evidence_ref")
                order_id = row["broker_order_id"]
                if row["outcome"] == "filled" and not order_id:
                    raise ValueError("Filled intent lacks broker order ID")
                if order_id is not None:
                    _identifier(order_id, "broker_order_id")
                    if order_id in order_ids:
                        raise ValueError("Duplicate broker order ID across intents")
                    order_ids.add(order_id)
            else:
                raise ValueError("Unknown broker intent status")
        if len(pending) > 1:
            raise ValueError("Multiple unresolved broker intents detected")
        return {
            "status": "local_intent_integrity_verified",
            "live_orders_approved": False,
            "intent_count": len(records),
            "unresolved_count": len(pending),
            "new_intents_permitted": len(pending) == 0,
            "warning": "Local state verification does not validate external broker evidence",
        }
