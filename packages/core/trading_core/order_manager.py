"""Order management with idempotency - 주문 관리 (멱등성 보장)"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import asyncio
import logging

from trading_core.exchange import ExchangeInterface

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    PENDING = "pending"      # 생성됨, 아직 미전송
    OPEN = "open"           # 거래소에 접수됨
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class Order:
    """주문 데이터"""
    # 낱등성 키 - 중복 주문 방지
    client_order_id: str
    
    # 거래소 정보
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount: Decimal          # 주문 수량
    price: Optional[Decimal] = None  # 지정가
    
    # 상태
    status: OrderStatus = OrderStatus.PENDING
    filled: Decimal = field(default_factory=lambda: Decimal("0"))
    remaining: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # 거래소 응답
    exchange_order_id: Optional[str] = None
    
    # 메타데이터
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # 전략 정보 (추적용)
    strategy_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'client_order_id': self.client_order_id,
            'symbol': self.symbol,
            'side': self.side.value,
            'order_type': self.order_type.value,
            'amount': str(self.amount),
            'price': str(self.price) if self.price else None,
            'status': self.status.value,
            'filled': str(self.filled),
            'remaining': str(self.remaining),
            'exchange_order_id': self.exchange_order_id,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'strategy_id': self.strategy_id,
        }


class OrderManager:
    """주문 관리자 - 멱등성 및 상태 추적 보장"""
    
    def __init__(self, exchange: ExchangeInterface):
        self.exchange = exchange
        self._orders: Dict[str, Order] = {}  # client_order_id -> Order
        self._pending_orders: Set[str] = set()
        self._lock = asyncio.Lock()
        self._polling_task: Optional[asyncio.Task] = None
        
    async def start(self) -> None:
        """주문 관리 시작"""
        self._polling_task = asyncio.create_task(self._poll_orders_loop())
        logger.info("OrderManager started")
        
    async def stop(self) -> None:
        """주문 관리 중지"""
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        logger.info("OrderManager stopped")
        
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        strategy_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> Order:
        """
        주문 생성
        
        Args:
            symbol: 거래 심볼 (BTC/USDT)
            side: buy/sell
            order_type: market/limit/stop_limit
            amount: 수량
            price: 지정가 (market은 None)
            strategy_id: 전략 식별자
            client_order_id: 외부에서 지정하는 ID (미지정시 자동 생성)
        """
        import uuid
        
        # 멱등성 키 생성
        if client_order_id is None:
            client_order_id = f"order-{uuid.uuid4().hex[:16]}"
        
        async with self._lock:
            # 중복 주문 체크
            if client_order_id in self._orders:
                existing = self._orders[client_order_id]
                logger.warning(f"Duplicate order attempt: {client_order_id}")
                return existing
            
            # 주문 객체 생성
            order = Order(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=amount,
                price=price,
                remaining=amount,
                strategy_id=strategy_id
            )
            
            self._orders[client_order_id] = order
            self._pending_orders.add(client_order_id)
        
        # 거래소에 주문 전송
        try:
            response = await self.exchange.create_order(
                symbol=symbol,
                order_type=order_type.value,
                side=side.value,
                amount=amount,
                price=price,
                params={'clientOrderId': client_order_id}
            )
            
            # 응답 업데이트
            order.exchange_order_id = response.get('id')
            order.status = OrderStatus.OPEN
            order.updated_at = datetime.utcnow()
            
            logger.info(f"Order placed: {client_order_id} -> {order.exchange_order_id}")
            
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.updated_at = datetime.utcnow()
            logger.error(f"Order failed: {client_order_id} - {e}")
            raise
            
        return order
    
    async def cancel_order(self, client_order_id: str) -> Order:
        """주문 취소"""
        async with self._lock:
            if client_order_id not in self._orders:
                raise ValueError(f"Order not found: {client_order_id}")
            
            order = self._orders[client_order_id]
            
            if order.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED]:
                logger.warning(f"Cannot cancel order in status {order.status}: {client_order_id}")
                return order
        
        # 거래소에 취소 요청
        if order.exchange_order_id:
            try:
                await self.exchange.cancel_order(order.exchange_order_id, order.symbol)
                order.status = OrderStatus.CANCELED
                order.updated_at = datetime.utcnow()
                logger.info(f"Order canceled: {client_order_id}")
            except Exception as e:
                logger.error(f"Cancel failed: {client_order_id} - {e}")
                raise
                
        return order
    
    async def get_order(self, client_order_id: str) -> Optional[Order]:
        """주문 조회"""
        async with self._lock:
            return self._orders.get(client_order_id)
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """미체결 주문 목록"""
        async with self._lock:
            orders = [
                o for o in self._orders.values()
                if o.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]
            ]
            if symbol:
                orders = [o for o in orders if o.symbol == symbol]
            return orders
    
    async def get_orders_by_strategy(self, strategy_id: str) -> List[Order]:
        """전략별 주문 조회"""
        async with self._lock:
            return [
                o for o in self._orders.values()
                if o.strategy_id == strategy_id
            ]
    
    async def _poll_orders_loop(self) -> None:
        """주문 상태 폴링 루프"""
        while True:
            try:
                await self._sync_orders()
                await asyncio.sleep(5)  # 5초마다 동기화
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order sync error: {e}")
                await asyncio.sleep(10)
    
    async def _sync_orders(self) -> None:
        """거래소와 주문 상태 동기화"""
        async with self._lock:
            open_orders = [
                o for o in self._orders.values()
                if o.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]
                and o.exchange_order_id
            ]
        
        for order in open_orders:
            try:
                exchange_order = await self.exchange.fetch_order(
                    order.exchange_order_id,
                    order.symbol
                )
                
                # 상태 업데이트
                new_status = self._map_exchange_status(exchange_order.get('status'))
                if new_status != order.status:
                    order.status = new_status
                    order.updated_at = datetime.utcnow()
                    logger.info(f"Order status updated: {order.client_order_id} -> {new_status.value}")
                
                # 체결량 업데이트
                filled = Decimal(str(exchange_order.get('filled', 0)))
                if filled != order.filled:
                    order.filled = filled
                    order.remaining = order.amount - filled
                    
            except Exception as e:
                logger.error(f"Failed to sync order {order.client_order_id}: {e}")
    
    def _map_exchange_status(self, status: str) -> OrderStatus:
        """거래소 상태를 내부 상태로 매핑"""
        status_map = {
            'open': OrderStatus.OPEN,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELED,
            'cancelled': OrderStatus.CANCELED,
            'expired': OrderStatus.EXPIRED,
            'rejected': OrderStatus.REJECTED,
            'pending': OrderStatus.PENDING,
        }
        return status_map.get(status.lower(), OrderStatus.OPEN)
    
    def get_all_orders(self) -> Dict[str, Order]:
        """전체 주문 조회 (복구용)"""
        return self._orders.copy()
    
    def load_orders(self, orders: Dict[str, Order]) -> None:
        """주문 복원 (재시작 시)"""
        self._orders = orders
        logger.info(f"Loaded {len(orders)} orders")
