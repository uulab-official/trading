"""Durable, offline stock-order simulation with exact integer KRW deltas.

Caller-supplied events are unverified simulation inputs, never authenticated
broker evidence. This module has no transport, credentials, balances or link to
another cash ledger. A valid replay does not authorize sending an order.

The hash chain and committed tail detect accidental/partial history edits. They
are not signatures and cannot defeat a coordinated, valid rewrite of the file.
"""

from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

if __package__:
    from .kis_vts_order import MAX_QUANTITY, build_order_plan
else:  # Standalone Python -S operator tools must not import package initializers.
    from kis_vts_order import MAX_QUANTITY, build_order_plan


_FINAL = frozenset(("filled", "canceled", "rejected"))
_FIELDS = {
    "reserve": {"order_date", "symbol", "side", "quantity", "limit_price", "max_notional"},
    "submission_uncertain": set(),
    "acknowledge": {"broker_order_id", "broker_org_no"},
    "fill": {"execution_id", "quantity", "price", "fee_krw", "tax_krw"},
    "cancel_requested": set(),
    "canceled": {"evidence_id"},
    "rejected": {"evidence_id"},
}
_SCHEMA = {
    "journal_config": "CREATE TABLE journal_config ("
                      "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                      "config_json TEXT NOT NULL)",
    "journal_tail": "CREATE TABLE journal_tail ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                    "sequence INTEGER NOT NULL CHECK(sequence>=0), event_hash TEXT NOT NULL)",
    "events": "CREATE TABLE events ("
              "sequence INTEGER PRIMARY KEY CHECK(sequence>0), event_id TEXT NOT NULL UNIQUE, "
              "event_json TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL)",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _label(value, *, account=False):
    pattern = r"[A-Za-z][A-Za-z0-9._-]*" if account else r"[A-Za-z0-9][A-Za-z0-9._-]*"
    if type(value) is not str or not 1 <= len(value) <= 64 or re.fullmatch(pattern, value) is None:
        raise ValueError("A bounded ASCII correlation label is required")
    return value


def _krw(value, *, maximum, zero=False):
    pattern = r"0|[1-9][0-9]*" if zero else r"[1-9][0-9]*"
    if type(value) is not str or not 1 <= len(value) <= maximum or re.fullmatch(pattern, value) is None:
        raise ValueError("A bounded canonical integer KRW string is required")
    return int(value)


def _shares(value):
    if type(value) is not int or not 1 <= value <= MAX_QUANTITY:
        raise ValueError("A bounded positive integer share count is required")


def _digits(value, maximum):
    if type(value) is not str or not 1 <= len(value) <= maximum or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("A bounded ASCII broker identifier is required")


def _validate_event(event):
    if type(event) is not dict or set(event) != {"event_id", "kind", "client_order_id", "data"}:
        raise ValueError("Invalid stock-order event fields")
    _label(event["event_id"])
    _label(event["client_order_id"])
    kind, data = event["kind"], event["data"]
    if type(kind) is not str or kind not in _FIELDS:
        raise ValueError("Invalid stock-order event kind")
    if type(data) is not dict or set(data) != _FIELDS[kind]:
        raise ValueError("Invalid stock-order event data fields")
    if kind == "reserve":
        order_date = data["order_date"]
        if type(order_date) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", order_date) is None:
            raise ValueError("An ISO order date is required")
        try:
            date.fromisoformat(order_date)
        except ValueError:
            raise ValueError("An ISO order date is required") from None
        build_order_plan(client_order_id=event["client_order_id"],
                         **{key: value for key, value in data.items() if key != "order_date"})
    elif kind == "acknowledge":
        _digits(data["broker_order_id"], 10)
        _digits(data["broker_org_no"], 5)
        if int(data["broker_order_id"]) == 0:
            raise ValueError("Broker order identifier must be nonzero")
    elif kind == "fill":
        _label(data["execution_id"])
        _shares(data["quantity"])
        _krw(data["price"], maximum=19)
        _krw(data["fee_krw"], maximum=29, zero=True)
        _krw(data["tax_krw"], maximum=29, zero=True)
    elif kind in ("canceled", "rejected"):
        _label(data["evidence_id"])
    # Validation admits only bounded immutable scalar values; detach the dicts.
    return {**event, "data": dict(data)}


