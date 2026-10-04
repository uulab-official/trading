"""Strict SQLite-only recovery for the legacy offline paper application.

This is separate from PaperRunner's fixture checkpoint. No credentials, complete
configuration, network client, real exchange, or automatic order replay belongs
here. Invalid prior state is inspectable but never a fresh funded simulation.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import errno
import json
import os
import sqlite3
import stat
import uuid

from trading_core.order_manager import Order, OrderManager, OrderSide, OrderStatus, OrderType
from trading_core.paper_exchange import PaperExchange
from trading_core.portfolio import Portfolio, Trade
from trading_core.risk_manager import RiskManager
from trading_core.state_store import SQLiteStateStore


class CheckpointError(RuntimeError):
    """Recovery requires attention; orders must not guess what happened."""


TOP_KEYS = {"version", "mode", "bot_id", "quote_asset", "risk_config", "generation",
            "last_observed_at", "portfolio", "accounting", "risk", "exchange", "orders", "intent"}
RISK_FIELDS = ("max_position_size_pct", "max_positions", "risk_per_trade_pct",
               "max_daily_loss_pct", "max_daily_trades", "max_leverage",
               "default_stop_loss_pct", "default_take_profit_pct",
               "circuit_breaker_loss_pct", "cooldown_minutes_after_loss")
PORTFOLIO_KEYS = {"initial_balance", "cash_balance", "positions", "trades", "total_pnl", "win_count", "loss_count"}
POSITION_KEYS = {"symbol", "side", "size", "entry_price", "current_price", "stop_loss_price",
                 "take_profit_price", "opened_at", "order_id", "strategy_id"}
TRADE_KEYS = {"symbol", "side", "size", "entry_price", "exit_price", "pnl", "pnl_pct",
              "opened_at", "closed_at", "strategy_id", "accounting_date"}
ORDER_KEYS = {"client_order_id", "symbol", "side", "order_type", "amount", "price", "status",
              "filled", "remaining", "exchange_order_id", "created_at", "updated_at", "strategy_id"}
ACCOUNTING_KEYS = {"date", "daily_start_value", "daily_pnl"}
RISK_KEYS = {"date", "total_pnl", "trade_count", "loss_count", "circuit_breaker", "cooldown_until"}


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise CheckpointError(f"Invalid {label} fields")
    return value


def _number(value, *, positive=False, nonnegative=False):
    if not isinstance(value, str):
        raise CheckpointError("Checkpoint decimals must be strings")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise CheckpointError("Invalid checkpoint decimal") from error
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise CheckpointError("Invalid checkpoint decimal range")
    return result


def _count(value):
    if type(value) is not int or value < 0:
        raise CheckpointError("Invalid checkpoint counter")
    return value


def _day(value):
    if not isinstance(value, str):
        raise CheckpointError("Invalid UTC accounting date")
    try:
        result = date.fromisoformat(value)
    except ValueError as error:
        raise CheckpointError("Invalid UTC accounting date") from error
    if result.isoformat() != value:
        raise CheckpointError("Noncanonical UTC accounting date")
    return result


def _time(value):
    if not isinstance(value, str):
        raise CheckpointError("Invalid UTC timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise CheckpointError("Invalid UTC timestamp") from error
    if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
        raise CheckpointError("Checkpoint timestamps must name UTC")
    return result.replace(tzinfo=None)


def _iso(value):
    if value.utcoffset() is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc).isoformat()


def risk_identity(config):
    """Explicit allowlist: never serialize an application's entire config."""
    return {name: str(getattr(config, name)) if isinstance(getattr(config, name), Decimal)
            else getattr(config, name) for name in RISK_FIELDS}


def is_legacy_paper_symbol(symbol):
    """Enforce the checkpoint's quote unit before an economic transition."""
    if not isinstance(symbol, str):
        return False
    parts = symbol.split("/")
    return (len(parts) == 2 and parts[1] == "USDT" and bool(parts[0])
            and parts[0] != "USDT" and parts[0] == parts[0].strip() and ":" not in parts[0])


