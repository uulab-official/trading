"""Moving Average Strategy - 이동평균선 전략"""

from datetime import datetime
from decimal import Decimal

from trading_data.candle import CandleBuffer
from trading_data.indicator import SMA, EMA

from trading_strategy.base import Strategy, Signal, SignalType, StrategyConfig


class MAStrategy(Strategy):
    """
    단순 이동평균 추세전략
    
    - 가격 > MA: 상승추세 -> 매수
    - 가격 < MA: 하락추세 -> 매도
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        ma_period: int = 50,
        use_ema: bool = False,
    ):
        super().__init__(config)
        self.ma_period = ma_period
        self.use_ema = use_ema
        
        if use_ema:
            self.ma = EMA(ma_period)
        else:
            self.ma = SMA(ma_period)
        
        self._prev_price_above_ma = False
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """MA 분석"""
        prices = candles.get_price_history()
        
        if len(prices) < self.ma_period:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        ma_value = self.ma.calculate(prices)
        current_price = candles.current.close if candles.current else prices[-1]
        
        price_above_ma = current_price > ma_value
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"Price: {current_price:.2f}, MA({self.ma_period}): {ma_value:.2f}"
        
        # 크로스오버 감지
        if price_above_ma and not self._prev_price_above_ma:
            # 골든크로스 (매수)
            signal_type = SignalType.BUY
            confidence = 0.7
            reason = f"Price crossed above MA{self.ma_period}"
        elif not price_above_ma and self._prev_price_above_ma:
            # 데드크로스 (매도)
            signal_type = SignalType.SELL
            confidence = 0.7
            reason = f"Price crossed below MA{self.ma_period}"
        elif price_above_ma:
            # 상승추세 유지
            confidence = 0.55
        else:
            # 하락추세 유지
            confidence = 0.55
        
        self._prev_price_above_ma = price_above_ma
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={
                'ma_value': float(ma_value),
                'ma_period': self.ma_period
            }
        )


class MACrossStrategy(Strategy):
    """
    이중 이동평균선 교차 전략 (골든크로스/데드크로스)
    
    - 단기 MA > 장기 MA: 골든크로스 -> 매수
    - 단기 MA < 장기 MA: 데드크로스 -> 매도
    
    Classic: MA50 x MA200
    Medium: MA20 x MA50
    Short: MA9 x MA21
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        fast_period: int = 50,
        slow_period: int = 200,
        use_ema: bool = False,
    ):
        super().__init__(config)
        self.fast_period = fast_period
        self.slow_period = slow_period
        
        if use_ema:
            self.fast_ma = EMA(fast_period)
            self.slow_ma = EMA(slow_period)
        else:
            self.fast_ma = SMA(fast_period)
            self.slow_ma = SMA(slow_period)
        
        self._prev_fast_above_slow = False
        self._consecutive_bars = 0  # 크로스 확인용 연속 봉 카운트
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """MA 교차 분석"""
        prices = candles.get_price_history()
        
        min_period = max(self.fast_period, self.slow_period)
        if len(prices) < min_period:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        fast_ma = self.fast_ma.calculate(prices)
        slow_ma = self.slow_ma.calculate(prices)
        current_price = candles.current.close if candles.current else prices[-1]
        
        fast_above_slow = fast_ma > slow_ma
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"MA{self.fast_period}: {fast_ma:.2f}, MA{self.slow_period}: {slow_ma:.2f}"
        
        # 크로스오버 감지
        if fast_above_slow and not self._prev_fast_above_slow:
            # 골든크로스
            signal_type = SignalType.BUY
            confidence = 0.75
            reason = f"Golden Cross: MA{self.fast_period} crossed above MA{self.slow_period}"
            self._consecutive_bars = 0
        elif not fast_above_slow and self._prev_fast_above_slow:
            # 데드크로스
            signal_type = SignalType.SELL
            confidence = 0.75
            reason = f"Death Cross: MA{self.fast_period} crossed below MA{self.slow_period}"
            self._consecutive_bars = 0
        elif fast_above_slow:
            # 상승추세 유지
            self._consecutive_bars += 1
            confidence = min(0.65, 0.5 + self._consecutive_bars * 0.01)
        else:
            # 하락추세 유지
            self._consecutive_bars += 1
            confidence = min(0.65, 0.5 + self._consecutive_bars * 0.01)
        
        self._prev_fast_above_slow = fast_above_slow
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
            metadata={
                'fast_ma': float(fast_ma),
                'slow_ma': float(slow_ma),
                'fast_period': self.fast_period,
                'slow_period': self.slow_period,
            }
        )


class TripleMAStrategy(Strategy):
    """
    삼중 이동평균선 전략
    
    - 단기 > 중기 > 장기: 강한 상승 -> 매수
    - 단기 < 중기 < 장기: 강한 하락 -> 매도
    - 정렬 깨짐: 추세 전환 예고
    
    Example: MA9 > MA21 > MA50
    """
    
    def __init__(
        self,
        config: StrategyConfig,
        short_period: int = 9,
        medium_period: int = 21,
        long_period: int = 50,
    ):
        super().__init__(config)
        self.short_ma = EMA(short_period)
        self.medium_ma = EMA(medium_period)
        self.long_ma = EMA(long_period)
        
        self._prev_aligned_bullish = False
        self._prev_aligned_bearish = False
    
    def analyze(self, candles: CandleBuffer) -> Signal:
        """삼중 MA 분석"""
        prices = candles.get_price_history()
        
        min_period = max(self.short_ma.period, self.medium_ma.period, self.long_ma.period)
        if len(prices) < min_period:
            return Signal(
                type=SignalType.HOLD,
                symbol=self.symbol,
                price=candles.current.close if candles.current else Decimal("0"),
                timestamp=datetime.utcnow(),
                reason="Insufficient data"
            )
        
        short = self.short_ma.calculate(prices)
        medium = self.medium_ma.calculate(prices)
        long = self.long_ma.calculate(prices)
        current_price = candles.current.close if candles.current else prices[-1]
        
        # 정렬 확인
        aligned_bullish = short > medium > long
        aligned_bearish = short < medium < long
        
        signal_type = SignalType.HOLD
        confidence = 0.5
        reason = f"MAs - Short: {short:.2f}, Medium: {medium:.2f}, Long: {long:.2f}"
        
        # 강한 상승 정렬 진입
        if aligned_bullish and not self._prev_aligned_bullish:
            signal_type = SignalType.BUY
            confidence = 0.8
            reason = "Strong bullish alignment (Short > Medium > Long)"
        # 강한 하락 정렬 진입
        elif aligned_bearish and not self._prev_aligned_bearish:
            signal_type = SignalType.SELL
            confidence = 0.8
            reason = "Strong bearish alignment (Short < Medium < Long)"
        # 정렬 유지
        elif aligned_bullish:
            confidence = 0.6
        elif aligned_bearish:
            confidence = 0.6
        # 정렬 깨짐 (주의)
        else:
            confidence = 0.4
            reason += " (MA alignment breaking)"
        
        self._prev_aligned_bullish = aligned_bullish
        self._prev_aligned_bearish = aligned_bearish
        
        return Signal(
            type=signal_type,
            symbol=self.symbol,
            price=current_price,
            timestamp=datetime.utcnow(),
            confidence=float(confidence),
            reason=reason,
            strategy_id=self.id,
        )
