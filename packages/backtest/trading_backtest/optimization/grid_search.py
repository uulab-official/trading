"""Parameter optimization - 파라미터 최적화"""

import itertools
from dataclasses import dataclass
from typing import Dict, List, Any, Callable, Optional
from decimal import Decimal
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed
import logging

from trading_strategy import Strategy, StrategyConfig
from trading_backtest import BacktestEngine, BacktestConfig

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """최적화 결과"""
    best_params: Dict[str, Any]
    best_score: Decimal
    all_results: pd.DataFrame
    param_grid: Dict[str, List[Any]]
    
    def to_dict(self) -> Dict:
        return {
            'best_params': self.best_params,
            'best_score': float(self.best_score),
            'param_grid': {k: [str(v) for v in vals] for k, vals in self.param_grid.items()},
        }


class GridSearchOptimizer:
    """그리드 서치 최적화"""
    
    def __init__(
        self,
        strategy_class: type,
        param_grid: Dict[str, List[Any]],
        backtest_config: Optional[BacktestConfig] = None
    ):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.backtest_config = backtest_config or BacktestConfig()
    
    def optimize(
        self,
        data: pd.DataFrame,
        metric: str = 'sharpe_ratio',
        n_jobs: int = -1
    ) -> OptimizationResult:
        """
        그리드 서치 실행
        
        Args:
            data: OHLCV 데이터
            metric: 최적화 기준 메트릭 ('sharpe_ratio', 'total_return_pct', etc.)
            n_jobs: 병렬 작업 수 (-1 = 모든 CPU)
        
        Returns:
            OptimizationResult
        """
        # 파라미터 조합 생성
        param_names = list(self.param_grid.keys())
        param_values = list(self.param_grid.values())
        param_combinations = list(itertools.product(*param_values))
        
        logger.info(f"Running grid search with {len(param_combinations)} combinations")
        
        results = []
        
        # 병렬 처리 또는 순차 처리
        if n_jobs == 1:
            for params in param_combinations:
                param_dict = dict(zip(param_names, params))
                result = self._evaluate_params(param_dict, data, metric)
                results.append(result)
        else:
            with ProcessPoolExecutor(max_workers=n_jobs if n_jobs > 0 else None) as executor:
                futures = {
                    executor.submit(self._evaluate_params, dict(zip(param_names, params)), data, metric): params
                    for params in param_combinations
                }
                
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
        
        # 결과 DataFrame 생성
        results_df = pd.DataFrame(results)
        
        # 최적 파라미터 찾기
        scores = pd.to_numeric(results_df[metric], errors='coerce').dropna()
        scores = scores[(scores != float('inf')) & (scores != float('-inf'))]
        if scores.empty:
            raise ValueError(f"No defined finite values for ranking metric {metric}")
        best_idx = scores.idxmax()
        best_params = results_df.loc[best_idx, param_names].to_dict()
        best_score = Decimal(str(results_df.loc[best_idx, metric]))
        
        logger.info(f"Best parameters found: {best_params} with {metric}={best_score}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=results_df,
            param_grid=self.param_grid
        )
    
    def _evaluate_params(
        self,
        params: Dict[str, Any],
        data: pd.DataFrame,
        metric: str
    ) -> Dict:
        """단일 파라미터 조합 평가"""
        try:
            # 전략 생성
            config = StrategyConfig(name='grid_search', symbol='TEST', timeframe='1h')
            strategy = self.strategy_class(config, **params)
            
            # 백테스트 실행
            engine = BacktestEngine(self.backtest_config)
            result = engine.run(strategy, data)
            
            # 결과 수집
            row = params.copy()
            row.update({
                'total_return_pct': float(result.total_return_pct),
                'sharpe_ratio': float(result.sharpe_ratio),
                'max_drawdown': float(result.max_drawdown),
                'win_rate': float(result.win_rate),
                'profit_factor': float(result.profit_factor) if result.profit_factor is not None else None,
                'total_trades': result.total_trades,
                'closed_trade_count': result.closed_trade_count,
                'realized_net_pnl': float(result.realized_net_pnl),
            })
            
            return row
            
        except Exception as e:
            logger.error(f"Error evaluating params {params}: {e}")
            row = params.copy()
            row.update({
                'total_return_pct': -999,
                'sharpe_ratio': -999,
                'max_drawdown': 0,
                'win_rate': 0,
                'profit_factor': 0,
                'total_trades': 0,
            })
            return row


class WalkForwardOptimizer:
    """Walk-Forward Analysis (WFA) 최적화"""
    
    def __init__(
        self,
        strategy_class: type,
        param_grid: Dict[str, List[Any]],
        train_size: int = 252,  # 학습 기간 (캔들 수)
        test_size: int = 63,    # 테스트 기간
        n_splits: int = 5       # 분할 수
    ):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.train_size = train_size
        self.test_size = test_size
        self.n_splits = n_splits
    
    def optimize(self, data: pd.DataFrame, metric: str = 'sharpe_ratio') -> Dict:
        """
        워크 포워드 분석 실행
        
        Returns:
            {
                'train_results': [...],
                'test_results': [...],
                'avg_test_performance': {...}
            }
        """
        results = {
            'train_results': [],
            'test_results': [],
            'splits': []
        }
        
        total_size = len(data)
        window_size = self.train_size + self.test_size
        
        for i in range(self.n_splits):
            end_idx = total_size - (self.n_splits - i - 1) * self.test_size
            start_idx = end_idx - window_size
            
            if start_idx < 0:
                logger.warning(f"Skipping split {i+1}: insufficient data")
                continue
            
            train_data = data.iloc[start_idx:start_idx + self.train_size]
            test_data = data.iloc[start_idx + self.train_size:end_idx]
            
            logger.info(f"Split {i+1}/{self.n_splits}: Train {len(train_data)}, Test {len(test_data)}")
            
            # 학습: 최적 파라미터 찾기
            optimizer = GridSearchOptimizer(
                self.strategy_class,
                self.param_grid,
                BacktestConfig()
            )
            train_result = optimizer.optimize(train_data, metric, n_jobs=1)
            
            # 테스트: 학습된 파라미터로 검증
            config = StrategyConfig(name='wfa_test', symbol='TEST', timeframe='1h')
            strategy = self.strategy_class(config, **train_result.best_params)
            
            engine = BacktestEngine()
            test_backtest = engine.run(strategy, test_data)
            
            results['train_results'].append(train_result)
            results['test_results'].append(test_backtest)
            results['splits'].append({
                'train_start': train_data.index[0],
                'train_end': train_data.index[-1],
                'test_start': test_data.index[0],
                'test_end': test_data.index[-1],
                'best_params': train_result.best_params
            })
        
        # 평균 테스트 성능 계산
        avg_performance = {
            'avg_return': float(sum(r.total_return_pct for r in results['test_results']) / len(results['test_results'])),
            'avg_sharpe': float(sum(r.sharpe_ratio for r in results['test_results']) / len(results['test_results'])),
            'avg_mdd': float(sum(r.max_drawdown for r in results['test_results']) / len(results['test_results'])),
        }
        results['avg_test_performance'] = avg_performance
        
        logger.info(f"Walk-forward analysis complete: {avg_performance}")
        
        return results
