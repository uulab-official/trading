use anyhow::{anyhow, ensure, Result};
use chrono::Utc;
use dashmap::mapref::entry::Entry;
use dashmap::DashMap;
use std::sync::Arc;
use tracing::{info, warn};
use uuid::Uuid;

use crate::core::models::{Order, OrderSide, OrderStatus, OrderType};
use crate::exchange::ExchangeEvent;

pub struct OrderManager {
    orders: Arc<DashMap<String, Order>>,
}

impl OrderManager {
    pub fn new() -> Self {
        Self {
            orders: Arc::new(DashMap::new()),
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
        let order_id = format!("order-{}", Uuid::new_v4());
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

        self.restore_order(order.clone())?;
        info!(order_id = %order_id, symbol = %order.symbol, "Order created");

        Ok(order)
    }

    pub fn update_order_status(&self, order_id: &str, status: OrderStatus) {
        if let Err(error) = self.try_update_order_status(order_id, status) {
            warn!(%error, "Order status update refused");
        }
    }

    /// Validate and restore an owned snapshot without replacing an existing order.
    pub fn restore_order(&self, order: Order) -> Result<()> {
        let entry = self.orders.entry(order.id.clone());
        validate_snapshot(&order)?;
        match entry {
            Entry::Vacant(entry) => {
                entry.insert(order);
            }
            Entry::Occupied(entry) => {
                ensure!(entry.get() == &order, "Conflicting existing order snapshot");
            }
        }
        Ok(())
    }

    /// Atomically apply a validated cumulative update to a known local order.
    pub fn apply_order_update(&self, order: &Order) -> Result<()> {
        let mut current = self
            .orders
            .get_mut(&order.id)
            .ok_or_else(|| anyhow!("Unknown local order"))?;
        if validate_update(&current, order)? {
            *current = order.clone();
        }
        Ok(())
    }

    /// Change status without inventing fill or account metadata.
    pub fn try_update_order_status(&self, order_id: &str, status: OrderStatus) -> Result<()> {
        let mut current = self
            .orders
            .get_mut(order_id)
            .ok_or_else(|| anyhow!("Unknown local order"))?;
        let mut next = current.clone();
        next.status = status;
        next.updated_at = Utc::now().max(current.updated_at);
        if validate_update(&current, &next)? {
            *current = next;
        }
        Ok(())
    }

    pub fn get_order(&self, order_id: &str) -> Option<Order> {
        self.orders.get(order_id).map(|o| o.clone())
    }

    pub fn get_open_orders(&self) -> Vec<Order> {
        self.orders
            .iter()
            .filter(|entry| {
                matches!(
                    entry.value().status,
                    OrderStatus::Pending | OrderStatus::Open | OrderStatus::PartiallyFilled
                )
            })
            .map(|entry| entry.value().clone())
            .collect()
    }

    pub fn get_all_orders(&self) -> Vec<Order> {
        self.orders
            .iter()
            .map(|entry| entry.value().clone())
            .collect()
    }

    pub fn handle_exchange_event(&self, event: &ExchangeEvent) {
        if let ExchangeEvent::TradeUpdate(order) = event {
            if let Err(error) = self.apply_order_update(order) {
                warn!(%error, "Exchange order update refused");
            }
        }
    }
}

fn valid_text(value: &str) -> bool {
    !value.trim().is_empty() && !value.chars().any(char::is_control)
}

fn cumulative_gross(order: &Order) -> f64 {
    order.filled_quantity * order.average_price.unwrap_or(0.0)
}

fn validate_snapshot(order: &Order) -> Result<()> {
    ensure!(valid_text(&order.id), "Invalid local order ID");
    ensure!(valid_text(&order.symbol), "Invalid order symbol");
    ensure!(
        order.quantity.is_finite() && order.quantity > 0.0,
        "Order quantity must be finite and positive"
    );
    match order.price {
        Some(price) => ensure!(
            price.is_finite() && price > 0.0,
            "Order price must be finite and positive"
        ),
        None => ensure!(
            order.order_type == OrderType::Market,
            "Non-market orders require a price"
        ),
    }
    ensure!(
        order.updated_at >= order.created_at,
        "Order update timestamp precedes creation"
    );
    if let Some(exchange_id) = &order.exchange_order_id {
        ensure!(valid_text(exchange_id), "Invalid exchange order ID");
    }
    ensure!(
        order.filled_quantity.is_finite()
            && order.filled_quantity >= 0.0
            && order.filled_quantity <= order.quantity,
        "Filled quantity is outside the requested quantity"
    );
    if order.filled_quantity == 0.0 {
        ensure!(
            order.average_price.is_none(),
            "Unfilled orders cannot have an average fill price"
        );
    } else {
        ensure!(
            order
                .average_price
                .is_some_and(|price| price.is_finite() && price > 0.0),
            "Positive fills require a finite positive average price"
        );
        let gross = cumulative_gross(order);
        ensure!(
            gross.is_finite() && gross > 0.0,
            "Filled gross value must be finite and positive"
        );
    }
    let status_matches_fill = match order.status {
        OrderStatus::Pending | OrderStatus::Open | OrderStatus::Failed => {
            order.filled_quantity == 0.0
        }
        OrderStatus::PartiallyFilled => {
            order.filled_quantity > 0.0 && order.filled_quantity < order.quantity
        }
        OrderStatus::Filled => order.filled_quantity == order.quantity,
        OrderStatus::Canceled => order.filled_quantity < order.quantity,
    };
    ensure!(
        status_matches_fill,
        "Order status conflicts with filled quantity"
    );
    match (order.commission, &order.commission_asset) {
        (None, None) => {}
        (Some(amount), Some(asset)) => {
            ensure!(
                amount.is_finite() && amount >= 0.0,
                "Commission must be finite and nonnegative"
            );
            ensure!(valid_text(asset), "Invalid commission asset");
            ensure!(
                amount == 0.0 || order.filled_quantity > 0.0,
                "Positive commission requires a positive fill"
            );
        }
        _ => {
            return Err(anyhow!(
                "Commission amount and asset must be provided together"
            ))
        }
    }
    Ok(())
}

/// Returns false for a terminal duplicate, which must leave the snapshot untouched.
fn validate_update(current: &Order, next: &Order) -> Result<bool> {
    validate_snapshot(next)?;
    ensure!(
        current.id == next.id
            && current.symbol == next.symbol
            && current.side == next.side
            && current.order_type == next.order_type
            && current.quantity == next.quantity
            && current.price == next.price
            && current.created_at == next.created_at,
        "Order identity and requested terms cannot change"
    );
    ensure!(
        next.updated_at >= current.updated_at,
        "Stale order update timestamp"
    );
    if current.exchange_order_id.is_some() {
        ensure!(
            next.exchange_order_id == current.exchange_order_id,
            "Known exchange order ID cannot change or disappear"
        );
    }
    ensure!(
        next.filled_quantity >= current.filled_quantity,
        "Cumulative filled quantity cannot decrease"
    );
    if next.filled_quantity == current.filled_quantity {
        ensure!(
            next.average_price == current.average_price,
            "Unchanged filled quantity cannot rewrite its average price"
        );
    } else {
        ensure!(
            cumulative_gross(next) > cumulative_gross(current),
            "Additional fills must increase cumulative gross value"
        );
    }
    if let Some(previous_commission) = current.commission {
        ensure!(
            next.commission
                .is_some_and(|amount| amount >= previous_commission),
            "Known cumulative commission cannot decrease or disappear"
        );
        ensure!(
            next.commission_asset == current.commission_asset,
            "Known commission asset cannot change"
        );
    }
    let allowed = match current.status {
        OrderStatus::Pending => true,
        OrderStatus::Open => matches!(
            next.status,
            OrderStatus::Open
                | OrderStatus::PartiallyFilled
                | OrderStatus::Filled
                | OrderStatus::Canceled
                | OrderStatus::Failed
        ),
        OrderStatus::PartiallyFilled => matches!(
            next.status,
            OrderStatus::PartiallyFilled | OrderStatus::Filled | OrderStatus::Canceled
        ),
        OrderStatus::Filled | OrderStatus::Canceled | OrderStatus::Failed => {
            let mut repeated = next.clone();
            repeated.updated_at = current.updated_at;
            ensure!(&repeated == current, "Terminal order content cannot change");
            return Ok(false);
        }
    };
    ensure!(allowed, "Invalid order status transition");
    Ok(true)
}
