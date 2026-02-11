"""Technical indicators - 기술적 인디케이터"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import List, Optional, Tuple


class Indicator(ABC):
    """인디케이터 베이스 클래스"""
    
    def __init__(self, period: int = 14):
        self.period = period
        
    @abstractmethod
    def calculate(self, data: List[Decimal]) -> any:
        """인디케이터 계산"""
        pass
    
    def _validate_data(self, data: List[Decimal]) -> bool:
        """데이터 검증"""
        return len(data) >= self.period
    
    @staticmethod
    def _sma(data: List[Decimal], period: int) -> Decimal:
        """단순 이동평균"""
        if len(data) < period:
            return Decimal("0")
        return sum(data[-period:]) / period
    
    @staticmethod
    def _ema(data: List[Decimal], period: int) -> Decimal:
        """지수 이동평균"""
        if len(data) < period:
            return Decimal("0")
        
        multiplier = Decimal("2") / (period + 1)
        ema = Indicator._sma(data[:period], period)
        
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
            
        return ema


class SMA(Indicator):
    """단순 이동평균 (Simple Moving Average)"""
    
    def calculate(self, data: List[Decimal]) -> Decimal:
        if not self._validate_data(data):
            return Decimal("0")
        return self._sma(data, self.period)


class EMA(Indicator):
    """지수 이동평균 (Exponential Moving Average)"""
    
    def calculate(self, data: List[Decimal]) -> Decimal:
        if not self._validate_data(data):
            return Decimal("0")
        return self._ema(data, self.period)


class RSI(Indicator):
    """
    상대강도지수 (Relative Strength Index)
    
    RSI = 100 - (100 / (1 + RS))
    RS = 평균 상승 / 평균 하락
    """
    
    def __init__(self, period: int = 14, overbought: float = 70, oversold: float = 30):
        super().__init__(period)
        self.overbought = Decimal(str(overbought))
        self.oversold = Decimal(str(oversold))
    
    def calculate(self, data: List[Decimal]) -> Decimal:
        if len(data) < self.period + 1:
            return Decimal("50")
        
        # 가격 변화 계산
        changes = [data[i] - data[i-1] for i in range(1, len(data))]
        
        # 상승/하락 분리
        gains = [c if c > 0 else Decimal("0") for c in changes]
        losses = [abs(c) if c < 0 else Decimal("0") for c in changes]
        
        # 초기 평균
        avg_gain = sum(gains[:self.period]) / self.period
        avg_loss = sum(losses[:self.period]) / self.period
        
        # 스묵화
        for i in range(self.period, len(gains)):
            avg_gain = (avg_gain * (self.period - 1) + gains[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses[i]) / self.period
        
        if avg_loss == 0:
            return Decimal("100")
        
        rs = avg_gain / avg_loss
        rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
        
        return rsi
    
    def is_overbought(self, rsi_value: Decimal) -> bool:
        """과매수 상태"""
        return rsi_value >= self.overbought
    
    def is_oversold(self, rsi_value: Decimal) -> bool:
        """과매도 상태"""
        return rsi_value <= self.oversold


class MACD(Indicator):
    """
    MACD (Moving Average Convergence Divergence)
    
    MACD Line = 12일 EMA - 26일 EMA
    Signal Line = 9일 EMA of MACD Line
    Histogram = MACD Line - Signal Line
    """
    
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(slow)
        self.fast = fast
        self.slow = slow
        self.signal = signal
    
    def calculate(self, data: List[Decimal]) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Returns: (macd_line, signal_line, histogram)
        """
        if len(data) < self.slow:
            return Decimal("0"), Decimal("0"), Decimal("0")
        
        # EMA 계산
        ema_fast = self._ema(data, self.fast)
        ema_slow = self._ema(data, self.slow)
        
        # MACD Line
        macd_line = ema_fast - ema_slow
        
        # Signal Line (MACD의 EMA)
        # MACD 히스토리가 필요하므로 단순화된 계산
        macd_history = []
        for i in range(self.slow, len(data) + 1):
            fast_ema = self._ema(data[:i], self.fast)
            slow_ema = self._ema(data[:i], self.slow)
            macd_history.append(fast_ema - slow_ema)
        
        signal_line = self._ema(macd_history, self.signal) if len(macd_history) >= self.signal else Decimal("0")
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def is_bullish_crossover(self, prev_macd: Decimal, prev_signal: Decimal,
                              curr_macd: Decimal, curr_signal: Decimal) -> bool:
        """골든크로스 (매수 신호)"""
        return prev_macd < prev_signal and curr_macd >= curr_signal
    
    def is_bearish_crossover(self, prev_macd: Decimal, prev_signal: Decimal,
                               curr_macd: Decimal, curr_signal: Decimal) -> bool:
        """데드크로스 (매도 신호)"""
        return prev_macd > prev_signal and curr_macd <= curr_signal


