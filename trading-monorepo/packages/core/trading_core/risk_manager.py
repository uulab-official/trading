"""Risk management - 리스크 관리 (자동매매 생존의 핵심)"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
import logging

from trading_core.portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """리스크 설정"""
    # 포지션 리스크
    max_position_size_pct: Decimal = Decimal("10")      # 계좌 대비 최대 포지션 10%
    max_positions: int = 2                               # 최대 동시 포지션 2개
    risk_per_trade_pct: Decimal = Decimal("1")          # 거래당 리스크 1%
    
    # 일일 리스크
    max_daily_loss_pct: Decimal = Decimal("3")          # 일일 최대 손실 3%
    max_daily_trades: int = 10                          # 일일 최대 거래 10회
    
    # 레버리지
    max_leverage: Decimal = Decimal("1")                # 최대 레버리지 1배 (현물)
    
    # 손절/익절
    default_stop_loss_pct: Decimal = Decimal("2")       # 기본 손절 2%
    default_take_profit_pct: Decimal = Decimal("4")     # 기본 익절 4%
    
    # 비상 정지
    circuit_breaker_loss_pct: Decimal = Decimal("5")    # 서킷브레이커 5%
    
    # 쿨다운
    cooldown_minutes_after_loss: int = 30               # 손실 후 30분 쿨다운


class RiskManager:
    """리스크 관리자 - 생존을 위한 모든 제한"""
    
    def __init__(self, config: RiskConfig, portfolio: Portfolio):
        self.config = config
        self.portfolio = portfolio
        
        # 일일 추적
        self._daily_stats = {
            'date': datetime.utcnow().date(),
            'total_pnl': Decimal("0"),
            'trade_count': 0,
            'loss_count': 0,
        }
        
        # 쿨다운
        self._cooldown_until: Optional[datetime] = None
        
        # 서킷브레이커
        self._circuit_breaker_triggered = False
        
    def reset_daily_stats(self) -> None:
        """일일 통계 초기화 (자정마다 호출)"""
        today = datetime.utcnow().date()
        if today != self._daily_stats['date']:
            self._daily_stats = {
                'date': today,
                'total_pnl': Decimal("0"),
                'trade_count': 0,
                'loss_count': 0,
            }
            self._circuit_breaker_triggered = False
            self._cooldown_until = None
            logger.info(f"Daily stats reset for {today}")
    
    def check_trade_allowed(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        price: Decimal
    ) -> tuple[bool, str]:
        """
        거래 가능 여부 체크
        
        Returns:
            (allowed: bool, reason: str)
        """
        self.reset_daily_stats()
        
        # 1. 서킷브레이커 체크
        if self._circuit_breaker_triggered:
            return False, "Circuit breaker triggered - trading halted"
        
        # 2. 쿨다운 체크
        if self._cooldown_until and datetime.utcnow() < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.utcnow()).seconds // 60
            return False, f"Cooldown active - {remaining} minutes remaining"
        
        # 3. 일일 손실 한도
        daily_loss_pct = self.portfolio.get_daily_loss_pct()
        if daily_loss_pct >= self.config.max_daily_loss_pct:
            return False, f"Daily loss limit reached: {daily_loss_pct}%"
        
        # 4. 일일 거래 횟수
        if self._daily_stats['trade_count'] >= self.config.max_daily_trades:
            return False, f"Daily trade limit reached: {self.config.max_daily_trades}"
        
        # 5. 최대 포지션 수
        open_positions = len(self.portfolio.get_open_positions())
        if open_positions >= self.config.max_positions:
            return False, f"Max positions reached: {open_positions}/{self.config.max_positions}"
        
        # 6. 포지션 사이즈
        position_value = amount * price
        account_value = self.portfolio.get_total_value()
        position_pct = (position_value / account_value) * 100 if account_value > 0 else Decimal("0")
        
        if position_pct > self.config.max_position_size_pct:
            return False, f"Position size too large: {position_pct:.2f}% > {self.config.max_position_size_pct}%"
        
        # 7. 심볼 중복 체크
        if self.portfolio.has_position(symbol):
            return False, f"Already have position in {symbol}"
        
        return True, "OK"
    
    def calculate_position_size(
        self,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        account_balance: Optional[Decimal] = None
    ) -> Decimal:
        """
        포지션 사이즈 계산 (피라미딩/안티피라미딩 가능)
        
        Formula: position_size = account_risk_amount / stop_distance
        """
        if account_balance is None:
            account_balance = self.portfolio.get_total_value()
        
        # 리스크 금액
        risk_amount = account_balance * (self.config.risk_per_trade_pct / 100)
        
        # 손절 거리
        stop_distance = abs(entry_price - stop_loss_price)
        if stop_distance == 0:
            logger.warning("Stop loss price equals entry price - using default 2%")
            stop_distance = entry_price * Decimal("0.02")
        
        # 포지션 사이즈
        position_size = risk_amount / stop_distance
        
        # 최대 포지션 제한
        max_position_value = account_balance * (self.config.max_position_size_pct / 100)
        max_position_size = max_position_value / entry_price
        
        final_size = min(position_size, max_position_size)
        
        logger.info(
            f"Position size calc: risk={risk_amount:.2f}, "
            f"stop={stop_distance:.4f}, size={final_size:.6f}"
        )
        
        return final_size
    
    def calculate_stop_loss(
        self,
        entry_price: Decimal,
        side: str,
        atr: Optional[Decimal] = None
    ) -> Decimal:
        """손절가 계산"""
        if atr:
            # ATR 기반 (변동성 적응형)
            stop_distance = atr * 2  # 2 ATR
        else:
            # 고정 %
            stop_distance = entry_price * (self.config.default_stop_loss_pct / 100)
        
        if side == 'buy':
            return entry_price - stop_distance
        else:
            return entry_price + stop_distance
    
    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        side: str
    ) -> Decimal:
        """익절가 계산 (손익비 기반)"""
        stop_distance = abs(entry_price - stop_loss_price)
        
        # 손익비 1:2
        reward_distance = stop_distance * 2
        
        if side == 'buy':
            return entry_price + reward_distance
        else:
            return entry_price - reward_distance
    
    def on_position_closed(self, pnl: Decimal) -> None:
        """포지션 종료 시 호출 - 리스크 업데이트"""
        self._daily_stats['trade_count'] += 1
        self._daily_stats['total_pnl'] += pnl
        
        if pnl < 0:
            self._daily_stats['loss_count'] += 1
            
            # 손실 후 쿨다운
            self._cooldown_until = datetime.utcnow() + timedelta(
                minutes=self.config.cooldown_minutes_after_loss
            )
            logger.info(f"Cooldown activated until {self._cooldown_until}")
        
        # 서킷브레이커 체크
        daily_loss_pct = self.portfolio.get_daily_loss_pct()
        if daily_loss_pct >= self.config.circuit_breaker_loss_pct:
            self._circuit_breaker_triggered = True
            logger.warning(f"CIRCUIT BREAKER TRIGGERED! Daily loss: {daily_loss_pct}%")
    
    def get_risk_report(self) -> Dict:
        """현재 리스크 상태 리포트"""
        self.reset_daily_stats()
        
        return {
            'daily_pnl': float(self._daily_stats['total_pnl']),
            'daily_pnl_pct': float(self.portfolio.get_daily_loss_pct()),
            'daily_trades': self._daily_stats['trade_count'],
            'daily_losses': self._daily_stats['loss_count'],
            'open_positions': len(self.portfolio.get_open_positions()),
            'circuit_breaker': self._circuit_breaker_triggered,
            'cooldown_active': self._cooldown_until is not None,
            'cooldown_remaining': (
                (self._cooldown_until - datetime.utcnow()).seconds // 60
                if self._cooldown_until else 0
            ),
        }
    
    def is_trading_allowed(self) -> bool:
        """현재 거래 가능 여부"""
        allowed, _ = self.check_trade_allowed("", "", Decimal("0"), Decimal("0"))
        return allowed
