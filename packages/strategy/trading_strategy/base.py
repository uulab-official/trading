"""Base strategy class - 전략 베이스 클래스"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any
import logging

from trading_data.candle import CandleBuffer

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """신호 타입"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"  # 포지션 청산


@dataclass
class Signal:
    """거래 신호"""
    type: SignalType
    symbol: str
    price: Decimal
    timestamp: datetime
    
    # 리스크 관리
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    position_size: Optional[Decimal] = None
    
    # 메타데이터
    confidence: float = 0.5           # 신뢰도 (0~1)
    reason: str = ""                  # 신호 발생 이유
    strategy_id: str = ""             # 전략 식별자
    metadata: Dict[str, Any] = None   # 추가 데이터
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    def to_dict(self) -> Dict:
        """딕셔너리 변환"""
        return {
            'type': self.type.value,
            'symbol': self.symbol,
            'price': str(self.price),
            'timestamp': self.timestamp.isoformat(),
            'stop_loss': str(self.stop_loss) if self.stop_loss else None,
            'take_profit': str(self.take_profit) if self.take_profit else None,
            'position_size': str(self.position_size) if self.position_size else None,
            'confidence': self.confidence,
            'reason': self.reason,
            'strategy_id': self.strategy_id,
            'metadata': self.metadata,
        }


@dataclass
class StrategyConfig:
    """전략 설정"""
    name: str
    symbol: str
    timeframe: str = "4h"             # 기본 4시간봉
    
    # 포지션 설정
    use_stop_loss: bool = True
    use_take_profit: bool = True
    
    # 필터
    min_confidence: float = 0.5       # 최소 신뢰도
    
    # 커스텀 파라미터
    params: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.params is None:
            self.params = {}


class Strategy(ABC):
    """전략 베이스 클래스"""
    
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.id = config.name
        self.symbol = config.symbol
        self.timeframe = config.timeframe
        
        # 데이터 버퍼
        self.candles = CandleBuffer(max_size=500, timeframe=self.timeframe)
        
        # 상태
        self.is_active = True
        self.last_signal: Optional[Signal] = None
        self.signal_history: List[Signal] = []
        
        # 통계
        self.total_signals = 0
        self.win_count = 0
        self.loss_count = 0
        
    @abstractmethod
    def analyze(self, candles: CandleBuffer) -> Signal:
        """
        캔들 데이터 분석하여 신호 생성
        
        반드시 구현해야 함
        """
        pass
    
    def on_candle(self, candle) -> Optional[Signal]:
        """
        새 캔들 수신 시 호출
        
        Returns:
            Signal or None
        """
        if not self.is_active:
            return None
        
        # 캔들 추가
        completed = self.candles.add_tick(
            timestamp=candle.timestamp,
            price=candle.close,
            volume=candle.volume
        )
        
        # 완성된 캔들이 있으면 분석
        if completed:
            self.candles.add_candle(completed)
        
        # 충분한 데이터가 쌓였으면 분석
        if self.candles.count >= 50:  # 최소 50개 캔들 필요
            signal = self.analyze(self.candles)
            
            if signal and signal.type != SignalType.HOLD:
                # 신호 필터링
                if signal.confidence >= self.config.min_confidence:
                    signal.strategy_id = self.id
                    self._record_signal(signal)
                    return signal
        
        return None
    
    def on_price_tick(self, price: Decimal, timestamp: int) -> Optional[Signal]:
        """실시간 가격 틱 수신 (고빈도 전략용)"""
        # 기본 구현: 무시 (서브클스에서 오버라이드)
        return None
    
    def _record_signal(self, signal: Signal) -> None:
        """신호 기록"""
        self.last_signal = signal
        self.signal_history.append(signal)
        self.total_signals += 1
        
        # 최근 100개만 유지
        if len(self.signal_history) > 100:
            self.signal_history = self.signal_history[-100:]
        
        logger.info(
            f"Signal generated: {signal.type.value} {signal.symbol} @ {signal.price} "
            f"(confidence: {signal.confidence:.2f})"
        )
    
    def get_stats(self) -> Dict:
        """전략 통계"""
        return {
            'strategy_id': self.id,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'is_active': self.is_active,
            'total_signals': self.total_signals,
            'candle_count': self.candles.count,
            'last_signal': self.last_signal.to_dict() if self.last_signal else None,
        }
    
    def activate(self) -> None:
        """전략 활성화"""
        self.is_active = True
        logger.info(f"Strategy activated: {self.id}")
    
    def deactivate(self) -> None:
        """전략 비활성화"""
        self.is_active = False
        logger.info(f"Strategy deactivated: {self.id}")
    
    def reset(self) -> None:
        """전략 상태 초기화"""
        self.candles.clear()
        self.signal_history.clear()
        self.last_signal = None
        self.total_signals = 0
        self.win_count = 0
        self.loss_count = 0
        logger.info(f"Strategy reset: {self.id}")
    
    def to_dict(self) -> Dict:
        """직렬화"""
        return {
            'id': self.id,
            'config': {
                'name': self.config.name,
                'symbol': self.config.symbol,
                'timeframe': self.config.timeframe,
            },
            'is_active': self.is_active,
            'stats': self.get_stats(),
        }
