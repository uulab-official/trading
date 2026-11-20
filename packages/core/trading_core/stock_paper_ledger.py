"""Durable event-sourced STOCK PAPER cash ledger.

No network, brokerage authentication, order submission, margin, or FX.
Every simulated fill is committed in one local SQLite transaction.
On each command the full ledger is replayed from an immutable ordered log.
"""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import json
import sqlite3

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
                    "schema_version": "1", "mode": "paper",
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
                    db.executemany("INSERT INTO paper_meta (key,value) VALUES (?,?)", wanted.items())
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
                        (client_id, symbol, side, shares, str(_amount(price)),
                         json.dumps(result, sort_keys=True, allow_nan=False)),
                    )
                db.execute("COMMIT")
                return result
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def report(self, *, marks=None):
        # BEGIN pins the snapshot: no concurrent writer may interleave
        # a fill between reconstruction and its reported valuation.
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                book, count = self._rebuild(
                    db, self.quote_currency, self.initial_cash, self.fee_pct
                )
                equity = book.marked_equity(marks or {})
                result = {
                    "mode": "paper", "live_orders_enabled": False,
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
