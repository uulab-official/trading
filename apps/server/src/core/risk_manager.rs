use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use tracing::{info, warn};

use crate::core::models::{Order, OrderSide, Position};

pub struct RiskManager {
    config: RiskConfig,
    daily_pnl: f64,
    daily_loss_limit: f64,
    last_reset: DateTime<Utc>,
    circuit_breaker_triggered: bool,
}

#[derive(Clone)]
pub struct RiskConfig {
    pub max_daily_loss_pct: f64,
    pub max_positions: usize,
    pub risk_per_trade_pct: f64,
    pub max_leverage: f64,
    pub circuit_breaker_threshold: f64,
}

impl RiskManager {
    pub fn new(config: RiskConfig) -> Self {
        let daily_loss_limit = config.max_daily_loss_pct / 100.0;
        Self {
            config,
            daily_pnl: 0.0,
            daily_loss_limit,
            last_reset: Utc::now(),
            circuit_breaker_triggered: false,
        }
    }

    pub fn check_trade_allowed(
        &self,
        order: &Order,
        current_balance: f64,
        open_positions: &[Position],
    ) -> Result<bool> {
        if self.circuit_breaker_triggered {
            warn!("Circuit breaker triggered, trading disabled");
            return Ok(false);
        }

        if self.daily_pnl < -self.daily_loss_limit * current_balance {
            warn!("Daily loss limit reached");
            return Ok(false);
        }

        if open_positions.len() >= self.config.max_positions {
            warn!(
                "Maximum positions limit reached: {}",
                self.config.max_positions
            );
            return Ok(false);
        }

        let position_count = open_positions
            .iter()
            .filter(|p| p.symbol == order.symbol)
            .count();
        if position_count > 0 {
            warn!("Position already exists for {}", order.symbol);
        }

        let risk_amount = current_balance * (self.config.risk_per_trade_pct / 100.0);
        let trade_value = order.quantity * order.price.unwrap_or(0.0);

        if trade_value > risk_amount * 10.0 {
            warn!("Trade value exceeds risk limits");
            return Ok(false);
        }

        Ok(true)
    }

    pub fn update_pnl(&mut self, pnl: f64) {
        self.daily_pnl += pnl;

        if self.daily_pnl < -self.daily_loss_limit {
            warn!("Daily loss limit exceeded: {}", self.daily_pnl);
        }
    }

    pub fn check_circuit_breaker(&mut self, drawdown_pct: f64) {
        if drawdown_pct > self.config.circuit_breaker_threshold && !self.circuit_breaker_triggered {
            self.circuit_breaker_triggered = true;
            warn!("Circuit breaker triggered! Drawdown: {}%", drawdown_pct);
        }
    }

    pub fn reset_daily(&mut self) {
        let now = Utc::now();
        if now.date_naive() != self.last_reset.date_naive() {
            self.daily_pnl = 0.0;
            self.last_reset = now;
            self.circuit_breaker_triggered = false;
            info!("Daily risk metrics reset");
        }
    }

    pub fn is_trading_allowed(&self) -> bool {
        !self.circuit_breaker_triggered
    }

    pub fn calculate_position_size(&self, balance: f64, entry_price: f64, stop_loss: f64) -> f64 {
        let risk_amount = balance * (self.config.risk_per_trade_pct / 100.0);
        let price_risk = (entry_price - stop_loss).abs();

        if price_risk <= 0.0 {
            return 0.0;
        }

        let position_size = risk_amount / price_risk;
        position_size.min(balance * self.config.max_leverage / entry_price)
    }

    pub fn calculate_stop_loss(&self, entry_price: f64, side: &OrderSide, atr: f64) -> f64 {
        let stop_distance = atr * 2.0;

        match side {
            OrderSide::Buy => entry_price - stop_distance,
            OrderSide::Sell => entry_price + stop_distance,
        }
    }
}
