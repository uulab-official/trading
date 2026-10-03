"""Offline spot-market emulator using only caller-supplied prices and candles.

This deliberately has no credential fields, sockets, HTTP client, or fallback to
an exchange. Fills are frictionless; it is not a profitability simulation.
"""

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from trading_core.exchange import Balance, ExchangeInterface, Market, Ticker


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


class PaperExchange(ExchangeInterface):
    def __init__(self, initial_balance: Decimal = Decimal("10000"), initial_positions: Optional[Mapping[str, Decimal]] = None):
        self._balances = {"USDT": _decimal(initial_balance, positive=False)}
        for symbol, amount in (initial_positions or {}).items():
            base, quote = _assets(symbol)
            if quote != "USDT":
                raise ValueError("The offline initial balance is denominated in USDT")
            self._balances[base] = _decimal(amount, positive=False)
        self._tickers: Dict[str, Ticker] = {}
        self._candles = {}
        self._orders = {}
        self._requests = {}
        self._trades = []
        self._next_order_id = 1
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Paper exchange is disconnected")

    def set_price(self, symbol: str, price: Decimal, *, timestamp: int = 0) -> None:
        _assets(symbol)
        price = _decimal(price)
        self._tickers[symbol] = Ticker(symbol, price, price, price, Decimal("0"), timestamp)

    def set_ohlcv(self, symbol: str, timeframe: str, candles: List[List[Any]]) -> None:
        _assets(symbol)
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
        base, quote = _assets(symbol)
        amount = _decimal(amount)
        if order_type != "market" or side not in ("buy", "sell"):
            raise ValueError("Offline paper execution supports market buys and sells only")
        key = (params or {}).get("clientOrderId")
        if not key:
            key = f"paper-local-{self._next_order_id}"
            while key in self._requests:
                self._next_order_id += 1
                key = f"paper-local-{self._next_order_id}"
            self._next_order_id += 1
        request = (symbol, order_type, side, amount, price)
        if key in self._requests:
            if self._requests[key] != request:
                raise ValueError("A paper clientOrderId cannot be reused for a different order")
            return dict(self._orders[key])
        ticker = await self.fetch_ticker(symbol)
        cost = amount * ticker.last
        if side == "buy":
            if cost > self._balances.get(quote, Decimal("0")):
                raise ValueError("Insufficient paper quote balance")
            self._balances[quote] -= cost
            self._balances[base] = self._balances.get(base, Decimal("0")) + amount
        else:
            if amount > self._balances.get(base, Decimal("0")):
                raise ValueError("Insufficient paper asset balance")
            self._balances[base] -= amount
            self._balances[quote] = self._balances.get(quote, Decimal("0")) + cost
        order = {"id": f"paper-{key}", "clientOrderId": key, "symbol": symbol, "type": order_type, "side": side,
                 "amount": amount, "filled": amount, "remaining": Decimal("0"), "average": ticker.last,
                 "price": ticker.last, "status": "closed", "timestamp": ticker.timestamp}
        self._requests[key] = request
        self._orders[key] = order
        self._trades.append(dict(order))
        return dict(order)

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        order = await self.fetch_order(order_id, symbol)
        raise ValueError("Paper market orders fill immediately and cannot be cancelled")

    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        self._ensure_connected()
        for order in self._orders.values():
            if order["id"] == order_id and order["symbol"] == symbol:
                return dict(order)
        raise ValueError("Unknown paper order")

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_connected()
        return []

    async def fetch_my_trades(self, symbol: Optional[str] = None, since: Optional[int] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        self._ensure_connected()
        trades = [dict(trade) for trade in self._trades if (symbol is None or trade["symbol"] == symbol) and (since is None or trade["timestamp"] >= since)]
        return trades[-limit:] if limit is not None and limit > 0 else trades
