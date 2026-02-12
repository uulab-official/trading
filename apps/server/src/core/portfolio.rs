use anyhow::Result;
use chrono::Utc;
use dashmap::DashMap;
use std::sync::Arc;
use tracing::{info, warn};

use crate::core::models::{Order, OrderSide, Position, Trade};

pub struct PortfolioManager {
    positions: Arc<DashMap<String, Position>>,
    balance: Arc<dashmap::DashMap<String, f64>>,
    trades: Arc<dashmap::DashMap<String, Trade>>,
    total_value: f64,
}

impl PortfolioManager {
    pub fn new() -> Self {
        Self {
            positions: Arc::new(DashMap::new()),
            balance: Arc::new(dashmap::DashMap::new()),
            trades: Arc::new(dashmap::DashMap::new()),
            total_value: 0.0,
        }
    }

    pub fn update_position_prices(&self, symbol: &str, current_price: f64) {
        if let Some(mut position) = self.positions.get_mut(symbol) {
            position.current_price = current_price;
            position.unrealized_pnl = if position.side == OrderSide::Buy {
                (current_price - position.entry_price) * position.quantity
            } else {
                (position.entry_price - current_price) * position.quantity
            };
        }
    }

    pub fn open_position(&self, order: &Order, fill_price: f64) -> Result<()> {
        let position = Position {
            symbol: order.symbol.clone(),
            side: order.side.clone(),
            quantity: order.quantity,
            entry_price: fill_price,
            current_price: fill_price,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            opened_at: Utc::now(),
            leverage: 1.0,
        };

        self.positions.insert(order.symbol.clone(), position);
        info!(
            symbol = %order.symbol,
            side = ?order.side,
            quantity = order.quantity,
            "Position opened"
        );
        Ok(())
    }

    pub fn close_position(&self, symbol: &str, exit_price: f64) -> Result<f64> {
        if let Some((_, position)) = self.positions.remove(symbol) {
            let pnl = if position.side == OrderSide::Buy {
                (exit_price - position.entry_price) * position.quantity
            } else {
                (position.entry_price - exit_price) * position.quantity
            };

            info!(
                symbol = %symbol,
                realized_pnl = pnl,
                "Position closed"
            );
            Ok(pnl)
        } else {
            warn!("Attempted to close non-existent position: {}", symbol);
            Ok(0.0)
        }
    }

    pub fn get_position(&self, symbol: &str) -> Option<Position> {
        self.positions.get(symbol).map(|p| p.clone())
    }

    pub fn get_all_positions(&self) -> Vec<Position> {
        self.positions
            .iter()
            .map(|entry| entry.value().clone())
            .collect()
    }

    pub fn get_total_value(&self) -> f64 {
        let positions_value: f64 = self
            .positions
            .iter()
            .map(|entry| entry.value().current_price * entry.value().quantity)
            .sum();
        positions_value
    }

    pub fn get_total_pnl(&self) -> (f64, f64) {
        let unrealized: f64 = self
            .positions
            .iter()
            .map(|entry| entry.value().unrealized_pnl)
            .sum();
        let realized: f64 = self
            .trades
            .iter()
            .map(|entry| {
                let trade = entry.value();
                if trade.side == OrderSide::Sell {
                    (trade.price - trade.commission) * trade.quantity
                } else {
                    -(trade.price + trade.commission) * trade.quantity
                }
            })
            .sum();
        (unrealized, realized)
    }

    pub fn record_trade(&self, order: &Order, fill_price: f64, commission: f64) {
        let trade = Trade {
            id: format!("trade-{}", Utc::now().timestamp_millis()),
            order_id: order.id.clone(),
            symbol: order.symbol.clone(),
            side: order.side.clone(),
            quantity: order.quantity,
            price: fill_price,
            timestamp: Utc::now(),
            commission,
            commission_asset: "USDT".to_string(),
        };
        self.trades.insert(trade.id.clone(), trade);
    }

    pub fn update_balance(&self, asset: &str, amount: f64) {
        self.balance.insert(asset.to_string(), amount);
    }

    pub fn get_balance(&self, asset: &str) -> f64 {
        self.balance.get(asset).map(|b| *b).unwrap_or(0.0)
    }
}
