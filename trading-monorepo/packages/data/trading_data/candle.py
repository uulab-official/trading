"""Candle data structures - 캔들 데이터 관리"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from collections import deque


@dataclass
class Candle:
    """OHLCV 캔들 데이터"""
    timestamp: int           # 밀리초 단위 시작 시간
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    
    @property
    def datetime(self) -> datetime:
        return datetime.utcfromtimestamp(self.timestamp / 1000)
    
    @property
    def range(self) -> Decimal:
        """고가 - 저가"""
        return self.high - self.low
    
    @property
    def body(self) -> Decimal:
        """몸통 (시가 - 종가)"""
        return abs(self.close - self.open)
    
    @property
    def is_bullish(self) -> bool:
        """양봉 여부"""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """음봉 여부"""
        return self.close < self.open


class TimeFrame:
    """시간대 상수"""
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    HOUR_2 = "2h"
    HOUR_4 = "4h"
    HOUR_6 = "6h"
    HOUR_8 = "8h"
    HOUR_12 = "12h"
    DAY_1 = "1d"
    DAY_3 = "3d"
    WEEK_1 = "1w"
    
    @classmethod
    def to_minutes(cls, timeframe: str) -> int:
        """시간대를 분으로 변환"""
        mapping = {
            cls.MINUTE_1: 1,
            cls.MINUTE_3: 3,
            cls.MINUTE_5: 5,
            cls.MINUTE_15: 15,
            cls.MINUTE_30: 30,
            cls.HOUR_1: 60,
            cls.HOUR_2: 120,
            cls.HOUR_4: 240,
            cls.HOUR_6: 360,
            cls.HOUR_8: 480,
            cls.HOUR_12: 720,
            cls.DAY_1: 1440,
            cls.DAY_3: 4320,
            cls.WEEK_1: 10080,
        }
        return mapping.get(timeframe, 60)
    
    @classmethod
    def to_milliseconds(cls, timeframe: str) -> int:
        """시간대를 밀리초로 변환"""
        return cls.to_minutes(timeframe) * 60 * 1000


class CandleBuffer:
    """캔들 버퍼 - 고정 크기의 캔들 데이터 유지"""
    
    def __init__(self, max_size: int = 500, timeframe: str = TimeFrame.HOUR_4):
        self.max_size = max_size
        self.timeframe = timeframe
        self._candles: deque[Candle] = deque(maxlen=max_size)
        self._current: Optional[Candle] = None
        
    def add_tick(self, timestamp: int, price: Decimal, volume: Decimal = Decimal("0")) -> Optional[Candle]:
        """
        실시간 틱 데이터 추가
        
        Returns:
            완성된 캔들 (새 캔들 시작 시)
        """
        timeframe_ms = TimeFrame.to_milliseconds(self.timeframe)
        candle_start = (timestamp // timeframe_ms) * timeframe_ms
        
        completed_candle = None
        
        if self._current is None:
            # 첫 캔들
            self._current = Candle(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume
            )
        elif self._current.timestamp != candle_start:
            # 새 캔들 시작 - 이전 캔들 완료
            completed_candle = self._current
            self._candles.append(completed_candle)
            
            self._current = Candle(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume
            )
        else:
            # 현재 캔들 업데이트
            self._current = Candle(
                timestamp=self._current.timestamp,
                open=self._current.open,
                high=max(self._current.high, price),
                low=min(self._current.low, price),
                close=price,
                volume=self._current.volume + volume
            )
        
        return completed_candle
    
    def add_candle(self, candle: Candle) -> None:
        """완성된 캔들 추가"""
        self._candles.append(candle)
        
        # 현재 캔들도 업데이트
        if self._current is None or candle.timestamp >= self._current.timestamp:
            self._current = candle
    
    def get_candles(self, count: Optional[int] = None) -> List[Candle]:
        """캔들 목록 조회 (과거 -> 최신 순)"""
        candles = list(self._candles)
        if count:
            candles = candles[-count:]
        return candles
    
    def get_all_candles(self) -> List[Candle]:
        """모든 캔들 (버퍼 + 현재)"""
        candles = list(self._candles)
        if self._current:
            candles.append(self._current)
        return candles
    
    @property
    def current(self) -> Optional[Candle]:
        """현재 형성 중인 캔들"""
        return self._current
    
    @property
    def last(self) -> Optional[Candle]:
        """마지막 완성된 캔들"""
        return self._candles[-1] if self._candles else None
    
    @property
    def count(self) -> int:
        """완성된 캔들 수"""
        return len(self._candles)
    
    def clear(self) -> None:
        """버퍼 초기화"""
        self._candles.clear()
        self._current = None
    
    def get_price_history(self) -> List[Decimal]:
        """종가 히스토리 (인디케이터용)"""
        return [c.close for c in self.get_all_candles()]
    
    def get_highs(self) -> List[Decimal]:
        """고가 히스토리"""
        return [c.high for c in self.get_all_candles()]
    
    def get_lows(self) -> List[Decimal]:
        """저가 히스토리"""
        return [c.low for c in self.get_all_candles()]
    
    def get_volumes(self) -> List[Decimal]:
        """거래량 히스토리"""
        return [c.volume for c in self.get_all_candles()]
    
    def get_dataframe(self):
        """pandas DataFrame으로 변환"""
        try:
            import pandas as pd
            candles = self.get_all_candles()
            if not candles:
                return pd.DataFrame()
            
            data = {
                'timestamp': [c.timestamp for c in candles],
                'datetime': [c.datetime for c in candles],
                'open': [float(c.open) for c in candles],
                'high': [float(c.high) for c in candles],
                'low': [float(c.low) for c in candles],
                'close': [float(c.close) for c in candles],
                'volume': [float(c.volume) for c in candles],
            }
            return pd.DataFrame(data)
        except ImportError:
            raise ImportError("pandas is required for DataFrame conversion")