class BollingerBands(Indicator):
    """
    볼린저 밴드
    
    Middle Band = N일 SMA
    Upper Band = Middle Band + (N일 표준편차 * 2)
    Lower Band = Middle Band - (N일 표준편차 * 2)
    """
    
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__(period)
        self.std_dev_multiplier = Decimal(str(std_dev))
    
    def calculate(self, data: List[Decimal]) -> Tuple[Decimal, Decimal, Decimal]:
        """
        Returns: (upper, middle, lower)
        """
        if len(data) < self.period:
            current_price = data[-1] if data else Decimal("0")
            return current_price, current_price, current_price
        
        # Middle Band (SMA)
        prices = data[-self.period:]
        middle = sum(prices) / len(prices)
        
        # 표준편차
        variance = sum((p - middle) ** 2 for p in prices) / len(prices)
        std_dev = variance.sqrt()
        
        # Upper/Lower Bands
        upper = middle + (std_dev * self.std_dev_multiplier)
        lower = middle - (std_dev * self.std_dev_multiplier)
        
        return upper, middle, lower
    
    def get_position(self, price: Decimal, upper: Decimal, lower: Decimal) -> str:
        """가격 위치 판단"""
        if price >= upper:
            return "overbought"  # 과매수
        elif price <= lower:
            return "oversold"     # 과매도
        else:
            return "neutral"      # 중립
    
    def bandwidth(self, upper: Decimal, lower: Decimal, middle: Decimal) -> Decimal:
        """밴드폭 (변동성 지표)"""
        if middle == 0:
            return Decimal("0")
        return ((upper - lower) / middle) * 100


class ATR(Indicator):
    """
    평균진폭지수 (Average True Range)
    
    진폭 = max(고가-저가, |고가-전종가|, |저가-전종가|)
    ATR = 진폭의 N일 이동평균
    """
    
    def __init__(self, period: int = 14):
        super().__init__(period)
    
    def calculate(self, highs: List[Decimal], lows: List[Decimal], closes: List[Decimal]) -> Decimal:
        """
        ATR 계산
        
        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
        """
        if len(closes) < self.period + 1:
            return Decimal("0")
        
        true_ranges = []
        
        for i in range(1, len(closes)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i-1]
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)
        
        # ATR = True Range의 이동평균
        atr = sum(true_ranges[-self.period:]) / self.period
        
        return atr


class StochasticRSI(Indicator):
    """
    스토캐스틱 RSI
    
    RSI에 스토캐스틱 공식을 적용
    """
    
    def __init__(self, rsi_period: int = 14, stoch_period: int = 14, 
                 k_period: int = 3, d_period: int = 3):
        super().__init__(rsi_period)
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.k_period = k_period
        self.d_period = d_period
        self._rsi = RSI(rsi_period)
    
    def calculate(self, data: List[Decimal]) -> Tuple[Decimal, Decimal]:
        """
        Returns: (%K, %D)
        """
        if len(data) < self.rsi_period + self.stoch_period:
            return Decimal("50"), Decimal("50")
        
        # RSI 계산
        rsi_values = []
        for i in range(self.rsi_period, len(data) + 1):
            rsi = self._rsi.calculate(data[:i])
            rsi_values.append(rsi)
        
        if len(rsi_values) < self.stoch_period:
            return Decimal("50"), Decimal("50")
        
        # Stochastic 공식 적용
        recent_rsi = rsi_values[-self.stoch_period:]
        min_rsi = min(recent_rsi)
        max_rsi = max(recent_rsi)
        
        if max_rsi == min_rsi:
            k = Decimal("50")
        else:
            k = ((recent_rsi[-1] - min_rsi) / (max_rsi - min_rsi)) * 100
        
        # %D (K의 이동평균)
        d = sum(rsi_values[-self.d_period:]) / self.d_period if len(rsi_values) >= self.d_period else k
        
        return k, d


class IndicatorSet:
    """여러 인디케이터를 한번에 계산"""
    
    def __init__(self):
        self.rsi = RSI()
        self.macd = MACD()
        self.bb = BollingerBands()
        self.sma_50 = SMA(50)
        self.sma_200 = SMA(200)
        self.ema_12 = EMA(12)
        self.ema_26 = EMA(26)
        self.atr = ATR()
    
    def calculate_all(self, data: List[Decimal], 
                      highs: List[Decimal] = None,
                      lows: List[Decimal] = None) -> dict:
        """전체 인디케이터 계산"""
        result = {
            'rsi': self.rsi.calculate(data),
            'macd': self.macd.calculate(data),
            'bb': self.bb.calculate(data),
            'sma_50': self.sma_50.calculate(data),
            'sma_200': self.sma_200.calculate(data),
            'ema_12': self.ema_12.calculate(data),
            'ema_26': self.ema_26.calculate(data),
        }
        
        if highs and lows and len(highs) == len(lows) == len(data):
            result['atr'] = self.atr.calculate(highs, lows, data)
        
        return result