def _portfolio_snapshot(portfolio):
    data = portfolio.to_dict()
    data.pop("daily_start_value")
    data["trades"] = [{
        "symbol": trade.symbol, "side": trade.side, "size": str(trade.size),
        "entry_price": str(trade.entry_price), "exit_price": str(trade.exit_price),
        "pnl": str(trade.pnl), "pnl_pct": str(trade.pnl_pct),
        "opened_at": _iso(trade.opened_at), "closed_at": _iso(trade.closed_at),
        "strategy_id": trade.strategy_id,
        "accounting_date": trade.accounting_date.isoformat() if trade.accounting_date else None,
    } for trade in portfolio._trades]
    for position in data["positions"].values():
        position["opened_at"] = _iso(datetime.fromisoformat(position["opened_at"]))
    return data


def _restore_economics(data):
    raw = _object(data["portfolio"], PORTFOLIO_KEYS, "portfolio")
    initial = _number(raw["initial_balance"], positive=True)
    _number(raw["cash_balance"], nonnegative=True)
    _number(raw["total_pnl"])
    _count(raw["win_count"])
    _count(raw["loss_count"])
    if not isinstance(raw["positions"], dict) or not isinstance(raw["trades"], list):
        raise CheckpointError("Invalid portfolio collections")
    for symbol, position in raw["positions"].items():
        _object(position, POSITION_KEYS, "position")
        if position["symbol"] != symbol or position["side"] != "long":
            raise CheckpointError("Invalid spot position identity")
        _number(position["size"], positive=True)
        _number(position["entry_price"], positive=True)
        for field in ("current_price", "stop_loss_price", "take_profit_price"):
            if position[field] is not None:
                _number(position[field])
        _time(position["opened_at"])
    portfolio = Portfolio.from_dict(raw)
    # Preserve naive UTC internally for existing duration arithmetic.
    for position in portfolio.get_open_positions():
        position.opened_at = _time(raw["positions"][position.symbol]["opened_at"])
    for item in raw["trades"]:
        _object(item, TRADE_KEYS, "trade")
        if item["side"] != "sell":
            raise CheckpointError("Invalid close side")
        kwargs = {name: _number(item[name], positive=name in ("size", "entry_price", "exit_price"))
                  for name in ("size", "entry_price", "exit_price", "pnl", "pnl_pct")}
        if kwargs["pnl"] != (kwargs["exit_price"] - kwargs["entry_price"]) * kwargs["size"]:
            raise CheckpointError("Trade PnL does not match its close")
        if kwargs["pnl_pct"] != (kwargs["exit_price"] - kwargs["entry_price"]) / kwargs["entry_price"] * 100:
            raise CheckpointError("Trade PnL percentage does not match its close")
        portfolio._trades.append(Trade(symbol=item["symbol"], side="sell", **kwargs,
            opened_at=_time(item["opened_at"]), closed_at=_time(item["closed_at"]),
            strategy_id=item["strategy_id"], accounting_date=_day(item["accounting_date"])))
    exchange = PaperExchange.from_snapshot(data["exchange"])
    if exchange.snapshot() != data["exchange"] or exchange.fee_pct or exchange.slippage_pct:
        raise CheckpointError("Unsupported or noncanonical legacy paper ledger")
    ledger = exchange.snapshot()
    cash, total_pnl, wins, losses = initial, Decimal("0"), 0, 0
    positions = {}
    closes = []
    for fill in ledger["trades"]:
        symbol = fill["symbol"]
        if not symbol.endswith("/USDT"):
            raise CheckpointError("Unsupported quote currency")
        size, price = _number(fill["amount"], positive=True), _number(fill["average"], positive=True)
        if fill["side"] == "buy":
            if symbol in positions:
                raise CheckpointError("Multiple overlapping legacy entries")
            positions[symbol] = (size, price, fill["clientOrderId"])
            cash -= size * price
        else:
            if symbol not in positions or positions[symbol][0] != size:
                raise CheckpointError("Unmatched paper close")
            entry = positions.pop(symbol)[1]
            pnl = (price - entry) * size
            closes.append((symbol, size, entry, price, pnl))
            cash += size * price
            total_pnl += pnl
            wins += int(pnl > 0)
            losses += int(pnl <= 0)
        if cash < 0:
            raise CheckpointError("Paper ledger overspends cash")
    if cash != portfolio.cash_balance or total_pnl != portfolio._total_pnl or (wins, losses) != (portfolio._win_count, portfolio._loss_count):
        raise CheckpointError("Paper ledger and portfolio totals disagree")
    if closes != [(t.symbol, t.size, t.entry_price, t.exit_price, t.pnl) for t in portfolio._trades]:
        raise CheckpointError("Paper ledger and complete close history disagree")
    if set(positions) != set(raw["positions"]):
        raise CheckpointError("Paper ledger and portfolio holdings disagree")
    expected_balances = {"USDT": cash}
    for symbol, (size, entry, order_id) in positions.items():
        position = portfolio.get_position(symbol)
        if (position.size, position.entry_price, position.order_id) != (size, entry, order_id):
            raise CheckpointError("Paper position cost basis disagrees")
        expected_balances[symbol.split("/")[0]] = size
    balances = {asset: _number(value, nonnegative=True) for asset, value in ledger["balances"].items()}
    if {k: v for k, v in balances.items() if v} != {k: v for k, v in expected_balances.items() if v}:
        raise CheckpointError("Paper broker balances disagree")
    if not isinstance(data["orders"], dict) or set(data["orders"]) != set(ledger["orders"]):
        raise CheckpointError("Order manager and paper ledger identities disagree")
    manager = OrderManager(exchange)
    for key, item in data["orders"].items():
        _object(item, ORDER_KEYS, "order")
        fill = ledger["orders"][key]
        if (item["client_order_id"], item["symbol"], item["side"], item["order_type"], item["status"], item["exchange_order_id"]) != (
                key, fill["symbol"], fill["side"], "market", "filled", fill["id"]):
            raise CheckpointError("Order manager and paper fill disagree")
        amount, filled, remaining = (_number(item[name], nonnegative=True) for name in ("amount", "filled", "remaining"))
        if amount != _number(fill["amount"]) or filled != amount or remaining != 0 or item["price"] is not None:
            raise CheckpointError("Incomplete paper fill")
        manager._orders[key] = Order(key, item["symbol"], OrderSide(item["side"]), OrderType.MARKET,
            amount, status=OrderStatus.FILLED, filled=filled, remaining=remaining,
            exchange_order_id=item["exchange_order_id"], created_at=_time(item["created_at"]),
            updated_at=_time(item["updated_at"]), strategy_id=item["strategy_id"])
    return portfolio, exchange, manager


