use anyhow::Result;
use chrono::Utc;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};
use tracing::info;

use crate::core::models::{Candle, Order, Ticker};

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

fn unsupported_operation(exchange: &'static str, operation: &'static str) -> anyhow::Error {
    anyhow::anyhow!("{exchange} {operation} is not implemented; no broker request was made")
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

    /// Return a passive event subscription; active transport/account methods
    /// are unsupported and never delegate execution to the registered adapter.
    pub async fn get_exchange(&self, id: &str) -> Option<Box<dyn Exchange>> {
        let exchanges = self.exchanges.read().await;
        exchanges.get(id).map(|e| {
            Box::new(EventReceiverProxy {
                event_rx: e.get_event_receiver(),
            }) as Box<dyn Exchange>
        })
    }

    pub fn subscribe(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_tx.subscribe()
    }
}

struct EventReceiverProxy {
    event_rx: broadcast::Receiver<ExchangeEvent>,
}

#[async_trait::async_trait]
impl Exchange for EventReceiverProxy {
    async fn connect(&mut self) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "connect",
        ))
    }
    async fn disconnect(&mut self) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "disconnect",
        ))
    }
    async fn subscribe_ticker(&mut self, _symbol: &str) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "subscribe_ticker",
        ))
    }
    async fn subscribe_candles(&mut self, _symbol: &str, _timeframe: &str) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "subscribe_candles",
        ))
    }
    async fn subscribe_orderbook(&mut self, _symbol: &str) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "subscribe_orderbook",
        ))
    }
    async fn place_order(&self, _order: &Order) -> Result<String> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "place_order",
        ))
    }
    async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> Result<()> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "cancel_order",
        ))
    }
    async fn get_balance(&self) -> Result<HashMap<String, f64>> {
        Err(unsupported_operation(
            "ExchangeManager event proxy",
            "get_balance",
        ))
    }
    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_rx.resubscribe()
    }
}
