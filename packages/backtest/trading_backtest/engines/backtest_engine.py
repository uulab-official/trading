"""Backtest Engine - 백테스트 엔진"""

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import logging

from trading_strategy import Strategy, Signal, SignalType
from trading_data import Candle, CandleBuffer
from trading_data._validation import nonnegative_decimal, positive_decimal, positive_integer

from trading_backtest.brokers.virtual_broker import VirtualBroker, BrokerConfig
from trading_backtest.trade_report import TradeReport

logger = logging.getLogger(__name__)
ACCOUNTING_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


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

    def __post_init__(self) -> None:
        self.initial_balance = positive_decimal(self.initial_balance, "initial_balance")
        self.position_size_pct = positive_decimal(self.position_size_pct, "position_size_pct")
        if self.position_size_pct > 100:
            raise ValueError("position_size_pct must not exceed 100")
        for field in ("commission_pct", "slippage_pct"):
            value = nonnegative_decimal(getattr(self, field), field)
            if value >= 100:
                raise ValueError(f"{field} must be below 100")
            setattr(self, field, value)
        self.max_positions = positive_integer(self.max_positions, "max_positions")


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
    profit_factor: Optional[Decimal]
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

    # final_balance remains cash for compatibility; returns use marked equity.
    final_equity: Optional[Decimal] = None
    closed_trade_count: int = 0
    break_even_count: int = 0
    realized_net_pnl: Decimal = Decimal("0")
    gross_realized_pnl: Decimal = Decimal("0")
    trade_report: Optional[TradeReport] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            'strategy_name': self.strategy_name,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'initial_balance': float(self.initial_balance),
            'final_balance': float(self.final_balance),
            'final_equity': float(self.final_equity if self.final_equity is not None else self.final_balance),
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
            'closed_trade_count': self.closed_trade_count,
            'break_even_count': self.break_even_count,
            'realized_net_pnl': float(self.realized_net_pnl),
            'gross_realized_pnl': float(self.gross_realized_pnl),
            'trade_report': self.trade_report.to_dict() if self.trade_report is not None else None,
            'win_count': self.win_count,
            'loss_count': self.loss_count,
            'win_rate': float(self.win_rate),
            'profit_factor': float(self.profit_factor) if self.profit_factor is not None else None,
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
        self._last_prices: Dict[str, Decimal] = {}
        
    def run(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> BacktestResult:
        """Run local OHLCV data with causal next-open fills and fixed accounting."""
        with localcontext(ACCOUNTING_CONTEXT):
            return self._run(strategy, data, progress_callback)

    def _run(
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
        
        # Validate the entire local fixture before any simulated order can fill.
        candles = self._prepare_candles(data, strategy.timeframe)
        self.strategy = strategy
        self.data = data
        self.equity_curve.clear()
        self.signals.clear()
        self._last_prices.clear()
        strategy.reset()
        
        # 브로커 초기화
        broker_config = BrokerConfig(
            slippage_pct=self.config.slippage_pct,
            slippage_volatility_factor=False,
            taker_fee_pct=self.config.commission_pct,
            maker_fee_pct=self.config.commission_pct
        )
        self.broker = VirtualBroker(
            config=broker_config,
            initial_balance=self.config.initial_balance
        )
        
        # 백테스트 루프
        total_bars = len(candles)
        prior_volume = Decimal("0")
        
        for i, candle in enumerate(candles):
            # 진행률 콜백
            if progress_callback and i % 100 == 0:
                progress_callback(i, total_bars)
            
            # Pending market orders execute at this open using only known inputs.
            # Current high/low/close/volume are unavailable until the bar closes.
            self.broker.process_bar({
                'open': candle.open, 'high': candle.open, 'low': candle.open,
                'close': candle.open, 'volume': prior_volume,
                'timestamp': candle.timestamp,
            })
            prior_volume = candle.volume
            bar_dict = {
                'open': candle.open,
                'high': candle.high,
                'low': candle.low,
                'close': candle.close,
                'volume': candle.volume,
                'timestamp': candle.timestamp
            }
            
            # 전략에 캔들 전달
            signal = strategy.on_candle(candle)
            
            if signal and signal.type != SignalType.HOLD:
                self.signals.append(signal)
                self._execute_signal(signal, bar_dict)
            
            # 자본 기록
            current_price = candle.close
            self._last_prices = {strategy.symbol: current_price}
            total_value = self.broker.get_total_value(self._last_prices)
            
            self.equity_curve.append({
                'timestamp': candle.timestamp,
                'datetime': datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc),
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

    @staticmethod
    def _prepare_candles(data: pd.DataFrame, timeframe: str) -> List[Candle]:
        if len(data) < 50:
            raise ValueError("Insufficient data for backtest (minimum 50 bars)")
        duration = CandleBuffer(timeframe=timeframe).duration_ms
        candles = []
        for index, row in zip(data.index, data.to_dict(orient="records")):
            ts = row.get('timestamp', index)
            if isinstance(ts, (datetime, pd.Timestamp)):
                ts = pd.Timestamp(ts)
                ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
                timestamp = int(ts.value // 1000000)
            else:
                if isinstance(ts, (bool, str)):
                    raise ValueError("timestamp must be Unix milliseconds or a datetime")
                timestamp = int(ts)
                if timestamp != ts:
                    raise ValueError("timestamp must be whole Unix milliseconds")
            candle = Candle(timestamp, row['open'], row['high'], row['low'],
                            row['close'], row.get('volume', 0))
            if candles and candle.timestamp < candles[-1].timestamp + duration:
                raise ValueError("candles must be chronological without duplicate or overlapping bars")
            candles.append(candle)
        return candles
    
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
                current_bar=None
            )
        elif signal.type in (SignalType.SELL, SignalType.CLOSE):
            # 매도
            position_size = self.broker.get_position(symbol)
            if position_size > 0:
                self.broker.submit_order(
                    symbol=symbol,
                    side='sell',
                    amount=position_size,
                    order_type='market',
                    current_bar=None
                )
    
    def _calculate_result(self) -> BacktestResult:
        """백테스트 결과 계산"""
        if not self.broker or not self.strategy:
            raise RuntimeError("Backtest not run yet")
        
        # 기본 데이터
        initial_balance = self.config.initial_balance
        final_balance = self.broker.balance
        final_equity = self.broker.get_total_value(self._last_prices)
        
        # 수익률 계산
        total_return = (final_equity - initial_balance) / initial_balance
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
        
        trade_report = self.broker.get_trade_report()
        
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
            cagr = Decimal(str((pow(float(final_equity / initial_balance), 1/years) - 1) * 100)) if years > 0 else Decimal("0")
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
            win_count=trade_report.win_count,
            loss_count=trade_report.loss_count,
            win_rate=trade_report.win_rate,
            profit_factor=trade_report.profit_factor,
            avg_win=trade_report.avg_win,
            avg_loss=trade_report.avg_loss,
            avg_trade=trade_report.avg_trade,
            expectancy=trade_report.expectancy,
            trades=trades,
            equity_curve=equity_df,
            skewness=skewness,
            kurtosis=kurtosis,
            var_95=var_95,
            cvar_95=cvar_95,
            final_equity=final_equity,
            closed_trade_count=trade_report.closed_trade_count,
            break_even_count=trade_report.break_even_count,
            realized_net_pnl=trade_report.realized_net_pnl,
            gross_realized_pnl=trade_report.gross_realized_pnl,
            trade_report=trade_report
        )
