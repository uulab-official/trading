"""RSI Strategy - RSI 기반 평균회귀 전략"""

from datetime import datetime
from decimal import Decimal

from trading_data.candle import CandleBuffer
from trading_data.indicator import RSI

from trading_strategy.base import Strategy, Signal, SignalType, StrategyConfig


class RSIStrategy(Strategy):
    """
    RSI 기반 평균회귀 전략
    
    - RSI < 30 (과매도): 매수
    - RSI > 70 (과매수): 매도
    - 중간: 홀드
    
    레벨 조정 가능:
    - 보수적: 20/80
    - 공격적: 30/70
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        rsi_period: int = 14,
        oversold: float = 30,
        overbought: float = 70,
    ):
        super().__init__(config)
        self.rsi = RSI(period=rsi_period, oversold=oversold, overbought=overbought)
        self.oversold = Decimal(str(oversold))
        self.overbought = Decimal(str(overbought))
        
        # 이전 상태 (크로스 감지용)
        self._prev_rsi: Decimal = Decimal("50")
        
    def analyze(self, candles: CandleBuffer) -> Signal:
        """RSI 분석"""
        prices = candles.get_price_history()
        
        if len(prices) < self.rsi.period + 1:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        # RSI 계산
        current_rsi = self.rsi.calculate(prices)
        current_price = candles.current.close if candles.current else prices[-1]
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"RSI: {current_rsi:.1f}"
        
        # 과매도 -> 매수
        if current_rsi <= self.oversold:
            signal_type = SignalType.BUY
            # RSI가 낮을수록 신뢰도 상승
            confidence = min(0.9, 0.5 + float((self.oversold - current_rsi) / 100))
            reason = f"RSI oversold: {float(current_rsi):.1f}"
            
        # 과매수 -> 매도
        elif current_rsi >= self.overbought:
            signal_type = SignalType.SELL
            # RSI가 높을수록 신뢰도 상승
            confidence = min(0.9, 0.5 + float((current_rsi - self.overbought) / 100))
            reason = f"RSI overbought: {float(current_rsi):.1f}"
        
        # 크로스 감지 (추가 신뢰도)
        if self._prev_rsi > self.oversold and current_rsi <= self.oversold:
            # 과매도 진입 (매수 강화)
            confidence = min(0.95, confidence + 0.1)
            reason += " (cross into oversold)"
        elif self._prev_rsi < self.overbought and current_rsi >= self.overbought:
            # 과매수 진입 (매도 강화)
            confidence = min(0.95, confidence + 0.1)
            reason += " (cross into overbought)"
        
        self._prev_rsi = current_rsi
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={'rsi': float(current_rsi)}
        )


class RSIDivergenceStrategy(Strategy):
    """
    RSI 다이버전스 전략
    
    - 가격은 더 낮은 저점 but RSI는 더 높은 저점 -> 매수
    - 가격은 더 높은 고점 but RSI는 더 낮은 고점 -> 매도
    
    더 강력한 신호지만 구현 복잡
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        rsi_period: int = 14,
        lookback: int = 20,
    ):
        super().__init__(config)
        self.rsi = RSI(rsi_period)
        self.lookback = lookback
        
    def analyze(self, candles: CandleBuffer) -> Signal:
        """다이버전스 분석"""
        all_candles = candles.get_all_candles()
        prices = candles.get_price_history()
        
        if len(prices) < self.lookback * 2:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data for divergence"
            )
        
        # RSI 히스토리
        rsi_values = []
        for i in range(self.lookback, len(prices) + 1):
            rsi = self.rsi.calculate(prices[:i])
            rsi_values.append(rsi)
        
        # 최근 저점/고점 찾기
        recent_prices = prices[-self.lookback:]
        prev_prices = prices[-self.lookback*2:-self.lookback]
        
        recent_rsi = rsi_values[-self.lookback:]
        prev_rsi = rsi_values[-self.lookback*2:-self.lookback]
        
        current_price = candles.current.close if candles.current else prices[-1]
        
        # 매수 다이버전스: 가격 더 낮음, RSI 더 높음
        if min(recent_prices) < min(prev_prices) and min(recent_rsi) > min(prev_rsi):
            return Signal(
                type=SignalType.BUY,
                symbol=self.symbol,
                price=current_price,
                timestamp=datetime.utcnow(),
                confidence=0.75,
                reason="RSI bullish divergence detected",
                strategy_id=self.id,
            )
        
        # 매도 다이버전스: 가격 더 높음, RSI 더 낮음
        if max(recent_prices) > max(prev_prices) and max(recent_rsi) < max(prev_rsi):
            return Signal(
                type=SignalType.SELL,
                symbol=self.symbol,
                price=current_price,
                timestamp=datetime.utcnow(),
                confidence=0.75,
                reason="RSI bearish divergence detected",
                strategy_id=self.id,
            )
        
        return Signal(
            type=SignalType.HOLD,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            reason="No divergence detected"
        )
