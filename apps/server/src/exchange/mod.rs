use anyhow::Result;
use chrono::Utc;
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::net::TcpStream;
use tokio::sync::{broadcast, RwLock};
use tokio::time::{interval, Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};
use tracing::{error, info, warn};

use crate::core::models::{Candle, Order, OrderSide, OrderStatus, OrderType, Ticker};

pub mod binance;
pub mod bithumb;

#[derive(Debug, Clone)]
pub struct ExchangeConfig {
    pub id: String,
    pub api_key: String,
    pub api_secret: String,
    pub ws_url: String,
    pub rest_url: String,
    pub testnet: bool,
}

#[derive(Debug, Clone)]
pub enum ExchangeEvent {
    Ticker(Ticker),
    Candle(Candle),
    TradeUpdate(Order),
    OrderBook(OrderBook),
    Connected,
    Disconnected,
    Error(String),
}

#[derive(Debug, Clone)]
pub struct OrderBook {
    pub symbol: String,
    pub bids: Vec<(f64, f64)>,
    pub asks: Vec<(f64, f64)>,
    pub timestamp: chrono::DateTime<Utc>,
}

#[async_trait::async_trait]
pub trait Exchange: Send + Sync {
    async fn connect(&mut self) -> Result<()>;
    async fn disconnect(&mut self) -> Result<()>;
    async fn subscribe_ticker(&mut self, symbol: &str) -> Result<()>;
    async fn subscribe_candles(&mut self, symbol: &str, timeframe: &str) -> Result<()>;
    async fn subscribe_orderbook(&mut self, symbol: &str) -> Result<()>;
    async fn place_order(&self, order: &Order) -> Result<String>;
    async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()>;
    async fn get_balance(&self) -> Result<HashMap<String, f64>>;
    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent>;
}

pub struct ExchangeManager {
    exchanges: Arc<RwLock<HashMap<String, Box<dyn Exchange>>>>,
    event_tx: broadcast::Sender<ExchangeEvent>,
}

impl ExchangeManager {
    pub fn new() -> Self {
        let (event_tx, _) = broadcast::channel(1000);
        Self {
            exchanges: Arc::new(RwLock::new(HashMap::new())),
            event_tx,
        }
    }

    pub async fn add_exchange(&self, id: String, exchange: Box<dyn Exchange>) -> Result<()> {
        let mut exchanges = self.exchanges.write().await;
        info!("Exchange {} added to manager", id);
        exchanges.insert(id, exchange);
        Ok(())
    }

    pub async fn get_exchange(&self, id: &str) -> Option<Box<dyn Exchange>> {
        let exchanges = self.exchanges.read().await;
        exchanges.get(id).map(|e| {
            Box::new(DummyExchange {
                event_rx: e.get_event_receiver(),
            }) as Box<dyn Exchange>
        })
    }

    pub fn subscribe(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_tx.subscribe()
    }
}

struct DummyExchange {
    event_rx: broadcast::Receiver<ExchangeEvent>,
}

#[async_trait::async_trait]
impl Exchange for DummyExchange {
    async fn connect(&mut self) -> Result<()> {
        Ok(())
    }
    async fn disconnect(&mut self) -> Result<()> {
        Ok(())
    }
    async fn subscribe_ticker(&mut self, _symbol: &str) -> Result<()> {
        Ok(())
    }
    async fn subscribe_candles(&mut self, _symbol: &str, _timeframe: &str) -> Result<()> {
        Ok(())
    }
    async fn subscribe_orderbook(&mut self, _symbol: &str) -> Result<()> {
        Ok(())
    }
    async fn place_order(&self, _order: &Order) -> Result<String> {
        Ok("dummy".to_string())
    }
    async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> Result<()> {
        Ok(())
    }
    async fn get_balance(&self) -> Result<HashMap<String, f64>> {
        Ok(HashMap::new())
    }
    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_rx.resubscribe()
    }
}