class LegacyPaperOrderManager(OrderManager):
    """Even direct order calls require this process's committed bounded intent."""
    def __init__(self, exchange, owner):
        super().__init__(exchange)
        self.owner = owner

    async def place_order(self, symbol, side, order_type, amount, price=None, strategy_id=None, client_order_id=None):
        owner, intent = self.owner, self.owner.intent
        if (owner._descriptor is None or not owner._executing_intent or intent is None
                or client_order_id != intent["id"] or symbol != intent["symbol"]
                or side.value != intent["kind"] or order_type is not OrderType.MARKET
                or price is not None or amount != _number(intent["amount"], positive=True)):
            raise CheckpointError("Order requires a matching current durable intent")
        owner.validate_quote(symbol, _number(intent["price"], positive=True))
        quote = await self.exchange.fetch_ticker(symbol)
        if quote.last != _number(intent["price"], positive=True):
            raise CheckpointError("Execution price changed after intent")
        return await super().place_order(symbol, side, order_type, amount, price, strategy_id, client_order_id)


class LegacyCheckpoint:
    """One writer, one paper ledger, one authoritative committed generation."""

    def __init__(self, store, bot_id, risk_config):
        self.store, self.bot_id, self.config = store, bot_id, risk_config
        self.key = f"bot:{bot_id}:legacy_checkpoint:v1"
        self.portfolio = self.exchange = self.manager = self.risk = None
        self.generation = 0
        self.last_observed = datetime.utcnow()
        self.intent = None
        self.recovery_reason = None
        self.risk_only_recovery = False
        self._saved = None
        self._source = None
        self._descriptor = None
        self._prior_marker = False
        self._executing_intent = False
        if not isinstance(bot_id, str) or not bot_id or ":" in bot_id:
            raise CheckpointError("Invalid paper bot identity")
        for field in RISK_FIELDS:
            value = getattr(risk_config, field)
            if isinstance(value, Decimal):
                if not value.is_finite() or value <= 0:
                    raise CheckpointError("Invalid effective paper risk settings")
            elif type(value) is not int or value < (0 if field == "cooldown_minutes_after_loss" else 1):
                raise CheckpointError("Invalid effective paper risk settings")
        if not isinstance(store, SQLiteStateStore):
            self.recovery_reason = "Unsupported backend: legacy paper v1 requires SQLite; existing state preserved"
            return
        self._acquire()

    def _acquire(self):
        try:
            import fcntl
            path = self.store.db_path.resolve()
            lock_path = path.with_name(f".{path.name}.legacy-paper.lock")
            self._prior_marker = lock_path.exists()
            descriptor = os.open(lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise CheckpointError("Checkpoint writer lock is not a regular file")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            self._descriptor = descriptor
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise CheckpointError("Another checkpoint writer owns this database") from error
            raise CheckpointError("Cannot acquire checkpoint writer lock") from error
        except (ImportError, AttributeError) as error:
            raise CheckpointError("Checkpoint writer ownership requires local POSIX SQLite") from error

    def release(self):
        if self._descriptor is not None:
            import fcntl
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None

    def restore(self):
        if self.recovery_reason:
            return
        try:
            if not self.store.db_path.exists():
                if self._prior_marker or self.store.existed_at_open:
                    raise CheckpointError("Missing database in an established workspace; explicit recovery required")
                # This is the only v1 schema creation: a genuinely new database,
                # after exclusive ownership and before any portfolio or order.
                self.store._init_db()
            with sqlite3.connect(self.store.db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT key, data FROM state").fetchall()
            raw = dict(rows).get(self.key)
            if raw is None:
                if rows:
                    raise CheckpointError("Existing legacy or foreign state needs explicit recovery; no automatic migration")
                if self.store.existed_at_open or self._prior_marker:
                    raise CheckpointError("Existing empty database needs explicit recovery")
                self.portfolio = Portfolio(Decimal("10000"))
                self.exchange = PaperExchange(initial_balance=self.portfolio.cash_balance)
                self.manager = LegacyPaperOrderManager(self.exchange, self)
                self.risk = RiskManager(self.config, self.portfolio)
                self.save()
                return
            data = json.loads(raw)
            self._validate_identity(data)
            portfolio, exchange, manager = _restore_economics(data)
            self.portfolio, self.exchange = portfolio, exchange
            self.manager = LegacyPaperOrderManager(exchange, self)
            self.manager.load_orders(manager.get_all_orders())
            self.generation = data["generation"]
            self.last_observed = _time(data["last_observed_at"])
            self._saved = deepcopy(data)
            self.intent = deepcopy(data["intent"])
            if self.intent is not None:
                _object(self.intent, {"id", "kind", "symbol", "amount", "price"}, "intent")
                if self.intent["kind"] not in ("buy", "sell") or not all(isinstance(v, str) and v for v in self.intent.values()):
                    raise CheckpointError("Invalid transition intent")
                _number(self.intent["amount"], positive=True)
                _number(self.intent["price"], positive=True)
            try:
                self._restore_risk(data)
            except (CheckpointError, ValueError, TypeError, KeyError, InvalidOperation):
                self.recovery_reason = "Risk state incomplete or invalid; new entries blocked"
                self.risk_only_recovery = True
                self._source = raw
                # Preserve the known processing high-water even without a
                # recoverable baseline. Unknown daily arithmetic is never saved.
                self.portfolio._daily_date = max(self.last_observed.date(), datetime.utcnow().date())
            if self.intent is not None:
                self.recovery_reason = "Unfinished intent requires reconciliation; no automatic order replay"
                self.risk_only_recovery = False
            elif self.risk is not None and datetime.utcnow() < self.last_observed:
                self.recovery_reason = "UTC clock rollback; preserved risk state blocks new entries"
        except Exception as error:
            self.portfolio = self.exchange = self.manager = self.risk = None
            self.recovery_reason = str(error) if isinstance(error, CheckpointError) else "Unreadable or invalid existing checkpoint; state preserved"

    def _validate_identity(self, data):
        _object(data, TOP_KEYS, "checkpoint")
        if type(data["version"]) is not int or data["version"] != 1 or data["mode"] != "paper":
            raise CheckpointError("Unsupported checkpoint version or mode")
        if data["bot_id"] != self.bot_id or data["quote_asset"] != "USDT":
            raise CheckpointError("Checkpoint bot or currency identity mismatch")
        if data["risk_config"] != risk_identity(self.config):
            raise CheckpointError("Checkpoint effective risk configuration mismatch")
        if any(type(data["risk_config"][name]) is not type(value) for name, value in risk_identity(self.config).items()):
            raise CheckpointError("Invalid risk configuration types")
        # Missing known risk fields can be diagnosed separately from economics.
        # Unexpected fields are never copied into a recovery snapshot/archive.
        for name, allowed in (("accounting", ACCOUNTING_KEYS), ("risk", RISK_KEYS)):
            section = data[name]
            if not isinstance(section, dict) or not set(section).issubset(allowed):
                raise CheckpointError("Unsupported risk/accounting payload fields")
        if _count(data["generation"]) < 1:
            raise CheckpointError("Invalid checkpoint generation")
        _time(data["last_observed_at"])

    def _restore_risk(self, data):
        accounting = _object(data["accounting"], ACCOUNTING_KEYS, "accounting")
        risk = _object(data["risk"], RISK_KEYS, "risk")
        day = _day(accounting["date"])
        baseline = _number(accounting["daily_start_value"], positive=True)
        pnl = _number(accounting["daily_pnl"])
        if _day(risk["date"]) != day or _number(risk["total_pnl"]) != pnl:
            raise CheckpointError("Risk and accounting periods or PnL disagree")
        count, losses = _count(risk["trade_count"]), _count(risk["loss_count"])
        if losses > count or count > self.portfolio._win_count + self.portfolio._loss_count:
            raise CheckpointError("Invalid daily trade counts")
        if type(risk["circuit_breaker"]) is not bool:
            raise CheckpointError("Invalid circuit latch")
        cooldown = _time(risk["cooldown_until"]) if risk["cooldown_until"] is not None else None
        if day > _time(data["last_observed_at"]).date():
            raise CheckpointError("Accounting date exceeds observed UTC high-water")
        history = self.portfolio._trades
        if (any(t.accounting_date > day or t.closed_at.date() > t.accounting_date for t in history)
                or any(a.accounting_date > b.accounting_date for a, b in zip(history, history[1:]))):
            raise CheckpointError("Close accounting chronology is inconsistent")
        daily = [t for t in self.portfolio._trades if t.accounting_date == day]
        if (sum((t.pnl for t in daily), Decimal("0")), len(daily), sum(t.pnl < 0 for t in daily)) != (pnl, count, losses):
            raise CheckpointError("Daily risk state disagrees with complete close records")
        cumulative = Decimal("0")
        for trade in daily:
            cumulative += trade.pnl
            if -cumulative / baseline * 100 >= self.config.circuit_breaker_loss_pct and not risk["circuit_breaker"]:
                raise CheckpointError("Recovered gains must not clear a prior circuit latch")
        negative = [t for t in daily if t.pnl < 0]
        if negative and (cooldown is None or cooldown < negative[-1].closed_at + timedelta(minutes=self.config.cooldown_minutes_after_loss)):
            raise CheckpointError("Missing or shortened post-loss cooldown")
        if -pnl / baseline * 100 >= self.config.circuit_breaker_loss_pct and not risk["circuit_breaker"]:
            raise CheckpointError("Missing required circuit latch")
        self.portfolio._daily_date, self.portfolio._daily_start_value, self.portfolio._daily_pnl = day, baseline, pnl
        self.risk = RiskManager(self.config, self.portfolio)
        self.risk._daily_stats = {"date": day, "total_pnl": pnl, "trade_count": count, "loss_count": losses}
        self.risk._circuit_breaker_triggered = risk["circuit_breaker"]
        self.risk._cooldown_until = cooldown

    def snapshot(self):
        return deepcopy(self._saved)

    def observe_period(self):
        if self.risk is None or self.intent is not None:
            return
        now = datetime.utcnow()
        if now < self.last_observed:
            self.recovery_reason = "UTC clock rollback; preserved risk state blocks new entries"
            return
        if self.recovery_reason and self.recovery_reason.startswith("UTC clock rollback"):
            self.recovery_reason = None
        if self.recovery_reason:
            return
        self.last_observed = now
        self.portfolio.ensure_daily_period(now)
        self.risk._sync_daily_stats()

    def entry_allowed(self):
        self.observe_period()
        if self.intent is not None:
            return False, "Unfinished intent blocks another entry"
        if self.recovery_reason:
            return False, self.recovery_reason
        if self.portfolio is None or self.risk is None:
            return False, "Recovery required"
        if self.portfolio._daily_start_value <= 0 or not self.portfolio._daily_start_value.is_finite():
            self.recovery_reason = "Daily baseline is invalid; explicit recovery required"
            return False, "Daily baseline is not valid for new entries"
        return True, "OK"

    def risk_report(self):
        """A read-only projection: diagnostic reads never roll/reset a period."""
        if self.risk is None:
            return {"daily_pnl": None, "daily_pnl_pct": None, "daily_trades": None,
                    "daily_losses": None, "circuit_breaker": None,
                    "cooldown_active": None, "recovery_required": self.recovery_reason}
        return {"daily_pnl": float(self.risk._daily_stats["total_pnl"]),
                "daily_pnl_pct": (float(self.portfolio.get_daily_pnl_pct())
                                  if self.portfolio._daily_start_value > 0 else None),
                "daily_trades": self.risk._daily_stats["trade_count"],
                "daily_losses": self.risk._daily_stats["loss_count"],
                "circuit_breaker": self.risk._circuit_breaker_triggered,
                "cooldown_active": bool(self.risk._cooldown_until and datetime.utcnow() < self.risk._cooldown_until),
                "recovery_required": self.recovery_reason}

    def _encode(self, generation):
        portfolio = _portfolio_snapshot(self.portfolio)
        if self.risk is None:
            # Unknown prior risk stays unknown. Do not invent a baseline after an exit.
            accounting, risk = deepcopy(self._saved["accounting"]), deepcopy(self._saved["risk"])
        else:
            accounting = {"date": self.portfolio.daily_date.isoformat(),
                "daily_start_value": str(self.portfolio._daily_start_value), "daily_pnl": str(self.portfolio._daily_pnl)}
            risk = {"date": self.risk._daily_stats["date"].isoformat(),
                "total_pnl": str(self.risk._daily_stats["total_pnl"]),
                "trade_count": self.risk._daily_stats["trade_count"], "loss_count": self.risk._daily_stats["loss_count"],
                "circuit_breaker": self.risk._circuit_breaker_triggered,
                "cooldown_until": _iso(self.risk._cooldown_until) if self.risk._cooldown_until else None}
        orders = {key: value.to_dict() for key, value in self.manager.get_all_orders().items()}
        for order in orders.values():
            for key in ("created_at", "updated_at"):
                order[key] = _iso(datetime.fromisoformat(order[key]))
        return {"version": 1, "mode": "paper", "bot_id": self.bot_id, "quote_asset": "USDT",
            "risk_config": risk_identity(self.config), "generation": generation,
            "last_observed_at": _iso(self.last_observed), "portfolio": portfolio,
            "accounting": accounting, "risk": risk, "exchange": self.exchange.snapshot(),
            "orders": orders, "intent": deepcopy(self.intent)}

    def save(self, *, transition=False):
        if self.portfolio is None or (self.recovery_reason and not transition) or (self.intent and not transition):
            return
        if self._descriptor is None:
            raise CheckpointError("Checkpoint writer ownership is missing")
        try:
            self.last_observed = max(self.last_observed, datetime.utcnow())
            data = self._encode(self.generation + 1)
            self._validate_identity(data)
            _restore_economics(data)
            encoded = json.dumps(data, sort_keys=True, allow_nan=False)
            with sqlite3.connect(self.store.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT data FROM state WHERE key=?", (self.key,)).fetchone()
                actual = _count(json.loads(row[0])["generation"]) if row else 0
                if actual != self.generation:
                    raise CheckpointError("Checkpoint generation changed; stale writer refused")
                if self._source is not None:
                    # On an explicit verified recovery exit, retain the exact original
                    # v1 source before replacing its canonical row. Never done at boot.
                    conn.execute("INSERT OR IGNORE INTO state(key,data) VALUES (?,?)",
                        (f"{self.key}:recovery_source:{self.generation}", self._source))
                conn.execute("INSERT INTO state(key,data,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                    (self.key, encoded, _iso(self.last_observed)))
                # Dashboard fields are projections of this same committed generation.
                for name, value in (("status", {"mode": "paper", "bot_id": self.bot_id,
                    "status": "recovery_required" if self.recovery_reason else "ready", "generation": data["generation"]}),
                    ("positions", list(portfolio for portfolio in data["portfolio"]["positions"].values())),
                    ("risk", {"accounting": data["accounting"], "risk": data["risk"],
                              "recovery_reason": self.recovery_reason, "generation": data["generation"]})):
                    conn.execute("INSERT INTO state(key,data,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                        (f"bot:{self.bot_id}:{name}", json.dumps(value, allow_nan=False), _iso(self.last_observed)))
                conn.commit()
            self.generation = data["generation"]
            self._saved = data
            self._source = None
        except Exception as error:
            self._executing_intent = False
            self.risk_only_recovery = False
            self.recovery_reason = "Checkpoint save failed; new entries blocked"
            raise CheckpointError(str(error) if isinstance(error, CheckpointError) else "Checkpoint transaction failed") from error

    def begin_intent(self, kind, symbol, *, amount, price):
        if self.intent is not None:
            raise CheckpointError("Unfinished intent cannot be replayed")
        if kind not in ("buy", "sell"):
            raise CheckpointError("Unsupported transition")
        if not is_legacy_paper_symbol(symbol):
            raise CheckpointError("Legacy paper orders require a BASE/USDT spot symbol")
        if kind == "buy" and not self.entry_allowed()[0]:
            raise CheckpointError(self.recovery_reason or "Entry is blocked")
        if self.recovery_reason and not self.risk_only_recovery and not self.recovery_reason.startswith("UTC clock rollback"):
            raise CheckpointError(self.recovery_reason)
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0 or not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise CheckpointError("Invalid bounded intent amount or price")
        self.validate_quote(symbol, price)
        if kind == "buy":
            allowed, reason = self.risk.check_trade_allowed(symbol, "buy", amount, price)
            if not allowed or amount * price > self.portfolio.cash_balance:
                raise CheckpointError(reason if not allowed else "Insufficient paper cash")
        else:
            position = self.portfolio.get_position(symbol)
            if position is None or position.size != amount:
                raise CheckpointError("Reducing intent must match known full position")
        self.intent = {"id": f"legacy-{uuid.uuid4().hex}", "kind": kind, "symbol": symbol,
                       "amount": str(amount), "price": str(price)}
        self.save(transition=True)
        self._executing_intent = True
        return self.intent["id"]

    def validate_quote(self, symbol, price):
        if not is_legacy_paper_symbol(symbol):
            raise CheckpointError("Legacy paper orders require a BASE/USDT spot symbol")
        if self.exchange is None:
            raise CheckpointError("No verified paper ledger is available")
        snapshot = self.exchange.snapshot()
        quote = snapshot["tickers"].get(symbol)
        if quote is None or _number(quote["last"], positive=True) != price:
            raise CheckpointError("No matching positive paper quote")
        timestamp = quote["timestamp"]
        if type(timestamp) is not int or timestamp < 0:
            raise CheckpointError("Invalid paper quote timestamp")
        if snapshot["trades"] and timestamp < snapshot["trades"][-1]["timestamp"]:
            raise CheckpointError("Paper quote precedes an already committed fill")

    def finish_intent(self):
        if self.intent is None:
            raise CheckpointError("No transition to complete")
        self._executing_intent = False
        self.intent = None
        self.save(transition=True)

    def fail_transition(self):
        self._executing_intent = False
        self.recovery_reason = "Unfinished intent requires reconciliation; no automatic order replay"
        self.risk_only_recovery = False
