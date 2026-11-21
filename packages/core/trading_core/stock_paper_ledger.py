"""Durable event-sourced STOCK PAPER cash ledger.

No network, brokerage authentication, order submission, margin, or FX.
Every simulated fill is committed in one local SQLite transaction.
On each command the full ledger is replayed from an immutable ordered log.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from trading_core.stock_paper import StockPaperBook, _amount


class StockPaperLedger:
    def __init__(self, path, *, quote_currency, initial_cash, fee_pct="0"):
        self.path = Path(path).resolve()
        self.quote_currency = quote_currency
        self.initial_cash = str(_amount(initial_cash))
        self.fee_pct = str(_amount(fee_pct, zero=True))
        # Validate before touching a filesystem.
        StockPaperBook(quote_currency, self.initial_cash, self.fee_pct)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""CREATE TABLE IF NOT EXISTS paper_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS paper_fills (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL, side TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    price TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )""")
                meta = dict(db.execute("SELECT key,value FROM paper_meta").fetchall())
                wanted = {
                    "schema_version": "1",
                    "mode": "paper",
                    "quote_currency": quote_currency,
                    "initial_cash": self.initial_cash,
                    "fee_pct": self.fee_pct,
                }
                if meta:
                    if meta != wanted:
                        raise ValueError("Stock paper ledger identity or configuration mismatch")
                else:
                    if db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]:
                        raise ValueError("Orphan paper fills without configuration")
                    db.executemany(
                        "INSERT INTO paper_meta (key,value) VALUES (?,?)", wanted.items()
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _connect(self):
        db = sqlite3.connect(str(self.path), isolation_level=None, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _assert_identity(self, db):
        """Verify account/currency config inside EVERY active transaction.

        An already-created instance must not keep writing if another
        operator or process modified its SQLite metadata after startup.
        """
        current = dict(db.execute("SELECT key,value FROM paper_meta").fetchall())
        expected = {
            "schema_version": "1",
            "mode": "paper",
            "quote_currency": self.quote_currency,
            "initial_cash": self.initial_cash,
            "fee_pct": self.fee_pct,
        }
        if current != expected:
            raise ValueError("Paper ledger configuration changed after initialization")

    @staticmethod
    def _rebuild(db, quote_currency, initial_cash, fee_pct):
        book = StockPaperBook(quote_currency, initial_cash, fee_pct)
        count = 0
        for row in db.execute(
            "SELECT client_id,symbol,side,shares,price,result_json "
            "FROM paper_fills ORDER BY sequence"
        ):
            client_id, symbol, side, shares, price, expected = row
            if type(shares) is not int or shares <= 0:
                raise ValueError("Invalid recorded share quantity")
            actual = book.execute(client_id, symbol, side, shares, price=price)
            saved = json.loads(expected)
            if saved != actual:
                raise ValueError("Paper execution record does not match replay")
            count += 1
        return book, count

    def execute(self, client_id, symbol, side, shares, *, price):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(db)
                has_strategy = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_strategy_state'"
                ).fetchone()
                if (
                    has_strategy
                    and db.execute("SELECT 1 FROM paper_strategy_state WHERE id=1").fetchone()
                ):
                    raise ValueError("Manual fill cannot mutate a strategy-managed stock ledger")
                book, count = self._rebuild(
                    db, self.quote_currency, self.initial_cash, self.fee_pct
                )
                # An already-seen client ID is never a second fill.
                # The core cash book rejects any change of terms.
                result = book.execute(client_id, symbol, side, shares, price=price)
                if len(book.fills) != count:
                    db.execute(
                        "INSERT INTO paper_fills (client_id,symbol,side,shares,price,result_json) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            client_id,
                            symbol,
                            side,
                            shares,
                            str(_amount(price)),
                            json.dumps(result, sort_keys=True, allow_nan=False),
                        ),
                    )
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def apply_strategy_replay(
        self,
        fixture,
        *,
        identity,
        expected_fills,
        expected_fills_for_prefix,
        marks,
        expected_equity,
    ):
        """Atomically advance one paper-only strategy and its simulated fills.

        Full saved candle-prefix SHA is checked before mutation, and the
        strategy-generated prior fills must equal persisted fill history.
        The prefix replay callback receives the committed bar count while
        the write transaction is locked, so a deleted trailing fill cannot
        be mistaken for an execution from newly appended candles.
        This is an OFFLINE replay, not a timed market-connected service.
        """
        if not isinstance(fixture, dict) or fixture.get("mode") != "paper":
            raise ValueError("Strategy replay requires paper fixture")
        candles = fixture.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError("Strategy replay requires completed candles")
        if not isinstance(expected_fills, list):
            raise ValueError("Expected fills must be a list")
        if not callable(expected_fills_for_prefix):
            raise ValueError("Strategy prefix replay is required")
        if not isinstance(identity, dict) or not identity:
            raise ValueError("Strategy configuration identity is required")
        identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(db)
                db.execute("""CREATE TABLE IF NOT EXISTS paper_strategy_state (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    identity_json TEXT NOT NULL,
                    bars INTEGER NOT NULL,
                    prefix_sha256 TEXT NOT NULL
                )""")
                saved = db.execute(
                    "SELECT identity_json,bars,prefix_sha256 FROM paper_strategy_state WHERE id=1"
                ).fetchone()
                if saved is None:
                    if db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]:
                        raise ValueError("Strategy cannot attach to existing manual stock fills")
                    prior_bars = 0
                else:
                    stored_identity, prior_bars, prefix_sha = saved
                    if stored_identity != identity_json:
                        raise ValueError("Stock strategy assumptions changed")
                    if type(prior_bars) is not int or prior_bars < 1 or prior_bars > len(candles):
                        raise ValueError("Stock paper strategy cursor invalid")
                    prefix = json.dumps(
                        candles[:prior_bars], sort_keys=True, separators=(",", ":"), allow_nan=False
                    )
                    if hashlib.sha256(prefix.encode()).hexdigest() != prefix_sha:
                        raise ValueError("Previous completed stock candles were changed")
                expected_prior_fills = (
                    [] if saved is None else expected_fills_for_prefix(prior_bars)
                )
                if not isinstance(expected_prior_fills, list):
                    raise ValueError("Expected prior fills must be a list")
                if expected_prior_fills != expected_fills[: len(expected_prior_fills)]:
                    raise ValueError("Strategy replay changed historical paper fills")
                book, previous_fill_count = self._rebuild(
                    db, self.quote_currency, self.initial_cash, self.fee_pct
                )
                if book.fills != expected_prior_fills:
                    raise ValueError("Historical strategy fills differ from committed cursor")
                new_fills = expected_fills[previous_fill_count:]
                for fill in new_fills:
                    if not isinstance(fill, dict) or fill.get("mode") != "paper":
                        raise ValueError("Strategy emitted a non-paper fill")
                    actual = book.execute(
                        fill["client_id"],
                        fill["symbol"],
                        fill["side"],
                        fill["shares"],
                        price=fill["price"],
                    )
                    if actual != fill:
                        raise ValueError("Strategy fill cannot be reconstructed")
                    db.execute(
                        "INSERT INTO paper_fills (client_id,symbol,side,shares,price,result_json) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            fill["client_id"],
                            fill["symbol"],
                            fill["side"],
                            fill["shares"],
                            fill["price"],
                            json.dumps(fill, sort_keys=True, allow_nan=False),
                        ),
                    )
                valuation_decimal = book.marked_equity(marks)
                # Verify independently computed expected equity BEFORE
                # committing new fill events and strategy cursor. Previously
                # the caller performed this check only AFTER COMMIT.
                if valuation_decimal != _amount(expected_equity, zero=True):
                    raise ValueError("Paper strategy equity reconciliation failed before commit")
                valuation = str(valuation_decimal)
                full_prefix = json.dumps(
                    candles, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                digest = hashlib.sha256(full_prefix.encode()).hexdigest()
                if saved is None:
                    db.execute(
                        "INSERT INTO paper_strategy_state (id,identity_json,bars,prefix_sha256) "
                        "VALUES (1,?,?,?)",
                        (identity_json, len(candles), digest),
                    )
                else:
                    db.execute(
                        "UPDATE paper_strategy_state SET bars=?,prefix_sha256=? WHERE id=1",
                        (len(candles), digest),
                    )
                db.execute("COMMIT")
                return {
                    "mode": "paper",
                    "live_orders_enabled": False,
                    "bars": len(candles),
                    "appended_bars": len(candles) - prior_bars,
                    "new_simulated_fills": len(new_fills),
                    "total_simulated_fills": len(book.fills),
                    "marked_equity": valuation,
                    "quote_currency": self.quote_currency,
                    "status": "paper_replayed",
                }
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def report(self, *, marks=None):
        # BEGIN pins the snapshot: no concurrent writer may interleave
        # a fill between reconstruction and its reported valuation.
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                self._assert_identity(db)
                book, count = self._rebuild(
                    db, self.quote_currency, self.initial_cash, self.fee_pct
                )
                equity = book.marked_equity(marks or {})
                result = {
                    "mode": "paper",
                    "live_orders_enabled": False,
                    "quote_currency": self.quote_currency,
                    "cash": str(book.cash),
                    "holdings": dict(book.holdings),
                    "simulated_fills": count,
                    "marked_equity": str(equity),
                    "warning": "Caller-supplied marks and hypothetical fills; not broker balances",
                }
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise
