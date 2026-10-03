"""Exchange abstraction layer - 거래소 추상화"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import asyncio

if TYPE_CHECKING:
    import ccxt.async_support as ccxt


@dataclass
class Balance:
    """계좌 잔고"""
    asset: str
    free: Decimal      # 사용 가능
    used: Decimal      # 주문에 묶인 금액
    total: Decimal     # 총 보유
    
    @property
    def available(self) -> Decimal:
        return self.free


@dataclass
class Ticker:
    """현재가 정보"""
    symbol: str
    bid: Decimal       # 매수 호가
    ask: Decimal       # 매도 호가
    last: Decimal      # 최종 체결가
    volume: Decimal    # 24시간 거래량
    timestamp: int     # 밀리초


@dataclass  
class Market:
    """거래 시장 정보"""
    symbol: str
    base: str          # 기준 통화 (BTC)
    quote: str         # 거래 통화 (USDT)
    min_amount: Decimal
    min_cost: Decimal
    precision: int
    active: bool


class ExchangeInterface(ABC):
    """거래소 인터페이스 - 모든 거래소는 이를 구현"""
    
    @abstractmethod
    async def connect(self) -> None:
        """거래소 연결"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """거래소 연결 해제"""
        pass
    
    @abstractmethod
    async def fetch_balance(self) -> Dict[str, Balance]:
        """전체 잔고 조회"""
        pass
    
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        """특정 심볼 현재가"""
        pass
    
    @abstractmethod
    async def fetch_markets(self) -> List[Market]:
        """거래 가능한 시장 목록"""
        pass
    
    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """주문 생성"""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """주문 취소"""
        pass
    
    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """주문 상태 조회"""
        pass
    
    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """미체결 주문 조회"""
        pass
    
    @abstractmethod
    async def fetch_my_trades(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """내 체결 내역"""
        pass


class CCXTExchange(ExchangeInterface):
    """CCXT 기반 거래소 구현"""
    
    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        sandbox: bool = True,
        options: Optional[Dict] = None
    ):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        self.options = options or {}
        self._exchange: Optional[ccxt.Exchange] = None
        
    async def connect(self) -> None:
        """CCXT 거래소 초기화"""
        # The live dependency is never imported by credential-free paper execution.
        import ccxt.async_support as ccxt
        exchange_class = getattr(ccxt, self.exchange_id)
        self._exchange = exchange_class({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',  # 현물 거래
                **self.options
            }
        })
        
        if self.sandbox:
            self._exchange.set_sandbox_mode(True)
            
        await self._exchange.load_markets()
        
    async def disconnect(self) -> None:
        """연결 해제"""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
            
    def _ensure_connected(self) -> "ccxt.Exchange":
        if not self._exchange:
            raise RuntimeError("Exchange not connected. Call connect() first.")
        return self._exchange
    
    async def fetch_balance(self) -> Dict[str, Balance]:
        """잔고 조회"""
        ex = self._ensure_connected()
        response = await ex.fetch_balance()
        
        balances = {}
        for asset, data in response.get('total', {}).items():
            if data > 0:  # 0보다 큰 잔고만
                free = Decimal(str(response.get('free', {}).get(asset, 0)))
                used = Decimal(str(response.get('used', {}).get(asset, 0)))
                total = Decimal(str(data))
                balances[asset] = Balance(
                    asset=asset,
                    free=free,
                    used=used,
                    total=total
                )
        return balances
    
    async def fetch_ticker(self, symbol: str) -> Ticker:
        """현재가 조회"""
        ex = self._ensure_connected()
        ticker = await ex.fetch_ticker(symbol)
        
        return Ticker(
            symbol=symbol,
            bid=Decimal(str(ticker.get('bid', 0))),
            ask=Decimal(str(ticker.get('ask', 0))),
            last=Decimal(str(ticker.get('last', 0))),
            volume=Decimal(str(ticker.get('quoteVolume', 0))),
            timestamp=ticker.get('timestamp', 0)
        )
    
    async def fetch_markets(self) -> List[Market]:
        """거래 시장 목록"""
        ex = self._ensure_connected()
        markets = await ex.fetch_markets()
        
        result = []
        for m in markets:
            if m.get('spot', False):  # 현물만
                limits = m.get('limits', {})
                amount_limits = limits.get('amount', {})
                cost_limits = limits.get('cost', {})
                precision = m.get('precision', {})
                
                result.append(Market(
                    symbol=m['symbol'],
                    base=m['base'],
                    quote=m['quote'],
                    min_amount=Decimal(str(amount_limits.get('min', 0))),
                    min_cost=Decimal(str(cost_limits.get('min', 0))),
                    precision=precision.get('price', 8),
                    active=m.get('active', True)
                ))
        return result
    
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """주문 생성"""
        ex = self._ensure_connected()
        
        # Idempotency key 추가 (중복 주문 방지)
        order_params = params or {}
        if 'clientOrderId' not in order_params:
            import uuid
            order_params['clientOrderId'] = f"bot-{uuid.uuid4().hex[:16]}"
        
        return await ex.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=float(amount),
            price=float(price) if price else None,
            params=order_params
        )
    
    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """주문 취소"""
        ex = self._ensure_connected()
        return await ex.cancel_order(order_id, symbol)
    
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """주문 조회"""
        ex = self._ensure_connected()
        return await ex.fetch_order(order_id, symbol)
    
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """미체결 주문 조회"""
        ex = self._ensure_connected()
        return await ex.fetch_open_orders(symbol)
    
    async def fetch_my_trades(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """체결 내역"""
        ex = self._ensure_connected()
        return await ex.fetch_my_trades(symbol, since, limit)
