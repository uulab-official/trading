"""Offline spot-market emulator using only caller-supplied prices and candles.

This deliberately has no credential fields, sockets, HTTP client, or fallback to
an exchange. Friction is explicitly configurable; it cannot establish profitability.
"""

from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

from trading_core.exchange import Balance, ExchangeInterface, Market, Ticker

PAPER_CALCULATION_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


def _decimal(value: Any, *, positive: bool = True) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        raise ValueError("Value must be finite and positive" if positive else "Value must be finite and non-negative")
    return number


def _assets(symbol: str):
    parts = symbol.split("/")
    if len(parts) != 2 or not all(parts) or any(":" in part or part != part.strip() for part in parts) or parts[0] == parts[1]:
        raise ValueError("Paper supports spot symbols in BASE/QUOTE format")
    return parts[0], parts[1]


def _serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


class PaperExchange(ExchangeInterface):
    def __init__(self, initial_balance: Decimal = Decimal("10000"), initial_positions: Optional[Mapping[str, Decimal]] = None, *, fee_pct: Decimal = Decimal("0"), slippage_pct: Decimal = Decimal("0"), quote_currency: str = "USDT"):
        if not isinstance(quote_currency, str) or quote_currency not in ("USDT", "KRW"):
            raise ValueError("Offline paper quote currency must be USDT or KRW")
        self._quote_currency = quote_currency
        self.fee_pct = _decimal(fee_pct, positive=False)
        self.slippage_pct = _decimal(slippage_pct, positive=False)
        if self.fee_pct >= 100 or self.slippage_pct >= 100:
            raise ValueError("Paper fee/slippage percentages must be below 100")
        self.total_fees = Decimal("0")
        self._balances = {quote_currency: _decimal(initial_balance, positive=False)}
        for symbol, amount in (initial_positions or {}).items():
            base, quote = self._market_assets(symbol)
            self._balances[base] = _decimal(amount, positive=False)
        self._tickers: Dict[str, Ticker] = {}
        self._candles = {}
        self._orders = {}
        self._requests = {}
        self._trades = []
        self._next_order_id = 1
        self._connected = False

    @property
    def quote_currency(self) -> str:
        return self._quote_currency

    def _market_assets(self, symbol):
        base, quote = _assets(symbol)
        if quote != self.quote_currency:
            raise ValueError("A paper book cannot mix quote currencies or infer an FX conversion")
        return base, quote

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Paper exchange is disconnected")

    def set_price(self, symbol: str, price: Decimal, *, timestamp: int = 0) -> None:
        # Retain legacy USDT metadata caches; only same-quote holdings/orders
        # affect the ledger. New KRW books keep all supplied marks explicit.
        self._market_assets(symbol) if self.quote_currency == "KRW" else _assets(symbol)
        price = _decimal(price)
        self._tickers[symbol] = Ticker(symbol, price, price, price, Decimal("0"), timestamp)

    def set_ohlcv(self, symbol: str, timeframe: str, candles: List[List[Any]]) -> None:
        self._market_assets(symbol) if self.quote_currency == "KRW" else _assets(symbol)
        copied = [list(candle) for candle in candles]
        for candle in copied:
            if len(candle) != 6:
                raise ValueError("Each fixture candle must contain timestamp, open, high, low, close, volume")
            for value in candle[1:5]:
                _decimal(value)
            _decimal(candle[5], positive=False)
        self._candles[(symbol, timeframe)] = copied
        if copied:
            self.set_price(symbol, copied[-1][4], timestamp=int(copied[-1][0]))

    async def fetch_balance(self) -> Dict[str, Balance]:
        self._ensure_connected()
        return {asset: Balance(asset, amount, Decimal("0"), amount) for asset, amount in self._balances.items()}

    async def fetch_ticker(self, symbol: str) -> Ticker:
        self._ensure_connected()
        if symbol not in self._tickers:
            raise ValueError(f"No local paper price supplied for {symbol}")
        ticker = self._tickers[symbol]
        return Ticker(**vars(ticker))

    async def fetch_markets(self) -> List[Market]:
        self._ensure_connected()
        return [Market(symbol, *_assets(symbol), Decimal("0"), Decimal("0"), 8, True) for symbol in self._tickers]

    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[List[Any]]:
        self._ensure_connected()
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("Candle limit must be a positive integer")
        return [list(candle) for candle in self._candles.get((symbol, timeframe), [])[-limit:]]

    async def create_order(self, symbol: str, order_type: str, side: str, amount: Decimal, price: Optional[Decimal] = None, params: Optional[Dict] = None) -> Dict[str, Any]:
        self._ensure_connected()
        base, quote = self._market_assets(symbol)
        amount = _decimal(amount)
        if order_type != "market" or side not in ("buy", "sell"):
            raise ValueError("Offline paper execution supports market buys and sells only")
        key = (params or {}).get("clientOrderId")
        next_id = self._next_order_id
        if not key:
            key = f"paper-local-{next_id}"
            while key in self._requests:
                next_id += 1
                key = f"paper-local-{next_id}"
            next_id += 1
        request = (symbol, order_type, side, amount, price)
        if key in self._requests:
            if self._requests[key] != request:
                raise ValueError("A paper clientOrderId cannot be reused for a different order")
            return deepcopy(self._orders[key])
        ticker = await self.fetch_ticker(symbol)
        with localcontext(PAPER_CALCULATION_CONTEXT):
            direction = Decimal("1") if side == "buy" else Decimal("-1")
            fill_price = ticker.last * (Decimal("1") + direction * self.slippage_pct / 100)
            cost = amount * fill_price
            fee = cost * self.fee_pct / 100
            if side == "buy":
                if cost + fee > self._balances.get(quote, Decimal("0")):
                    raise ValueError("Insufficient paper quote balance")
                self._balances[quote] -= cost + fee
                self._balances[base] = self._balances.get(base, Decimal("0")) + amount
            else:
                if amount > self._balances.get(base, Decimal("0")):
                    raise ValueError("Insufficient paper asset balance")
                self._balances[base] -= amount
                self._balances[quote] = self._balances.get(quote, Decimal("0")) + cost - fee
            self.total_fees += fee
        self._next_order_id = next_id
        order = {"id": f"paper-{key}", "clientOrderId": key, "symbol": symbol, "type": order_type, "side": side,
                 "amount": amount, "filled": amount, "remaining": Decimal("0"), "average": fill_price,
                 "price": fill_price, "status": "closed", "timestamp": ticker.timestamp,
                 "fee": {"cost": fee, "currency": quote}}
        self._requests[key] = request
        self._orders[key] = order
        self._trades.append(deepcopy(order))
        return deepcopy(order)

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        order = await self.fetch_order(order_id, symbol)
        raise ValueError("Paper market orders fill immediately and cannot be cancelled")

    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        self._ensure_connected()
        for order in self._orders.values():
            if order["id"] == order_id and order["symbol"] == symbol:
                return deepcopy(order)
        raise ValueError("Unknown paper order")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_connected()
        return []

    async def fetch_my_trades(self, symbol: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        self._ensure_connected()
        trades = [deepcopy(trade) for trade in self._trades if (symbol is None or trade["symbol"] == symbol) and (since is None or trade["timestamp"] >= since)]
        return trades[-limit:] if limit is not None and limit > 0 else trades

    def get_total_value(self) -> Decimal:
        """Mark quote cash and spot holdings in the book's explicit quote currency."""
        with localcontext(PAPER_CALCULATION_CONTEXT):
            total = self._balances.get(self.quote_currency, Decimal("0"))
            for asset, amount in self._balances.items():
                if asset != self.quote_currency and amount:
                    symbol = f"{asset}/{self.quote_currency}"
                    if symbol not in self._tickers:
                        raise ValueError(f"No local valuation price supplied for {symbol}")
                    total += amount * self._tickers[symbol].last
        return total

    def snapshot(self) -> Dict[str, Any]:
        """Plain JSON-compatible paper state; never credentials or executable objects."""
        state = {
            "version": 1 if self.quote_currency == "USDT" else 2, "mode": "paper", "fee_pct": self.fee_pct,
            "slippage_pct": self.slippage_pct, "total_fees": self.total_fees,
            "balances": self._balances,
            "tickers": {symbol: vars(ticker) for symbol, ticker in self._tickers.items()},
            "orders": self._orders, "requests": self._requests, "trades": self._trades,
            "next_order_id": self._next_order_id,
        }
        # Keep the existing USDT schema byte-compatible; new KRW books state
        # their units in a separate version instead of relabeling old balances.
        if self.quote_currency != "USDT":
            state["quote_currency"] = self.quote_currency
        return _serialize(state)

    @classmethod
    def from_snapshot(cls, state: Mapping[str, Any]):
        version = state.get("version")
        if type(version) is not int or state.get("mode") != "paper":
            raise ValueError("Only explicit-currency offline paper snapshots can be restored")
        if version == 1 and "quote_currency" not in state:
            quote_currency = "USDT"
        elif version == 2 and state.get("quote_currency") == "KRW":
            quote_currency = "KRW"
        else:
            raise ValueError("Paper snapshot version and quote currency do not match")
        exchange = cls(initial_balance=Decimal("0"), fee_pct=state["fee_pct"], slippage_pct=state["slippage_pct"], quote_currency=quote_currency)
        balances = state["balances"]
        if quote_currency not in balances or any(not isinstance(asset, str) or not asset or "/" in asset for asset in balances):
            raise ValueError("Invalid paper balances")
        exchange._balances = {asset: _decimal(amount, positive=False) for asset, amount in balances.items()}
        for symbol, ticker in state["tickers"].items():
            exchange.set_price(symbol, _decimal(ticker["last"]), timestamp=int(ticker["timestamp"]))
        exchange.total_fees = _decimal(state["total_fees"], positive=False)
        next_id = state["next_order_id"]
        if type(next_id) is not int or next_id < 1:
            raise ValueError("Invalid paper order counter")
        exchange._next_order_id = next_id
        for key, raw in state["orders"].items():
            order = deepcopy(raw)
            _, quote = exchange._market_assets(order["symbol"])
            expected_fields = {"id", "clientOrderId", "symbol", "type", "side", "amount", "filled", "remaining", "average", "price", "status", "timestamp", "fee"}
            if set(order) != expected_fields or not isinstance(key, str) or not key or order["clientOrderId"] != key or order["id"] != f"paper-{key}" or order["type"] != "market" or order["status"] != "closed" or order["side"] not in ("buy", "sell"):
                raise ValueError("Invalid paper order snapshot")
            if type(order["timestamp"]) is not int or order["timestamp"] < 0 or order["fee"]["currency"] != quote:
                raise ValueError("Invalid paper order timestamp or fee currency")
            for name in ("amount", "filled", "average", "price"):
                order[name] = _decimal(order[name])
            order["remaining"] = _decimal(order["remaining"], positive=False)
            order["fee"]["cost"] = _decimal(order["fee"]["cost"], positive=False)
            if order["filled"] != order["amount"] or order["remaining"] != 0 or order["price"] != order["average"]:
                raise ValueError("Offline market snapshot must contain complete fills")
            with localcontext(PAPER_CALCULATION_CONTEXT):
                if order["fee"]["cost"] != order["amount"] * order["average"] * exchange.fee_pct / 100:
                    raise ValueError("Paper order fee does not match its fill")
            exchange._orders[key] = order
        for key, raw in state["requests"].items():
            if key not in exchange._orders or len(raw) != 5:
                raise ValueError("Invalid paper deduplication snapshot")
            request = (raw[0], raw[1], raw[2], _decimal(raw[3]), _decimal(raw[4]) if raw[4] is not None else None)
            order = exchange._orders[key]
            if request[:4] != (order["symbol"], order["type"], order["side"], order["amount"]):
                raise ValueError("Paper request does not match its filled order")
            exchange._requests[key] = request
        if set(exchange._orders) != set(exchange._requests):
            raise ValueError("Paper orders and deduplication records do not match")
        trade_ids = [trade["id"] for trade in state["trades"]]
        by_id = {order["id"]: order for order in exchange._orders.values()}
        if len(trade_ids) != len(set(trade_ids)) or set(trade_ids) != set(by_id):
            raise ValueError("Paper trade/order records do not match")
        if state["trades"] != [_serialize(by_id[trade_id]) for trade_id in trade_ids]:
            raise ValueError("Paper trade bodies do not match filled orders")
        ordered = [by_id[trade_id] for trade_id in trade_ids]
        if any(left["timestamp"] > right["timestamp"] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Paper trades are not chronological")
        exchange._orders = {order["clientOrderId"]: order for order in ordered}
        exchange._trades = deepcopy(ordered)
        with localcontext(PAPER_CALCULATION_CONTEXT):
            if exchange.total_fees != sum((order["fee"]["cost"] for order in ordered), Decimal("0")):
                raise ValueError("Paper fee totals do not match filled orders")
        return exchange