def _strict_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate journal JSON field")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("Invalid journal JSON number")

    if type(body) is not str or len(body) > 16384:
        raise ValueError("Invalid journal JSON encoding")
    try:
        return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise ValueError("Invalid journal JSON encoding") from None


def _event_hash(sequence, previous, body):
    return hashlib.sha256((str(sequence) + "\n" + previous + "\n" + body).encode("ascii")).hexdigest()


class _Replay:
    def __init__(self, account_ref):
        self.account_ref = account_ref
        self.events = {}
        self.deliveries = set()
        self.orders = {}
        self.broker_orders = {}
        self.executions = {}

    def apply(self, event):
        identity, client, kind = event["event_id"], event["client_order_id"], event["kind"]
        body = _canonical(event)
        if identity in self.events:
            if self.events[identity] != body:
                raise ValueError("Event identifier reused with different data")
            return False
        delivery = (client, kind, _canonical(event["data"]))
        if delivery not in self.deliveries:
            self._reduce(client, kind, event["data"])
            self.deliveries.add(delivery)
        # A new event ID records delivery even when no economic/state change is needed.
        self.events[identity] = body
        return True

    def _reduce(self, client, kind, data):
        if kind == "reserve":
            if client in self.orders:
                raise ValueError("Client order identifier reused with different terms")
            if any(order["status"] not in _FINAL for order in self.orders.values()):
                raise ValueError("An unresolved order blocks new client intents")
            self.orders[client] = {
                "client_order_id": client, **data, "status": "reserved",
                "broker_order_id": None, "broker_org_no": None,
                "filled_quantity": 0, "gross_krw": 0, "fee_krw": 0,
                "tax_krw": 0, "cash_delta_krw": 0, "executions": [],
            }
            return
        order = self.orders.get(client)
        if order is None:
            raise ValueError("An existing client order is required")
        status = order["status"]
        if kind == "acknowledge":
            if order["broker_order_id"] is not None:
                raise ValueError("Recorded broker order identity is immutable")
            if status not in ("reserved", "uncertain"):
                raise ValueError("Acknowledgment is invalid in this order state")
            # Preserve original zero padding, but do not allow padding aliases to
            # bind the same numerical broker identity to another local order.
            identity = (order["order_date"], int(data["broker_org_no"]), int(data["broker_order_id"]))
            if identity in self.broker_orders:
                raise ValueError("Broker order identity already belongs to another client order")
            self.broker_orders[identity] = client
            order.update(data, status="accepted")
        elif kind == "fill":
            self._fill(order, data)
        elif kind == "submission_uncertain":
            if status != "reserved":
                raise ValueError("Uncertain submission is invalid in this order state")
            order["status"] = "uncertain"
        elif kind == "cancel_requested":
            if status not in ("accepted", "partially_filled"):
                raise ValueError("Cancellation request requires a working acknowledged order")
            order["status"] = "cancel_pending"
        elif kind == "canceled":
            if status != "cancel_pending":
                raise ValueError("Cancellation confirmation requires a pending cancellation")
            order["status"] = "canceled"
        elif kind == "rejected":
            if status in _FINAL or order["filled_quantity"] != 0:
                raise ValueError("Rejection requires an unresolved order without fills")
            order["status"] = "rejected"

    def _fill(self, order, data):
        identity = (order["order_date"], data["execution_id"])
        economics = (order["client_order_id"], data["quantity"], data["price"],
                     data["fee_krw"], data["tax_krw"])
        if identity in self.executions:
            if self.executions[identity] != economics:
                raise ValueError("Execution identity reused with different order or economic terms")
            return
        if order["broker_order_id"] is None or order["status"] not in (
                "accepted", "partially_filled", "cancel_pending"):
            raise ValueError("New fills require an unresolved acknowledged order")
        quantity, price = data["quantity"], int(data["price"])
        if order["filled_quantity"] + quantity > order["quantity"]:
            raise ValueError("Execution quantity exceeds the remaining order quantity")
        limit = int(order["limit_price"])
        if (order["side"] == "buy" and price > limit) or (order["side"] == "sell" and price < limit):
            raise ValueError("Execution price violates the recorded limit price")
        gross, fee, tax = quantity * price, int(data["fee_krw"]), int(data["tax_krw"])
        cash = -gross - fee - tax if order["side"] == "buy" else gross - fee - tax
        self.executions[identity] = economics
        order["filled_quantity"] += quantity
        for key, value in (("gross_krw", gross), ("fee_krw", fee), ("tax_krw", tax), ("cash_delta_krw", cash)):
            order[key] += value
        order["executions"].append({**data, "gross_krw": str(gross), "cash_delta_krw": str(cash)})
        if order["filled_quantity"] == order["quantity"]:
            order["status"] = "filled"
        elif order["status"] != "cancel_pending":
            order["status"] = "partially_filled"

    def snapshot(self):
        orders, pending, positions = [], [], {}
        cash = 0
        for order in self.orders.values():
            remaining = order["quantity"] - order["filled_quantity"]
            terminal = order["status"] in _FINAL
            if not terminal:
                pending.append(order["client_order_id"])
            result = {**order, "unfilled_quantity": remaining,
                      "working_quantity": 0 if terminal else remaining,
                      "executions": [dict(execution) for execution in order["executions"]]}
            for key in ("gross_krw", "fee_krw", "tax_krw", "cash_delta_krw"):
                result[key] = str(order[key])
            orders.append(result)
            cash += order["cash_delta_krw"]
            if order["filled_quantity"]:
                change = order["filled_quantity"] * (1 if order["side"] == "buy" else -1)
                positions[order["symbol"]] = positions.get(order["symbol"], 0) + change
        return {
            "mode": "paper", "live_orders_enabled": False,
            "provenance": "synthetic_or_caller_supplied_unverified", "account_ref": self.account_ref,
            "currency": "KRW", "market": "KRX", "event_count": len(self.events),
            "execution_count": len(self.executions), "pending_client_order_ids": pending,
            "blocked": bool(pending), "cash_delta_krw": str(cash),
            "position_deltas": {symbol: quantity for symbol, quantity in sorted(positions.items()) if quantity},
            "orders": orders,
        }


