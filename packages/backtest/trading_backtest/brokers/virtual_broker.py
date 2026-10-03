"""Virtual broker for backtesting - 백테스트용 가상 브로커"""

from dataclasses import dataclass, field, replace
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
import logging

from trading_backtest.trade_report import FillRecord, TradeReport, build_trade_report

logger = logging.getLogger(__name__)

BROKER_CALCULATION_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


def _decimal(value: Any, *, positive: bool = True) -> Decimal:
    """Parse financial inputs without accepting NaN, infinity or invalid signs."""
    try:
        number = Decimal(str(value))
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValueError("Expected a finite decimal value") from exc
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        raise ValueError(
            "Value must be finite and positive"
            if positive else "Value must be finite and non-negative"
        )
    return number


def _utcnow() -> datetime:
    # Preserve the public naive-UTC datetime representation without utcnow().
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELED = "canceled"


@dataclass
class SimulatedOrder:
    """백테스트용 주문"""
    order_id: str
    symbol: str
    side: str  # 'buy' or 'sell'
    order_type: str  # 'market' or 'limit'; unsupported types are rejected
    amount: Decimal
    price: Optional[Decimal] = None
    
    status: OrderStatus = OrderStatus.PENDING
    filled_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    filled_price: Optional[Decimal] = None
    
    created_at: datetime = field(default_factory=_utcnow)
    filled_at: Optional[datetime] = None
    
    # 백테스트 메타데이터
    slippage_pct: Decimal = field(default_factory=lambda: Decimal("0"))
    fee_pct: Decimal = field(default_factory=lambda: Decimal("0.1"))  # 0.1% 기본 수수료


@dataclass
class BrokerConfig:
    """브로커 시뮬레이션 설정"""
    # 슬리피지
    slippage_pct: Decimal = Decimal("0.05")  # 0.05% 기본 슬리피지
    slippage_volatility_factor: bool = True  # 변동성 기반 슬리피지 조정
    
    # 수수료
    maker_fee_pct: Decimal = Decimal("0.1")  # 지정가 수수료
    taker_fee_pct: Decimal = Decimal("0.1")  # 시장가 수수료
    
    # 체결 설정
    partial_fill_probability: float = 0.0  # 부분 체결 확률 (0-1)
    min_fill_delay_bars: int = 0  # 최소 체결 지연 (캔들 수)
    max_fill_delay_bars: int = 0  # 최대 체결 지연 (캔들 수)
    
    # 유동성 설정
    min_volume_threshold: Decimal = Decimal("1000")  # 최소 거래량 임계값


