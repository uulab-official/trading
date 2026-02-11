"""Portfolio tracking - 포트폴리오 및 포지션 관리"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """포지션 (보유 중인 거래)"""
    symbol: str
    side: str                    # 'long' or 'short'
    size: Decimal               # 수량 (양수)
    entry_price: Decimal        # 진입 가격
    
    # 현재 상태
    current_price: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    unrealized_pnl_pct: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # 리스크 관리
    stop_loss_price: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    
    # 메타데이터
    opened_at: datetime = field(default_factory=datetime.utcnow)
    order_id: Optional[str] = None      # 연결된 주문 ID
    strategy_id: Optional[str] = None   # 어떤 전략으로 진입
    
    def update_price(self, current_price: Decimal) -> None:
        """현재가 업데이트 및 미실현 손익 계산"""
        self.current_price = current_price
        
        price_diff = current_price - self.entry_price
        if self.side == 'short':
            price_diff = -price_diff
            
        self.unrealized_pnl = price_diff * self.size
        self.unrealized_pnl_pct = (price_diff / self.entry_price) * 100
    
    @property
    def position_value(self) -> Decimal:
        """포지션 가치"""
        return self.size * self.current_price if self.current_price else self.size * self.entry_price
    
    def should_stop_loss(self) -> bool:
        """손절 조건 체크"""
        if not self.stop_loss_price:
            return False
        
        if self.side == 'long':
            return self.current_price <= self.stop_loss_price
        else:
            return self.current_price >= self.stop_loss_price
    
    def should_take_profit(self) -> bool:
        """익절 조건 체크"""
        if not self.take_profit_price:
            return False
        
        if self.side == 'long':
            return self.current_price >= self.take_profit_price
        else:
            return self.current_price <= self.take_profit_price


@dataclass
class Trade:
    """완료된 거래"""
    symbol: str
    side: str                    # 'buy' or 'sell'
    size: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    opened_at: datetime
    closed_at: datetime
    strategy_id: Optional[str] = None


class Portfolio:
    """포트폴리오 관리자"""
    
    def __init__(self, initial_balance: Decimal = Decimal("1000")):
        # 잔고
        self.initial_balance = initial_balance
        self.cash_balance = initial_balance
        
        # 포지션
        self._positions: Dict[str, Position] = {}  # symbol -> Position
        
        # 거래 히스토리
        self._trades: List[Trade] = []
        
        # 일일 추적
        self._daily_start_value = initial_balance
        self._daily_pnl = Decimal("0")
        
        # 전체 통계
        self._total_pnl = Decimal("0")
        self._win_count = 0
        self._loss_count = 0
        
    # ===== 포지션 관리 =====
    
    def open_position(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        entry_price: Decimal,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        strategy_id: Optional[str] = None,
        order_id: Optional[str] = None
    ) -> Position:
        """포지션 진입"""
        if symbol in self._positions:
            raise ValueError(f"Position already exists for {symbol}")
        
        # 현물 기준: buy만 가능 (숏은 미지원, 선물에서만)
        if side == 'short':
            raise ValueError("Short selling not supported in spot trading")
        
        # 현금 차감
        cost = size * entry_price
        if cost > self.cash_balance:
            raise ValueError(f"Insufficient balance: {self.cash_balance} < {cost}")
        
        self.cash_balance -= cost
        
        position = Position(
            symbol=symbol,
            side='long',
            size=size,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            strategy_id=strategy_id,
            order_id=order_id
        )
        
        self._positions[symbol] = position
        logger.info(f"Position opened: {symbol} {size} @ {entry_price}")
        
        return position
    
    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        exit_time: Optional[datetime] = None
    ) -> Trade:
        """포지션 종료"""
        if symbol not in self._positions:
            raise ValueError(f"No position found for {symbol}")
        
        position = self._positions[symbol]
        
        # 손익 계산
        price_diff = exit_price - position.entry_price
        pnl = price_diff * position.size
        pnl_pct = (price_diff / position.entry_price) * 100
        
        # 현금 복원
        exit_value = position.size * exit_price
        self.cash_balance += exit_value
        
        # 거래 기록
        trade = Trade(
            symbol=symbol,
            side='sell',
            size=position.size,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            opened_at=position.opened_at,
            closed_at=exit_time or datetime.utcnow(),
            strategy_id=position.strategy_id
        )
        
        self._trades.append(trade)
        del self._positions[symbol]
        
        # 통계 업데이트
        self._total_pnl += pnl
        self._daily_pnl += pnl
        
        if pnl > 0:
            self._win_count += 1
        else:
            self._loss_count += 1
        
        logger.info(
            f"Position closed: {symbol} PnL={pnl:.2f} ({pnl_pct:.2f}%) "
            f"Balance={self.cash_balance:.2f}"
        )
        
        return trade
    
    def update_position_prices(self, prices: Dict[str, Decimal]) -> None:
        """전체 포지션 가격 업데이트"""
        for symbol, position in self._positions.items():
            if symbol in prices:
                position.update_price(prices[symbol])
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """특정 포지션 조회"""
        return self._positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """포지션 보유 여부"""
        return symbol in self._positions
    
    def get_open_positions(self) -> List[Position]:
        """전체 포지션 목록"""
        return list(self._positions.values())
    
    # ===== 잔고 및 가치 =====
    
    def get_total_value(self) -> Decimal:
        """총 자산 가치 (현금 + 포지션)"""
        position_value = sum(
            p.position_value for p in self._positions.values()
        )
        return self.cash_balance + position_value
    
    def get_position_value(self) -> Decimal:
        """포지션 총 가치"""
        return sum(p.position_value for p in self._positions.values())
    
    def get_unrealized_pnl(self) -> Decimal:
        """미실현 손익"""
        return sum(p.unrealized_pnl for p in self._positions.values())
    
    def get_daily_loss_pct(self) -> Decimal:
        """일일 손실률"""
        if self._daily_start_value == 0:
            return Decimal("0")
        return (self._daily_pnl / self._daily_start_value) * 100
    
    # ===== 리포트 =====
    
    def get_stats(self) -> Dict:
        """포트폴리오 통계"""
        total_trades = self._win_count + self._loss_count
        win_rate = (self._win_count / total_trades * 100) if total_trades > 0 else 0
        
        # 손익비
        winning_trades = [t for t in self._trades if t.pnl > 0]
        losing_trades = [t for t in self._trades if t.pnl <= 0]
        
        avg_win = sum(t.pnl_pct for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.pnl_pct for t in losing_trades) / len(losing_trades) if losing_trades else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        return {
            'initial_balance': float(self.initial_balance),
            'cash_balance': float(self.cash_balance),
            'total_value': float(self.get_total_value()),
            'total_return_pct': float((self.get_total_value() / self.initial_balance - 1) * 100),
            'unrealized_pnl': float(self.get_unrealized_pnl()),
            'realized_pnl': float(self._total_pnl),
            'daily_pnl': float(self._daily_pnl),
            'daily_loss_pct': float(self.get_daily_loss_pct()),
            'open_positions': len(self._positions),
            'position_value': float(self.get_position_value()),
            'total_trades': total_trades,
            'win_count': self._win_count,
            'loss_count': self._loss_count,
            'win_rate': win_rate,
            'profit_factor': float(profit_factor),
            'avg_win_pct': float(avg_win),
            'avg_loss_pct': float(avg_loss),
        }
    
    def get_position_report(self) -> List[Dict]:
        """현재 포지션 상세 리포트"""
        return [
            {
                'symbol': p.symbol,
                'side': p.side,
                'size': float(p.size),
                'entry_price': float(p.entry_price),
                'current_price': float(p.current_price),
                'unrealized_pnl': float(p.unrealized_pnl),
                'unrealized_pnl_pct': float(p.unrealized_pnl_pct),
                'stop_loss': float(p.stop_loss_price) if p.stop_loss_price else None,
                'take_profit': float(p.take_profit_price) if p.take_profit_price else None,
                'duration_hours': (datetime.utcnow() - p.opened_at).total_seconds() / 3600,
            }
            for p in self._positions.values()
        ]
    
    def reset_daily(self) -> None:
        """일일 리셋"""
        self._daily_start_value = self.get_total_value()
        self._daily_pnl = Decimal("0")
        logger.info(f"Daily portfolio reset. Start value: {self._daily_start_value}")
    
    def to_dict(self) -> Dict:
        """상태 저장용 직렬화"""
        return {
            'initial_balance': str(self.initial_balance),
            'cash_balance': str(self.cash_balance),
            'positions': {
                symbol: {
                    'symbol': p.symbol,
                    'side': p.side,
                    'size': str(p.size),
                    'entry_price': str(p.entry_price),
                    'stop_loss_price': str(p.stop_loss_price) if p.stop_loss_price else None,
                    'take_profit_price': str(p.take_profit_price) if p.take_profit_price else None,
                    'opened_at': p.opened_at.isoformat(),
                    'order_id': p.order_id,
                    'strategy_id': p.strategy_id,
                }
                for symbol, p in self._positions.items()
            },
            'trades': [
                {
                    'symbol': t.symbol,
                    'pnl': str(t.pnl),
                    'pnl_pct': str(t.pnl_pct),
                    'closed_at': t.closed_at.isoformat(),
                }
                for t in self._trades[-100:]  # 최근 100개만 저장
            ],
            'daily_start_value': str(self._daily_start_value),
            'total_pnl': str(self._total_pnl),
            'win_count': self._win_count,
            'loss_count': self._loss_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Portfolio':
        """상태 복원"""
        portfolio = cls(
            initial_balance=Decimal(data.get('initial_balance', '1000'))
        )
        portfolio.cash_balance = Decimal(data.get('cash_balance', '0'))
        portfolio._daily_start_value = Decimal(data.get('daily_start_value', '1000'))
        portfolio._total_pnl = Decimal(data.get('total_pnl', '0'))
        portfolio._win_count = data.get('win_count', 0)
        portfolio._loss_count = data.get('loss_count', 0)
        
        # 포지션 복원
        for symbol, p_data in data.get('positions', {}).items():
            position = Position(
                symbol=p_data['symbol'],
                side=p_data['side'],
                size=Decimal(p_data['size']),
                entry_price=Decimal(p_data['entry_price']),
                stop_loss_price=Decimal(p_data['stop_loss_price']) if p_data.get('stop_loss_price') else None,
                take_profit_price=Decimal(p_data['take_profit_price']) if p_data.get('take_profit_price') else None,
                opened_at=datetime.fromisoformat(p_data['opened_at']),
                order_id=p_data.get('order_id'),
                strategy_id=p_data.get('strategy_id'),
            )
            portfolio._positions[symbol] = position
        
        return portfolio
