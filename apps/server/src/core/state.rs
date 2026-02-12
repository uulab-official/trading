use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tokio::sync::RwLock;

use crate::core::database::Database;
use crate::core::models::{Order, Position};

pub struct StateStore {
    db: Database,
    cache: RwLock<StateCache>,
}

#[derive(Default)]
struct StateCache {
    orders: HashMap<String, Order>,
    positions: HashMap<String, Position>,
}

impl StateStore {
    pub async fn new(db: Database) -> Result<Self> {
        let cache = RwLock::new(StateCache::default());
        Ok(Self { db, cache })
    }

    pub async fn save_order(&self, order: &Order) -> Result<()> {
        self.db.save_order(order).await?;
        let mut cache = self.cache.write().await;
        cache.orders.insert(order.id.clone(), order.clone());
        Ok(())
    }

    pub async fn get_order(&self, order_id: &str) -> Result<Option<Order>> {
        {
            let cache = self.cache.read().await;
            if let Some(order) = cache.orders.get(order_id) {
                return Ok(Some(order.clone()));
            }
        }
        self.db.get_order(order_id).await
    }

    pub async fn save_position(&self, position: &Position) -> Result<()> {
        self.db.save_position(position).await?;
        let mut cache = self.cache.write().await;
        cache.positions.insert(position.symbol.clone(), position.clone());
        Ok(())
    }

    pub async fn get_position(&self, symbol: &str) -> Result<Option<Position>> {
        {
            let cache = self.cache.read().await;
            if let Some(position) = cache.positions.get(symbol) {
                return Ok(Some(position.clone()));
            }
        }
        self.db.get_position(symbol).await
    }

    pub async fn persist_all(&self) -> Result<()> {
        let cache = self.cache.read().await;
        for order in cache.orders.values() {
            self.db.save_order(order).await?;
        }
        for position in cache.positions.values() {
            self.db.save_position(position).await?;
        }
        Ok(())
    }
}