class VirtualBroker:
    """Offline spot accounting with caller-supplied bars and valuation prices.

    Limit triggers and optional volatility slippage approximate a completed
    OHLCV bar; they do not model intrabar event ordering. Open-only market bars
    work with fixed slippage. Stop and partial-fill execution are unsupported.
    """
    
    def __init__(self, config: Optional[BrokerConfig] = None, initial_balance: Decimal = Decimal("10000")):
        config = config or BrokerConfig()
        financial_config = {}
        for name in ("slippage_pct", "maker_fee_pct", "taker_fee_pct"):
            value = _decimal(getattr(config, name), positive=False)
            if value >= 100:
                raise ValueError("Broker fee/slippage percentages must be below 100")
            financial_config[name] = value
        financial_config["min_volume_threshold"] = _decimal(
            config.min_volume_threshold, positive=False
        )
        self.config = replace(config, **financial_config)
        self.balance = _decimal(initial_balance, positive=False)
        self.initial_balance = self.balance
        
        # 주문 관리
        self.orders: List[SimulatedOrder] = []
        self.pending_orders: Dict[str, SimulatedOrder] = {}
        
        # 포지션
        self.positions: Dict[str, Decimal] = {}  # symbol -> size
        self._current_prices: Dict[str, Decimal] = {}
        
        # 거래 내역
        self.trades: List[Dict] = []
        # Exact successful-fill inputs for reporting; raw compatibility dicts stay unchanged.
        self._report_fills: List[FillRecord] = []
        
        # 현재 시뮬레이션 상태
        self.current_bar: Optional[Dict] = None
        self.bar_index: int = 0
        
        # 누적 수수료
        self.total_fees: Decimal = Decimal("0")
        
    def submit_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        order_type: str = "market",
        price: Optional[Decimal] = None,
        current_bar: Optional[Dict] = None
    ) -> SimulatedOrder:
        """
        주문 제출
        
        Args:
            symbol: 거래 심볼
            side: 'buy' or 'sell'
            amount: 수량
            order_type: 'market' or 'limit'; other types are rejected
            price: 지정가 (order_type이 market이면 None)
            current_bar: 현재 캔들 데이터 (OHLCV)
        """
        import uuid
        
        order_id = f"sim-{uuid.uuid4().hex[:12]}"
        
        order = SimulatedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price,
            fee_pct=self.config.taker_fee_pct if order_type == "market" else self.config.maker_fee_pct
        )
        
        self.orders.append(order)
        try:
            order.amount, order.price, order.fee_pct = self._validate_order(order)
        except ValueError as exc:
            self._reject_order(order, str(exc))
        else:
            # An unsuccessful immediate attempt remains queued only while pending.
            if current_bar is not None:
                self._try_fill_order(order, current_bar)
            if order.status == OrderStatus.PENDING:
                self.pending_orders[order_id] = order
        logger.debug(f"Order submitted: {order_id} {side} {amount} {symbol} @ {order_type}")
        
        return order

    def _validate_order(
        self, order: SimulatedOrder
    ) -> Tuple[Decimal, Optional[Decimal], Decimal]:
        if (
            not isinstance(order.symbol, str)
            or not order.symbol
            or order.symbol != order.symbol.strip()
        ):
            raise ValueError("Order symbol must be a non-empty string")
        if order.side not in ("buy", "sell"):
            raise ValueError("Only spot buy and sell orders are supported")
        if order.order_type not in ("market", "limit"):
            raise ValueError("Only market and limit orders are supported")
        amount = _decimal(order.amount)
        price = _decimal(order.price) if order.price is not None else None
        if order.order_type == "limit" and price is None:
            raise ValueError("Limit orders require a positive price")
        fee_pct = _decimal(order.fee_pct, positive=False)
        if fee_pct >= 100:
            raise ValueError("Order fee percentage must be below 100")
        return amount, price, fee_pct

    def _reject_order(self, order: SimulatedOrder, reason: str) -> None:
        # Rejections are terminal, with all fill fields and accounting untouched.
        order.status = OrderStatus.REJECTED
        self.pending_orders.pop(order.order_id, None)
        logger.debug("Order rejected: %s: %s", order.order_id, reason)
    
    def _try_fill_order(self, order: SimulatedOrder, bar: Dict) -> bool:
        """
        주문 체결 시도
        
        Returns:
            True if filled, False otherwise
        """
        if order.status != OrderStatus.PENDING:
            return False
        symbol = order.symbol
        try:
            amount, limit_price, fee_pct = self._validate_order(order)
            prices = {
                key: _decimal(bar[key])
                for key in ("open", "high", "low", "close") if key in bar
            }
            if "high" in prices and "low" in prices and prices["high"] < prices["low"]:
                raise ValueError("Candle high cannot be below low")
            volume = _decimal(bar.get("volume", 0), positive=False)
            volume_threshold = _decimal(self.config.min_volume_threshold, positive=False)
            if volume < volume_threshold:
                return False

            if order.order_type == "market":
                fill_price = prices.get("open", prices.get("close"))
                if fill_price is None:
                    raise ValueError("Market execution requires a positive open or close")
            else:
                trigger = "low" if order.side == "buy" else "high"
                if trigger not in prices:
                    raise ValueError("Limit execution requires its candle trigger price")
                if (
                    (order.side == "buy" and prices["low"] > limit_price)
                    or (order.side == "sell" and prices["high"] < limit_price)
                ):
                    return False
                fill_price = limit_price

            # Compute everything before publishing a fill or changing the ledger.
            with localcontext(BROKER_CALCULATION_CONTEXT):
                slippage = self._calculate_slippage(bar)
                direction = Decimal("1") if order.side == "buy" else Decimal("-1")
                fill_price = _decimal(fill_price * (Decimal("1") + direction * slippage))
                notional = _decimal(fill_price * amount)
                fee = _decimal(notional * fee_pct / 100, positive=False)
                balance = _decimal(self.balance, positive=False)
                holding = _decimal(self.positions.get(symbol, Decimal("0")), positive=False)
                if order.side == "buy":
                    cost = notional + fee
                    if cost > balance:
                        raise ValueError("Insufficient spot cash balance")
                    new_balance = _decimal(balance - cost, positive=False)
                    new_holding = _decimal(holding + amount, positive=False)
                else:
                    if amount > holding:
                        raise ValueError("Insufficient spot asset balance")
                    new_balance = _decimal(balance + notional - fee, positive=False)
                    new_holding = _decimal(holding - amount, positive=False)
                new_fees = _decimal(self.total_fees + fee, positive=False)
                slippage_pct = slippage * 100
            mark = prices.get("close", prices.get("open", limit_price))
            trade = {
                'order_id': order.order_id,
                'symbol': symbol,
                'side': order.side,
                'amount': float(amount),
                'price': float(fill_price),
                'fee': float(fee),
                'timestamp': bar.get('timestamp'),
                'bar_index': self.bar_index,
            }
            report_fill = FillRecord(order.order_id, symbol, order.side, amount, notional,
                                     fee, holding, new_holding, deepcopy(bar.get('timestamp')))
        except (ValueError, DecimalException) as exc:
            self._reject_order(order, str(exc))
            return False

        self.balance = new_balance
        if new_holding:
            self.positions[symbol] = new_holding
        else:
            self.positions.pop(symbol, None)
        self.total_fees = new_fees
        if mark is not None:
            self._current_prices[symbol] = mark
        self.trades.append(trade)
        self._report_fills.append(report_fill)
        order.status = OrderStatus.FILLED
        order.filled_amount = amount
        order.filled_price = fill_price
        order.filled_at = _utcnow()
        order.slippage_pct = slippage_pct
        self.pending_orders.pop(order.order_id, None)
        
        logger.debug(f"Order filled: {order.order_id} @ {fill_price}")
        return True
    
    def _calculate_slippage(self, bar: Dict) -> Decimal:
        """슬리피지 계산"""
        percentage = _decimal(self.config.slippage_pct, positive=False)
        if percentage >= 100:
            raise ValueError("Broker slippage percentage must be below 100")
        with localcontext(BROKER_CALCULATION_CONTEXT):
            base_slippage = percentage / 100
            if (
                not self.config.slippage_volatility_factor
                or not all(key in bar for key in ("high", "low", "open"))
            ):
                return base_slippage
            high, low, open_price = (_decimal(bar[key]) for key in ("high", "low", "open"))
            if high < low:
                raise ValueError("Candle high cannot be below low")
            volatility = (high - low) / open_price
            adjusted_slippage = base_slippage * (Decimal("1") + volatility * 10)
            return min(adjusted_slippage, Decimal("0.01"))
    
    def process_bar(self, bar: Dict) -> None:
        """
        새로운 캔들 바 처리 - 대기 중인 주문 체결 시도
        """
        self.current_bar = bar
        self.bar_index += 1
        
        # 대기 중인 주문 처리
        for order_id, order in list(self.pending_orders.items()):
            self._try_fill_order(order, bar)
            if order.status != OrderStatus.PENDING:
                self.pending_orders.pop(order_id, None)
    
    def get_position(self, symbol: str) -> Decimal:
        """포지션 조회"""
        return self.positions.get(symbol, Decimal("0"))
    
    def get_total_value(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """
        총 자산 가치 계산
        
        Args:
            current_prices: {symbol: price} 형태의 현재가
        """
        prices = {symbol: _decimal(price) for symbol, price in current_prices.items()}
        marks = {**self._current_prices, **prices}
        with localcontext(BROKER_CALCULATION_CONTEXT):
            value = self.balance
            for symbol, size in self.positions.items():
                if size:
                    if symbol not in marks:
                        raise ValueError(f"No local valuation price supplied for {symbol}")
                    value += _decimal(size, positive=False) * marks[symbol]
            value = _decimal(value, positive=False)
        # Invalid or incomplete valuation attempts cannot replace good marks.
        self._current_prices.update(prices)
        return value
    
    def get_performance_summary(self, current_prices: Optional[Dict[str, Decimal]] = None) -> Dict:
        """Report marked equity and cash using explicit or previously recorded marks.

        Holdings remain open; marks do not imply liquidation or exit fees.
        A percentage return is undefined (None) when initial cash is zero.
        """
        final_value = self.get_total_value(current_prices if current_prices is not None else {})
        with localcontext(BROKER_CALCULATION_CONTEXT):
            position_value = final_value - self.balance
            total_return = (
                (final_value - self.initial_balance) / self.initial_balance * 100
                if self.initial_balance else None
            )
        
        return {
            'initial_balance': float(self.initial_balance),
            'final_balance': float(self.balance),
            'final_equity': float(final_value),
            'position_value': float(position_value),
            'total_return_pct': float(total_return) if total_return is not None else None,
            'total_trades': len(self.trades),
            'total_fees': float(self.total_fees),
            'open_positions': len(self.positions),
        }

    def get_trade_report(self) -> TradeReport:
        """Return frozen financial fields and isolated timestamp metadata in quote units."""
        return build_trade_report(tuple(self._report_fills))
    
    def reset(self) -> None:
        """브로커 초기화"""
        self.balance = self.initial_balance
        self.orders.clear()
        self.pending_orders.clear()
        self.positions.clear()
        self._current_prices.clear()
        self.trades.clear()
        self._report_fills.clear()
        self.current_bar = None
        self.bar_index = 0
        self.total_fees = Decimal("0")
