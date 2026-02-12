"""Backtest Engine - 백테스트 엔진"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import pandas as pd
import numpy as np
import logging

from trading_strategy import Strategy, Signal, SignalType
from trading_data import CandleBuffer

from trading_backtest.brokers.virtual_broker import VirtualBroker, BrokerConfig

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """백테스트 설정"""
    # 자본 설정
    initial_balance: Decimal = Decimal("10000")
    
    # 거래 설정
    commission_pct: Decimal = Decimal("0.1")  # 수수료 %
    slippage_pct: Decimal = Decimal("0.05")   # 슬리피지 %
    
    # 리스크 설정
    position_size_pct: Decimal = Decimal("10")  # 포지션 크기 %
    max_positions: int = 3
    
    # 백테스트 설정
    timeframe: str = "1h"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


@dataclass
class BacktestResult:
    """백테스트 결과"""
    # 기본 정보
    strategy_name: str
    symbol: str
    timeframe: str
    
    # 자본
    initial_balance: Decimal
    final_balance: Decimal
    
    # 수익률
    total_return_pct: Decimal
    cagr: Decimal  # Compound Annual Growth Rate
    annualized_return: Decimal
    
    # 리스크
    volatility: Decimal  # 연환산 표준편차
    max_drawdown: Decimal
    max_drawdown_duration: int  # 일
    
    # 위험조정 수익률
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    
    # 거래 통계
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    profit_factor: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    avg_trade: Decimal
    expectancy: Decimal
    
    # 거래 내역
    trades: List[Dict]
    equity_curve: pd.DataFrame
    
    # 추가 메트릭스
    skewness: Decimal
    kurtosis: Decimal
    var_95: Decimal
    cvar_95: Decimal
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            'strategy_name': self.strategy_name,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'initial_balance': float(self.initial_balance),
            'final_balance': float(self.final_balance),
            'total_return_pct': float(self.total_return_pct),
            'cagr': float(self.cagr),
            'annualized_return': float(self.annualized_return),
            'volatility': float(self.volatility),
            'max_drawdown': float(self.max_drawdown),
            'max_drawdown_duration': self.max_drawdown_duration,
            'sharpe_ratio': float(self.sharpe_ratio),
            'sortino_ratio': float(self.sortino_ratio),
            'calmar_ratio': float(self.calmar_ratio),
            'total_trades': self.total_trades,
            'win_count': self.win_count,
            'loss_count': self.loss_count,
            'win_rate': float(self.win_rate),
            'profit_factor': float(self.profit_factor),
            'avg_win': float(self.avg_win),
            'avg_loss': float(self.avg_loss),
            'avg_trade': float(self.avg_trade),
            'expectancy': float(self.expectancy),
            'skewness': float(self.skewness),
            'kurtosis': float(self.kurtosis),
            'var_95': float(self.var_95),
            'cvar_95': float(self.cvar_95),
        }


class BacktestEngine:
    """백테스트 엔진 - 전략을 과거 데이터로 검증"""
    
    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.broker: Optional[VirtualBroker] = None
        self.strategy: Optional[Strategy] = None
        self.data: Optional[pd.DataFrame] = None
        
        # 결과 저장
        self.equity_curve: List[Dict] = []
        self.signals: List[Signal] = []
        
    def run(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> BacktestResult:
        """
        백테스트 실행
        
        Args:
            strategy: 테스트할 전략
            data: OHLCV 데이터 (pandas DataFrame)
            progress_callback: 진행률 콜백 (current, total)
        
        Returns:
            BacktestResult
        """
        logger.info(f"Starting backtest for {strategy.id}")
        
        self.strategy = strategy
        self.data = data
        
        # 브로커 초기화
        broker_config = BrokerConfig(
            slippage_pct=self.config.slippage_pct,
            taker_fee_pct=self.config.commission_pct,
            maker_fee_pct=self.config.commission_pct
        )
        self.broker = VirtualBroker(
            config=broker_config,
            initial_balance=self.config.initial_balance
        )
        
        # 데이터 준비
        if len(data) < 50:
            raise ValueError("Insufficient data for backtest (minimum 50 bars)")
        
        # 백테스트 루프
        total_bars = len(data)
        candle_buffer = CandleBuffer(max_size=500, timeframe=strategy.timeframe)
        
        for i, (_, row) in enumerate(data.iterrows()):
            # 진행률 콜백
            if progress_callback and i % 100 == 0:
                progress_callback(i, total_bars)
            
            # 캔들 생성
            from trading_data import Candle
            # timestamp 처리
            if 'timestamp' in row:
                ts = row['timestamp']
                if hasattr(ts, 'timestamp'):
                    timestamp = int(ts.timestamp() * 1000)
                else:
                    timestamp = int(ts)
            else:
                timestamp = int(row.name.timestamp() * 1000)
            
            candle = Candle(
                timestamp=timestamp,
                open=Decimal(str(row['open'])),
                high=Decimal(str(row['high'])),
                low=Decimal(str(row['low'])),
                close=Decimal(str(row['close'])),
                volume=Decimal(str(row.get('volume', 0)))
            )
            
            # 브로커에 현재 바 처리
            bar_dict = {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row.get('volume', 0),
                'timestamp': candle.timestamp
            }
            self.broker.process_bar(bar_dict)
            
            # 전략에 캔들 전달
            signal = strategy.on_candle(candle)
            
            if signal and signal.type != SignalType.HOLD:
                self.signals.append(signal)
                self._execute_signal(signal, bar_dict)
            
            # 자본 기록
            current_price = Decimal(str(row['close']))
            total_value = self.broker.get_total_value({strategy.symbol: current_price})
            
            self.equity_curve.append({
                'timestamp': candle.timestamp,
                'datetime': datetime.utcfromtimestamp(candle.timestamp / 1000),
                'equity': float(total_value),
                'cash': float(self.broker.balance),
                'position': float(self.broker.get_position(strategy.symbol)),
                'price': float(current_price)
            })
        
        # 최종 진행률
        if progress_callback:
            progress_callback(total_bars, total_bars)
        
        # 결과 계산
        result = self._calculate_result()
        
        logger.info(f"Backtest completed: Return={result.total_return_pct:.2f}%, Trades={result.total_trades}")
        
        return result
    
    def _execute_signal(self, signal: Signal, bar: Dict) -> None:
        """신호 실행"""
        if not self.broker:
            return
        
        symbol = signal.symbol
        current_price = Decimal(str(bar['close']))
        
        # 포지션 사이즈 계산
        balance = self.broker.balance
        position_value = balance * (self.config.position_size_pct / 100)
        amount = position_value / current_price
        
        # 리스크 체크
        if len(self.broker.positions) >= self.config.max_positions:
            if symbol not in self.broker.positions:
                logger.debug(f"Max positions reached, skipping {symbol}")
                return
        
        # 주문 실행
        if signal.type == SignalType.BUY:
            # 매수
            self.broker.submit_order(
                symbol=symbol,
                side='buy',
                amount=amount,
                order_type='market',
                current_bar=bar
            )
        elif signal.type == SignalType.SELL:
            # 매도
            position_size = self.broker.get_position(symbol)
            if position_size > 0:
                self.broker.submit_order(
                    symbol=symbol,
                    side='sell',
                    amount=position_size,
                    order_type='market',
                    current_bar=bar
                )
    
    def _calculate_result(self) -> BacktestResult:
        """백테스트 결과 계산"""
        if not self.broker or not self.strategy:
            raise RuntimeError("Backtest not run yet")
        
        # 기본 데이터
        initial_balance = self.config.initial_balance
        final_balance = self.broker.balance
        
        # 수익률 계산
        total_return = (final_balance - initial_balance) / initial_balance
        total_return_pct = total_return * 100
        
        # Equity curve DataFrame
        equity_df = pd.DataFrame(self.equity_curve)
        if len(equity_df) > 0:
            equity_df['returns'] = equity_df['equity'].pct_change()
            equity_df['cummax'] = equity_df['equity'].cummax()
            equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax']
        
        # 거래 분석
        trades = self.broker.trades
        total_trades = len(trades)
        
        if total_trades > 0:
            # 수익/손실 거래
            pnl_list = []
            for i, trade in enumerate(trades):
                # 매도 거래만 PnL 계산
                if trade['side'] == 'sell':
                    # 해당 매수 찾기
                    buy_trade = None
                    for j in range(i-1, -1, -1):
                        if trades[j]['side'] == 'buy' and trades[j]['symbol'] == trade['symbol']:
                            buy_trade = trades[j]
                            break
                    
                    if buy_trade:
                        pnl = (trade['price'] - buy_trade['price']) / buy_trade['price'] * 100
                        pnl_list.append(pnl)
            
            win_trades = [p for p in pnl_list if p > 0]
            loss_trades = [p for p in pnl_list if p <= 0]
            
            win_count = len(win_trades)
            loss_count = len(loss_trades)
            win_rate = Decimal(str(win_count / total_trades * 100)) if total_trades > 0 else Decimal("0")
            
            avg_win = Decimal(str(np.mean(win_trades))) if win_trades else Decimal("0")
            avg_loss = Decimal(str(np.mean(loss_trades))) if loss_trades else Decimal("0")
            avg_trade = Decimal(str(np.mean(pnl_list))) if pnl_list else Decimal("0")
            
            profit_factor = abs(sum(win_trades) / sum(loss_trades)) if loss_trades and sum(loss_trades) != 0 else Decimal("0")
            expectancy = (win_rate / 100 * avg_win) + ((Decimal("1") - win_rate / 100) * avg_loss)
        else:
            win_count = loss_count = 0
            win_rate = avg_win = avg_loss = avg_trade = profit_factor = expectancy = Decimal("0")
        
        # 리스크 메트릭스
        if len(equity_df) > 1 and 'returns' in equity_df.columns:
            returns = equity_df['returns'].dropna()
            
            # 변동성 (연환산)
            volatility = Decimal(str(returns.std() * np.sqrt(365 * 24)))  # 시간봉 기준 가정
            
            # 최대 낙폭
            max_drawdown = Decimal(str(equity_df['drawdown'].min() * 100))
            
            # MDD 지속 기간
            drawdown_duration = 0
            max_duration = 0
            in_drawdown = False
            for dd in equity_df['drawdown']:
                if dd < 0:
                    if not in_drawdown:
                        in_drawdown = True
                        drawdown_duration = 1
                    else:
                        drawdown_duration += 1
                else:
                    in_drawdown = False
                    max_duration = max(max_duration, drawdown_duration)
                    drawdown_duration = 0
            
            # 샤프 비율 (무위험 이자율 0% 가정)
            sharpe_ratio = Decimal(str(returns.mean() / returns.std() * np.sqrt(365 * 24))) if returns.std() != 0 else Decimal("0")
            
            # 소르티노 비율
            downside_returns = returns[returns < 0]
            downside_std = downside_returns.std() if len(downside_returns) > 0 else 0
            sortino_ratio = Decimal(str(returns.mean() / downside_std * np.sqrt(365 * 24))) if downside_std != 0 else Decimal("0")
            
            # 캘마 비율
            calmar_ratio = total_return_pct / abs(max_drawdown) if max_drawdown != 0 else Decimal("0")
            
            # 왜도, 첨도
            skewness = Decimal(str(returns.skew()))
            kurtosis = Decimal(str(returns.kurtosis()))
            
            # VaR, CVaR
            var_95 = Decimal(str(np.percentile(returns, 5)))
            cvar_95 = Decimal(str(returns[returns <= np.percentile(returns, 5)].mean()))
        else:
            volatility = max_drawdown = sharpe_ratio = sortino_ratio = calmar_ratio = Decimal("0")
            max_duration = 0
            skewness = kurtosis = var_95 = cvar_95 = Decimal("0")
        
        # CAGR 계산 (데이터 기간 기준)
        if len(equity_df) > 1:
            start_date = equity_df['datetime'].iloc[0]
            end_date = equity_df['datetime'].iloc[-1]
            years = (end_date - start_date).days / 365.25
            cagr = Decimal(str((pow(float(final_balance / initial_balance), 1/years) - 1) * 100)) if years > 0 else Decimal("0")
        else:
            cagr = Decimal("0")
        
        return BacktestResult(
            strategy_name=self.strategy.id,
            symbol=self.strategy.symbol,
            timeframe=self.strategy.timeframe,
            initial_balance=initial_balance,
            final_balance=final_balance,
            total_return_pct=total_return_pct,
            cagr=cagr,
            annualized_return=cagr,  # 같은 값 사용
            volatility=volatility,
            max_drawdown=max_drawdown,
            max_drawdown_duration=max_duration,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            total_trades=total_trades,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_trade=avg_trade,
            expectancy=expectancy,
            trades=trades,
            equity_curve=equity_df,
            skewness=skewness,
            kurtosis=kurtosis,
            var_95=var_95,
            cvar_95=cvar_95
        )
