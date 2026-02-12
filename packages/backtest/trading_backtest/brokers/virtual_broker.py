"""Virtual broker for backtesting - 백테스트용 가상 브로커"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


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
    order_type: str  # 'market', 'limit', 'stop'
    amount: Decimal
    price: Optional[Decimal] = None
    
    status: OrderStatus = OrderStatus.PENDING
    filled_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    filled_price: Optional[Decimal] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
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
    """백테스트용 가상 브로커 - 실제 거래소 동작 시뮬레이션"""
    
    def __init__(self, config: Optional[BrokerConfig] = None, initial_balance: Decimal = Decimal("10000")):
        self.config = config or BrokerConfig()
        self.balance = initial_balance
        self.initial_balance = initial_balance
        
        # 주문 관리
        self.orders: List[SimulatedOrder] = []
        self.pending_orders: Dict[str, SimulatedOrder] = {}
        
        # 포지션
        self.positions: Dict[str, Decimal] = {}  # symbol -> size
        
        # 거래 내역
        self.trades: List[Dict] = []
        
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
            order_type: 'market', 'limit', 'stop'
            price: 지정가/스탑가 (order_type이 market이면 None)
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
        
        # 현재 바가 제공되면 즉시 체결 시도
        if current_bar is not None:
            self._try_fill_order(order, current_bar)
        else:
            self.pending_orders[order_id] = order
        
        self.orders.append(order)
        logger.debug(f"Order submitted: {order_id} {side} {amount} {symbol} @ {order_type}")
        
        return order
    
    def _try_fill_order(self, order: SimulatedOrder, bar: Dict) -> bool:
        """
        주문 체결 시도
        
        Returns:
            True if filled, False otherwise
        """
        symbol = order.symbol
        
        # 거래량 체크
        volume = Decimal(str(bar.get('volume', 0)))
        if volume < self.config.min_volume_threshold:
            logger.debug(f"Insufficient volume: {volume} < {self.config.min_volume_threshold}")
            return False
        
        # 가격 결정
        if order.order_type == "market":
            # 시장가: 시가 또는 현재가
            fill_price = Decimal(str(bar.get('open', bar.get('close', 0))))
        elif order.order_type == "limit":
            # 지정가: 가격 도달 여부 체크
            if order.side == "buy":
                # 매수 지정가: 저가 <= 지정가
                if Decimal(str(bar.get('low', 0))) > order.price:
                    return False
                fill_price = order.price
            else:
                # 매도 지정가: 고가 >= 지정가
                if Decimal(str(bar.get('high', 0))) < order.price:
                    return False
                fill_price = order.price
        else:
            fill_price = Decimal(str(bar.get('close', 0)))
        
        # 슬리피지 적용
        slippage = self._calculate_slippage(bar)
        if order.side == "buy":
            fill_price = fill_price * (Decimal("1") + slippage)
        else:
            fill_price = fill_price * (Decimal("1") - slippage)
        
        # 주문 체결
        order.status = OrderStatus.FILLED
        order.filled_amount = order.amount
        order.filled_price = fill_price
        order.filled_at = datetime.utcnow()
        order.slippage_pct = slippage * 100
        
        # 수수료 계산
        fee = fill_price * order.amount * (order.fee_pct / 100)
        self.total_fees += fee
        
        # 잔고 업데이트
        if order.side == "buy":
            cost = fill_price * order.amount + fee
            if cost > self.balance:
                order.status = OrderStatus.REJECTED
                logger.warning(f"Order rejected: insufficient balance {self.balance} < {cost}")
                return False
            self.balance -= cost
            self.positions[symbol] = self.positions.get(symbol, Decimal("0")) + order.amount
        else:
            # 매도
            revenue = fill_price * order.amount - fee
            self.balance += revenue
            self.positions[symbol] = self.positions.get(symbol, Decimal("0")) - order.amount
            if self.positions[symbol] <= 0:
                del self.positions[symbol]
        
        # 거래 기록
        self.trades.append({
            'order_id': order.order_id,
            'symbol': symbol,
            'side': order.side,
            'amount': float(order.amount),
            'price': float(fill_price),
            'fee': float(fee),
            'timestamp': bar.get('timestamp'),
            'bar_index': self.bar_index
        })
        
        logger.debug(f"Order filled: {order.order_id} @ {fill_price}")
        return True
    
    def _calculate_slippage(self, bar: Dict) -> Decimal:
        """슬리피지 계산"""
        if not self.config.slippage_volatility_factor:
            return self.config.slippage_pct / 100
        
        # 변동성 기반 슬리피지
        high = Decimal(str(bar.get('high', 0)))
        low = Decimal(str(bar.get('low', 0)))
        open_price = Decimal(str(bar.get('open', 0)))
        
        if open_price == 0:
            return self.config.slippage_pct / 100
        
        volatility = (high - low) / open_price
        base_slippage = self.config.slippage_pct / 100
        
        # 변동성이 높을수록 슬리피지 증가
        adjusted_slippage = base_slippage * (Decimal("1") + volatility * 10)
        
        return min(adjusted_slippage, Decimal("0.01"))  # 최대 1%로 제한
    
    def process_bar(self, bar: Dict) -> None:
        """
        새로운 캔들 바 처리 - 대기 중인 주문 체결 시도
        """
        self.current_bar = bar
        self.bar_index += 1
        
        # 대기 중인 주문 처리
        for order_id, order in list(self.pending_orders.items()):
            if self._try_fill_order(order, bar):
                del self.pending_orders[order_id]
    
    def get_position(self, symbol: str) -> Decimal:
        """포지션 조회"""
        return self.positions.get(symbol, Decimal("0"))
    
    def get_total_value(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """
        총 자산 가치 계산
        
        Args:
            current_prices: {symbol: price} 형태의 현재가
        """
        position_value = Decimal("0")
        for symbol, size in self.positions.items():
            price = current_prices.get(symbol, Decimal("0"))
            position_value += size * price
        
        return self.balance + position_value
    
    def get_performance_summary(self) -> Dict:
        """성과 요약"""
        final_value = self.balance
        for symbol in self.positions:
            # 백테스트 종료 시 포지션은 청산 가정
            pass
        
        total_return = (self.balance - self.initial_balance) / self.initial_balance * 100
        
        return {
            'initial_balance': float(self.initial_balance),
            'final_balance': float(self.balance),
            'total_return_pct': float(total_return),
            'total_trades': len(self.trades),
            'total_fees': float(self.total_fees),
            'open_positions': len(self.positions),
        }
    
    def reset(self) -> None:
        """브로커 초기화"""
        self.balance = self.initial_balance
        self.orders.clear()
        self.pending_orders.clear()
        self.positions.clear()
        self.trades.clear()
        self.current_bar = None
        self.bar_index = 0
        self.total_fees = Decimal("0")
