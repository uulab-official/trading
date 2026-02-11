"""WebSocket client for real-time data - 실시간 데이터 수신"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)


class WebSocketClient(ABC):
    """WebSocket 클라이언트 인터페이스"""
    
    def __init__(self, url: str):
        self.url = url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._handlers: Dict[str, List[Callable]] = {}
        self._subscribed_symbols: Set[str] = set()
        
    def on(self, event: str, handler: Callable) -> None:
        """이벤트 핸들러 등록"""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)
        
    def off(self, event: str, handler: Callable) -> None:
        """이벤트 핸들러 제거"""
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h != handler]
    
    async def _emit(self, event: str, data: any) -> None:
        """이벤트 발생"""
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"Handler error for {event}: {e}")
    
    async def connect(self) -> None:
        """WebSocket 연결"""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self.url)
        self._running = True
        
        # 연결 후 구독 복원
        for symbol in self._subscribed_symbols:
            await self._subscribe_symbol(symbol)
        
        logger.info(f"WebSocket connected: {self.url}")
        
        # 메시지 루프 시작
        asyncio.create_task(self._message_loop())
        
    async def disconnect(self) -> None:
        """WebSocket 연결 해제"""
        self._running = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
            
        if self._session:
            await self._session.close()
            self._session = None
            
        logger.info("WebSocket disconnected")
    
    async def _message_loop(self) -> None:
        """메시지 수신 루프"""
        while self._running and self._ws:
            try:
                msg = await self._ws.receive()
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    await self._emit('error', msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed")
                    break
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Message loop error: {e}")
                await asyncio.sleep(1)
    
    @abstractmethod
    async def _handle_message(self, data: Dict) -> None:
        """메시지 처리 (구현 필요)"""
        pass
    
    @abstractmethod
    async def subscribe_ticker(self, symbol: str) -> None:
        """티커 구독"""
        pass
    
    @abstractmethod
    async def subscribe_orderbook(self, symbol: str) -> None:
        """오더북 구독"""
        pass
    
    @abstractmethod
    async def subscribe_trades(self, symbol: str) -> None:
        """체결 내역 구독"""
        pass
    
    @abstractmethod
    async def _subscribe_symbol(self, symbol: str) -> None:
        """낶부 구독 메서드"""
        pass


class BinanceWebSocket(WebSocketClient):
    """바이낸스 WebSocket 구현"""
    
    def __init__(self, testnet: bool = True):
        base_url = (
            "wss://testnet.binance.vision/ws" if testnet
            else "wss://stream.binance.com:9443/ws"
        )
        super().__init__(base_url)
        self.testnet = testnet
        self._stream_url: Optional[str] = None
        
    async def connect(self) -> None:
        """스트림 연결 (콤바인드 스트림 사용)"""
        # 스트림 목록이 있으면 콤바인드 스트림으로 연결
        if self._subscribed_symbols:
            streams = []
            for symbol in self._subscribed_symbols:
                streams.append(f"{symbol.lower()}@ticker")
            
            stream_path = "/".join(streams)
            base = self.url.replace('/ws', '')
            self._stream_url = f"{base}/stream?streams={stream_path}"
            self.url = self._stream_url
        
        await super().connect()
    
    async def _handle_message(self, data: Dict) -> None:
        """바이낸스 메시지 처리"""
        # 콤바인드 스트림 응답 처리
        if 'stream' in data and 'data' in data:
            stream = data['stream']
            message = data['data']
            
            if '@ticker' in stream:
                await self._handle_ticker(message)
            elif '@depth' in stream:
                await self._emit('orderbook', message)
            elif '@trade' in stream:
                await self._emit('trade', message)
                
        # 단일 스트림 응답
        elif 'e' in data:
            event_type = data['e']
            
            if event_type == '24hrTicker':
                await self._handle_ticker(data)
            elif event_type == 'depthUpdate':
                await self._emit('orderbook', data)
            elif event_type == 'trade':
                await self._emit('trade', data)
    
    async def _handle_ticker(self, data: Dict) -> None:
        """티커 데이터 처리"""
        ticker = {
            'symbol': data.get('s'),
            'price': Decimal(data.get('c', '0')),      # 현재가
            'bid': Decimal(data.get('b', '0')),        # 매수호가
            'ask': Decimal(data.get('a', '0')),        # 매도호가
            'volume': Decimal(data.get('v', '0')),     # 거래량
            'change_24h': Decimal(data.get('P', '0')), # 24시간 변동률
            'timestamp': data.get('E', 0),
        }
        await self._emit('ticker', ticker)
    
    async def subscribe_ticker(self, symbol: str) -> None:
        """실시간 티커 구독"""
        symbol = symbol.replace('/', '').lower()
        self._subscribed_symbols.add(symbol)
        
        if self._ws:
            await self._subscribe_symbol(symbol)
    
    async def subscribe_orderbook(self, symbol: str) -> None:
        """오더북 구독"""
        symbol = symbol.replace('/', '').lower()
        self._subscribed_symbols.add(symbol)
        
        if self._ws:
            msg = {
                "method": "SUBSCRIBE",
                "params": [f"{symbol}@depth"],
                "id": 1
            }
            await self._ws.send_json(msg)
    
    async def subscribe_trades(self, symbol: str) -> None:
        """체결 내역 구독"""
        symbol = symbol.replace('/', '').lower()
        self._subscribed_symbols.add(symbol)
        
        if self._ws:
            msg = {
                "method": "SUBSCRIBE",
                "params": [f"{symbol}@trade"],
                "id": 1
            }
            await self._ws.send_json(msg)
    
    async def _subscribe_symbol(self, symbol: str) -> None:
        """심볼 구독"""
        if not self._ws:
            return
            
        msg = {
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@ticker"],
            "id": 1
        }
        await self._ws.send_json(msg)
        logger.debug(f"Subscribed to {symbol} ticker")


class DataAggregator:
    """WebSocket 데이터 집계기 - 여러 소스 통합"""
    
    def __init__(self):
        self._clients: List[WebSocketClient] = []
        self._latest_prices: Dict[str, Decimal] = {}
        self._handlers: Dict[str, List[Callable]] = {}
        
    def add_client(self, client: WebSocketClient) -> None:
        """클라이언트 추가"""
        self._clients.append(client)
        
        # 가격 업데이트 핸들러
        async def on_ticker(data):
            symbol = data.get('symbol')
            price = data.get('price')
            if symbol and price:
                self._latest_prices[symbol] = price
                await self._emit('price', {'symbol': symbol, 'price': price})
        
        client.on('ticker', on_ticker)
        
    async def connect_all(self) -> None:
        """모든 클라이언트 연결"""
        await asyncio.gather(
            *[client.connect() for client in self._clients],
            return_exceptions=True
        )
        
    async def disconnect_all(self) -> None:
        """모든 클라이언트 연결 해제"""
        await asyncio.gather(
            *[client.disconnect() for client in self._clients],
            return_exceptions=True
        )
        
    def on(self, event: str, handler: Callable) -> None:
        """이벤트 핸들러 등록"""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(handler)
        
    async def _emit(self, event: str, data: any) -> None:
        """이벤트 발생"""
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"Handler error: {e}")
    
    def get_price(self, symbol: str) -> Optional[Decimal]:
        """최신 가격 조회"""
        return self._latest_prices.get(symbol)
    
    def get_all_prices(self) -> Dict[str, Decimal]:
        """전체 가격 조회"""
        return self._latest_prices.copy()