class StockOrderJournal:
    """Account-alias-bound SQLite journal. Use create/open_existing factories.

    Every public read and append fully audits one consistent SQLite transaction.
    Connections are short-lived; journal instances hold no cached account state.
    """

    @classmethod
    def _instance(cls, path, account_ref, read_only):
        _label(account_ref, account=True)
        if type(read_only) is not bool:
            raise ValueError("Read-only mode must be a boolean")
        instance = object.__new__(cls)
        try:
            instance.path = Path(path).absolute()
        except (TypeError, ValueError):
            raise ValueError("A journal file path is required") from None
        instance.account_ref = account_ref
        instance._read_only = read_only
        return instance

    @classmethod
    def create(cls, path, *, account_ref):
        """Exclusively create a mode-0600 journal at an unused SQLite basename.

        Use an existing private directory: the caller controls its permissions.
        Reserved sidecar entries are refused, including dangling symlinks. The
        checks cannot prevent another directory writer from introducing paths
        after the final check or replacing the directory during initialization.
        """
        instance = cls._instance(path, account_ref, False)
        instance._require_unused_sidecars()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(instance.path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        # Catch entries introduced while reserving the main file, before any
        # SQLite connection can recover, replace or unlink reserved sidecars.
        instance._require_unused_sidecars()
        # A failed initialization retains the exclusively created file; never
        # unlink a pathname that another process might meanwhile have replaced.
        with instance._transaction(write=True, initialize=True) as db:
            for statement in _SCHEMA.values():
                db.execute(statement)
            config = instance._config()
            db.execute("INSERT INTO journal_config VALUES (1, ?)", (config,))
            db.execute("INSERT INTO journal_tail VALUES (1, 0, ?)", (instance._genesis(),))
            instance._replay(db)
        return instance

    def _require_unused_sidecars(self):
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                continue
            raise FileExistsError("Reserved SQLite sidecar path already exists")

    @classmethod
    def open_existing(cls, path, *, account_ref, read_only=False):
        """Audit an existing regular file without creating or repairing it."""
        instance = cls._instance(path, account_ref, read_only)
        instance.snapshot()
        return instance

    def _config(self):
        return _canonical({"schema_version": 1, "mode": "paper", "account_ref": self.account_ref,
                           "currency": "KRW", "market": "KRX"})

    def _genesis(self):
        return hashlib.sha256(("stock-order-journal-v1\n" + self._config()).encode("ascii")).hexdigest()

    def _connect(self, *, read_only):
        if not stat.S_ISREG(self.path.lstat().st_mode):
            raise ValueError("Journal must be an existing regular file without a final symlink")
        mode = "ro" if read_only or self._read_only else "rw"
        db = sqlite3.connect(self.path.as_uri() + "?mode=" + mode,
                             uri=True, timeout=5.0, isolation_level=None)
        try:
            # These connection-local settings do not mutate database contents.
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            if mode == "ro":
                db.execute("PRAGMA query_only=ON")
            return db
        except BaseException:
            db.close()
            raise

    @contextmanager
    def _transaction(self, *, write, initialize=False):
        db = None
        try:
            db = self._connect(read_only=not write)
            if initialize:
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except sqlite3.Error:
            raise ValueError("Invalid or unavailable stock-order journal") from None
        finally:
            if db is not None:
                try:
                    if db.in_transaction:
                        db.rollback()
                finally:
                    db.close()

    def _replay(self, db):
        if db.execute("PRAGMA journal_mode").fetchone() != ("wal",):
            raise ValueError("Journal requires WAL storage")
        if db.execute("PRAGMA synchronous").fetchone() != (2,):
            raise ValueError("Journal requires FULL synchronization")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Journal SQLite integrity check failed")
        schema = set(db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        expected = {("table", name, name, statement) for name, statement in _SCHEMA.items()}
        expected.add(("index", "sqlite_autoindex_events_1", "events", None))
        if schema != expected:
            raise ValueError("Journal schema does not match the supported version")
        if db.execute("SELECT singleton, config_json FROM journal_config").fetchall() != [(1, self._config())]:
            raise ValueError("Journal configuration or account alias does not match")
        replay, previous = _Replay(self.account_ref), self._genesis()
        for number, row in enumerate(db.execute(
                "SELECT sequence, event_id, event_json, prev_hash, event_hash FROM events ORDER BY sequence"), 1):
            sequence, identity, body, old_hash, stored_hash = row
            if type(sequence) is not int or sequence != number or old_hash != previous:
                raise ValueError("Journal event sequence or hash chain is inconsistent")
            event = _validate_event(_strict_json(body))
            if _canonical(event) != body or event["event_id"] != identity:
                raise ValueError("Journal event encoding is not canonical")
            digest = _event_hash(sequence, previous, body)
            if stored_hash != digest or not replay.apply(event):
                raise ValueError("Journal event hash or identity is inconsistent")
            previous = digest
        tail = db.execute("SELECT singleton, sequence, event_hash FROM journal_tail").fetchall()
        if tail != [(1, len(replay.events), previous)]:
            raise ValueError("Journal history does not match its committed tail")
        return replay, previous

    def record(self, event):
        """Atomically record a validated event, or return an exact ID redelivery."""
        if self._read_only:
            raise PermissionError("Read-only stock-order journal cannot be modified")
        event = _validate_event(event)
        with self._transaction(write=True) as db:
            replay, previous = self._replay(db)
            if replay.apply(event):
                sequence = len(replay.events)
                body = _canonical(event)
                digest = _event_hash(sequence, previous, body)
                db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                           (sequence, event["event_id"], body, previous, digest))
                db.execute("UPDATE journal_tail SET sequence=?, event_hash=? WHERE singleton=1",
                           (sequence, digest))
            result = replay.snapshot()
        return result

    def snapshot(self):
        """Read-only full replay audit; cash and positions are deltas, not balances."""
        with self._transaction(write=False) as db:
            replay, _previous = self._replay(db)
            result = replay.snapshot()
        return result
