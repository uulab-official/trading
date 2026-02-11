"""Breakout Strategy - 돌파/추세 전환 전략"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from trading_data.candle import CandleBuffer
from trading_data.indicator import ATR

from trading_strategy.base import Strategy, Signal, SignalType, StrategyConfig


class BreakoutStrategy(Strategy):
    """
    저항선/지지선 돌파 전략
    
    - 최근 N기간 고점 돌파 -> 매수
    - 최근 N기간 저점 이탈 -> 매도
    - ATR로 변동성 고려
    
    변동성 돌파 (Volatility Breakout):
    - 오늘 시가 + 어제 ATR * K > 현재가 -> 매수
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        lookback_period: int = 20,
        atr_period: int = 14,
        breakout_threshold: float = 0.0,  # 0 = 정확히 돌파, 0.01 = 1% 돌파
        use_volatility_filter: bool = True,
    ):
        super().__init__(config)
        self.lookback_period = lookback_period
        self.atr_period = atr_period
        self.breakout_threshold = Decimal(str(breakout_threshold))
        self.use_volatility_filter = use_volatility_filter
        
        self.atr = ATR(atr_period)
        
        # 이전 극값
        self._recent_high: Optional[Decimal] = None
        self._recent_low: Optional[Decimal] = None
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """돌파 분석"""
        all_candles = candles.get_all_candles()
        prices = candles.get_price_history()
        
        if len(prices) < self.lookback_period + self.atr_period:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        # 최근 고점/저점 계산 (현재 제외)
        recent_candles = all_candles[-self.lookback_period-1:-1]
        recent_high = max(c.high for c in recent_candles)
        recent_low = min(c.low for c in recent_candles)
        
        current_candle = candles.current or all_candles[-1]
        current_price = current_candle.close
        
        # ATR 계산
        atr_value = Decimal("0")
        if self.use_volatility_filter:
            highs = candles.get_highs()
            lows = candles.get_lows()
            atr_value = self.atr.calculate(highs, lows, prices)
        
        # 돌파 임계값
        breakout_level_up = recent_high * (Decimal("1") + self.breakout_threshold)
        breakout_level_down = recent_low * (Decimal("1") - self.breakout_threshold)
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"Recent High: {recent_high:.2f}, Low: {recent_low:.2f}, Current: {current_price:.2f}"
        
        # 상방 돌파
        if current_price > breakout_level_up:
            signal_type = SignalType.BUY
            confidence = self._calculate_confidence(
                current_price, breakout_level_up, atr_value, 'up'
            )
            reason = f"Upside breakout above {recent_high:.2f}"
        
        # 하방 돌파
        elif current_price < breakout_level_down:
            signal_type = SignalType.SELL
            confidence = self._calculate_confidence(
                current_price, breakout_level_down, atr_value, 'down'
            )
            reason = f"Downside breakout below {recent_low:.2f}"
        
        self._recent_high = recent_high
        self._recent_low = recent_low
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={
                'recent_high': float(recent_high),
                'recent_low': float(recent_low),
                'atr': float(atr_value),
            }
        )
    
    def _calculate_confidence(
        self,
        price: Decimal,
        breakout_level: Decimal,
        atr: Decimal,
        direction: str
    ) -> Decimal:
        """돌파 강도에 따른 신뢰도 계산"""
        # 돌파 거리
        if direction == 'up':
            breakout_distance = price - breakout_level
        else:
            breakout_distance = breakout_level - price
        
        # ATR 대비 돌파 강도
        if atr > 0:
            strength = breakout_distance / atr
            confidence = min(Decimal("0.9"), Decimal("0.6") + strength * Decimal("0.1"))
        else:
            confidence = Decimal("0.65")
        
        return confidence


