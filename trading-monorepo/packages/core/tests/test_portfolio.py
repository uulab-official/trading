"""Tests for trading_core package"""

import pytest
from decimal import Decimal

from trading_core.portfolio import Portfolio, Position
from trading_core.risk_manager import RiskManager, RiskConfig


class TestPortfolio:
    """Portfolio tests"""
    
    def test_initial_balance(self):
        """Test initial balance"""
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        assert portfolio.get_total_value() == Decimal("1000")
        assert portfolio.cash_balance == Decimal("1000")
    
    def test_open_position(self):
        """Test opening a position"""
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        
        position = portfolio.open_position(
            symbol="BTC/USDT",
            side="long",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            stop_loss=Decimal("48000"),
            take_profit=Decimal("55000")
        )
        
        assert position.symbol == "BTC/USDT"
        assert position.size == Decimal("0.1")
        assert portfolio.has_position("BTC/USDT")
        assert portfolio.get_position("BTC/USDT") == position
    
    def test_position_calculation(self):
        """Test position PnL calculation"""
        position = Position(
            symbol="BTC/USDT",
            side="long",
            size=Decimal("0.1"),
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("48000"),
            take_profit_price=Decimal("55000")
        )
        
        # Price goes up 10%
        position.update_price(Decimal("55000"))
        
        assert position.unrealized_pnl == Decimal("500")  # (55000 - 50000) * 0.1
        assert position.unrealized_pnl_pct == Decimal("10")


class TestRiskManager:
    """RiskManager tests"""
    
    def test_position_sizing(self):
        """Test position size calculation"""
        config = RiskConfig(
            risk_per_trade_pct=Decimal("1"),
            max_position_size_pct=Decimal("10")
        )
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        risk_manager = RiskManager(config, portfolio)
        
        # 1% risk, 2% stop loss
        size = risk_manager.calculate_position_size(
            entry_price=Decimal("50000"),
            stop_loss_price=Decimal("49000"),
            account_balance=Decimal("1000")
        )
        
        # Should risk $10 (1% of 1000), stop distance is $1000
        # Position size = 10 / 1000 = 0.01
        assert size > 0
    
    def test_daily_loss_limit(self):
        """Test daily loss limit"""
        config = RiskConfig(max_daily_loss_pct=Decimal("3"))
        portfolio = Portfolio(initial_balance=Decimal("1000"))
        risk_manager = RiskManager(config, portfolio)
        
        # Simulate 5% loss
        portfolio._daily_pnl = Decimal("-50")
        portfolio._daily_start_value = Decimal("1000")
        
        allowed, reason = risk_manager.check_trade_allowed(
            symbol="BTC/USDT",
            side="buy",
            amount=Decimal("0.1"),
            price=Decimal("50000")
        )
        
        assert not allowed
        assert "Daily loss limit" in reason
