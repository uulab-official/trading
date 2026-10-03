"""Tests for trading_backtest package"""

import unittest
import pandas as pd
from decimal import Decimal

from trading_backtest import BacktestEngine, BacktestConfig, VirtualBroker
from trading_backtest.brokers.virtual_broker import BrokerConfig
from trading_strategy import RSIStrategy, StrategyConfig


class TestVirtualBroker(unittest.TestCase):
    """VirtualBroker tests"""
    
    def test_initial_balance(self):
        """Test initial balance"""
        broker = VirtualBroker(initial_balance=Decimal("10000"))
        assert broker.balance == Decimal("10000")
    
    def test_market_order(self):
        """Test market order execution"""
        broker = VirtualBroker(
            config=BrokerConfig(slippage_pct=Decimal("0")),
            initial_balance=Decimal("10000")
        )
        
        bar = {
            'open': 50000,
            'high': 51000,
            'low': 49000,
            'close': 50500,
            'volume': 1000
        }
        
        order = broker.submit_order(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.1"),
            order_type="market",
            current_bar=bar
        )
        
        assert order.status.value == "filled"
        assert order.filled_amount == Decimal("0.1")
    
    def test_position_tracking(self):
        """Test position tracking"""
        broker = VirtualBroker(initial_balance=Decimal("10000"))
        
        bar = {
            'open': 50000,
            'high': 51000,
            'low': 49000,
            'close': 50500,
            'volume': 1000
        }
        
        broker.submit_order(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.1"),
            order_type="market",
            current_bar=bar
        )
        
        position = broker.get_position("BTC/USDT")
        assert position == Decimal("0.1")


class TestBacktestEngine(unittest.TestCase):
    """BacktestEngine tests"""
    
    def test_rsi_backtest(self):
        """Test RSI strategy backtest"""
        # Create sample data
        dates = pd.date_range(start='2024-01-01', periods=100, freq='1h')
        data = []
        
        # Generate trending data
        for i, date in enumerate(dates):
            price = 50000 + i * 10  # Upward trend
            data.append({
                'timestamp': int(date.timestamp() * 1000),
                'open': price - 50,
                'high': price + 100,
                'low': price - 100,
                'close': price,
                'volume': 1000
            })
        
        df = pd.DataFrame(data)
        
        # Create strategy
        config = StrategyConfig(name="test_rsi", symbol="BTC/USDT", timeframe="1h")
        strategy = RSIStrategy(config, rsi_period=14, oversold=30, overbought=70)
        
        # Run backtest
        engine = BacktestEngine(BacktestConfig(initial_balance=Decimal("10000")))
        result = engine.run(strategy, df)
        
        # Verify results
        assert result is not None
        assert result.initial_balance == Decimal("10000")
        assert isinstance(result.total_trades, int)
        assert isinstance(result.total_return_pct, Decimal)
    
    def test_performance_metrics(self):
        """Test performance metrics calculation"""
        dates = pd.date_range(start='2024-01-01', periods=50, freq='1h')
        data = []
        
        for i, date in enumerate(dates):
            price = 50000 + (i % 10 - 5) * 100  # Oscillating price
            data.append({
                'timestamp': int(date.timestamp() * 1000),
                'open': price - 50,
                'high': price + 100,
                'low': price - 100,
                'close': price,
                'volume': 1000
            })
        
        df = pd.DataFrame(data)
        
        config = StrategyConfig(name="test", symbol="BTC/USDT", timeframe="1h")
        strategy = RSIStrategy(config)
        
        engine = BacktestEngine(BacktestConfig())
        result = engine.run(strategy, df)
        
        # Check metrics exist
        assert hasattr(result, 'sharpe_ratio')
        assert hasattr(result, 'max_drawdown')
        assert hasattr(result, 'win_rate')
        assert hasattr(result, 'profit_factor')