class VolatilityBreakoutStrategy(Strategy):
    """
    변동성 돌파 전략 (Larry Williams' Volatility Breakout)
    
    진입가 = 오늘 시가 + (어제 ATR * K)
    - 현재가 > 진입가 -> 매수
    
    K=0.5: 보수적
    K=1.0: 중간
    K=2.0: 공격적
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        atr_period: int = 14,
        k: float = 0.5,
    ):
        super().__init__(config)
        self.atr_period = atr_period
        self.k = Decimal(str(k))
        self.atr = ATR(atr_period)
        
        self._daily_open: Optional[Decimal] = None
        self._entry_level: Optional[Decimal] = None
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """변동성 돌파 분석"""
        all_candles = candles.get_all_candles()
        
        if len(all_candles) < self.atr_period + 1:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        prices = candles.get_price_history()
        highs = candles.get_highs()
        lows = candles.get_lows()
        
        # 어제 ATR
        atr_value = self.atr.calculate(highs[:-1], lows[:-1], prices[:-1])
        
        # 현재 캔들 (오늘)
        current_candle = candles.current or all_candles[-1]
        daily_open = current_candle.open
        current_price = current_candle.close
        
        # 진입 레벨
        entry_level = daily_open + (atr_value * self.k)
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"Open: {daily_open:.2f}, Entry: {entry_level:.2f}, Current: {current_price:.2f}, ATR: {atr_value:.2f}"
        
        # 돌파 체크
        if current_price > entry_level:
            signal_type = SignalType.BUY
            # 돌파 강도
            breakout_strength = (current_price - entry_level) / atr_value if atr_value > 0 else Decimal("0")
            confidence = min(Decimal("0.85"), Decimal("0.6") + breakout_strength * Decimal("0.1"))
            reason = f"Volatility breakout: price {current_price:.2f} > entry {entry_level:.2f}"
        
        self._daily_open = daily_open
        self._entry_level = entry_level
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={
                'daily_open': float(daily_open),
                'entry_level': float(entry_level),
                'atr': float(atr_value),
                'k': float(self.k),
            }
        )


class SupportResistanceStrategy(Strategy):
    """
    지지/저항선 기반 전략
    
    - 저항선 돌파 -> 매수
    - 지지선 이탈 -> 매도
    - 지지선 근처 -> 매수
    - 저항선 근처 -> 매도
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        lookback: int = 50,
        touch_threshold: float = 0.005,  # 0.5% 이내 접촉
    ):
        super().__init__(config)
        self.lookback = lookback
        self.touch_threshold = Decimal(str(touch_threshold))
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """지지/저항 분석"""
        all_candles = candles.get_all_candles()
        
        if len(all_candles) < self.lookback:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        # Pivot High/Low 찾기
        recent_candles = all_candles[-self.lookback:]
        
        # 간단한 방법: 고점/저점
        highs = [c.high for c in recent_candles]
        lows = [c.low for c in recent_candles]
        
        resistance = max(highs)
        support = min(lows)
        
        current_candle = candles.current or all_candles[-1]
        current_price = current_candle.close
        
        # 가격 위치
        price_range = resistance - support
        if price_range == 0:
            position_ratio = Decimal("0.5")
        else:
            position_ratio = (current_price - support) / price_range
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"Support: {support:.2f}, Resistance: {resistance:.2f}, Position: {position_ratio:.2%}"
        
        # 저항선 돌파
        if current_price > resistance * (Decimal("1") - self.touch_threshold):
            if current_price > resistance:
                signal_type = SignalType.BUY
                confidence = Decimal("0.75")
                reason = f"Resistance breakout: {current_price:.2f} > {resistance:.2f}"
            else:
                # 저항 근처
                confidence = Decimal("0.4")
                reason += " (near resistance)"
        
        # 지지선 이탈
        elif current_price < support * (Decimal("1") + self.touch_threshold):
            if current_price < support:
                signal_type = SignalType.SELL
                confidence = Decimal("0.75")
                reason = f"Support breakdown: {current_price:.2f} < {support:.2f}"
            else:
                # 지지 근처
                confidence = Decimal("0.6")
                reason += " (near support - potential buy)"
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={
                'support': float(support),
                'resistance': float(resistance),
                'position_ratio': float(position_ratio),
            }
        )
