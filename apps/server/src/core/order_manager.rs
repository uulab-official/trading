use anyhow::Result;
use chrono::Utc;
use dashmap::DashMap;
use std::sync::Arc;
use tracing::{error, info};

use crate::core::models::{Order, OrderSide, OrderStatus, OrderType};
use crate::exchange::ExchangeEvent;

pub struct OrderManager {
    orders: Arc<DashMap<String, Order>>,
    pending_orders: Arc<DashMap<String, Order>>,
}

impl OrderManager {
    pub fn new() -> Self {
        Self {
            orders: Arc::new(DashMap::new()),
            pending_orders: Arc::new(DashMap::new()),
        }
    }

    pub async fn create_order(
        &self,
        symbol: String,
        side: OrderSide,
        order_type: OrderType,
        quantity: f64,
        price: Option<f64>,
    ) -> Result<Order> {
        let order_id = format!("order-{}", Utc::now().timestamp_millis());
        let now = Utc::now();

        let order = Order {
            id: order_id.clone(),
            symbol,
            side,
            order_type,
            quantity,
            price,
            status: OrderStatus::Pending,
            created_at: now,
            updated_at: now,
            exchange_order_id: None,
            filled_quantity: 0.0,
            average_price: None,
            commission: None,
            commission_asset: None,
        };

        self.pending_orders.insert(order_id.clone(), order.clone());
        info!(order_id = %order_id, symbol = %order.symbol, "Order created");

        Ok(order)
    }

    pub fn update_order_status(&self, order_id: &str, status: OrderStatus) {
        if let Some(mut order) = self.orders.get_mut(order_id) {
            order.status = status;
            order.updated_at = Utc::now();
        }
    }

    pub fn get_order(&self, order_id: &str) -> Option<Order> {
        self.orders.get(order_id).map(|o| o.clone())
    }

    pub fn get_open_orders(&self) -> Vec<Order> {
        self.orders
            .iter()
            .filter(|entry| matches!(entry.value().status, OrderStatus::Open | OrderStatus::PartiallyFilled))
            .map(|entry| entry.value().clone())
            .collect()
    }

    pub fn handle_exchange_event(&self, event: &ExchangeEvent) {
        match event {
            ExchangeEvent::TradeUpdate(order) => {
                self.update_order_status(&order.id, order.status.clone());
            }
            _ => {}
        }
    }
}