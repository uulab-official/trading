use anyhow::Result;
use std::collections::HashMap;
use tokio::sync::broadcast;

use crate::core::models::Order;
use crate::exchange::{Exchange, ExchangeConfig, ExchangeEvent};

pub struct BithumbClient {
    event_tx: broadcast::Sender<ExchangeEvent>,
}

impl BithumbClient {
    pub fn new(_config: ExchangeConfig) -> Self {
        // The current stub adapter does not consume exchange configuration.
        let (event_tx, _) = broadcast::channel(1000);
        Self { event_tx }
    }
}

#[async_trait::async_trait]
impl Exchange for BithumbClient {
    async fn connect(&mut self) -> Result<()> {
        Err(super::unsupported_operation("Bithumb", "connect"))
    }

    async fn disconnect(&mut self) -> Result<()> {
        Err(super::unsupported_operation("Bithumb", "disconnect"))
    }

    async fn subscribe_ticker(&mut self, _symbol: &str) -> Result<()> {
        Err(super::unsupported_operation("Bithumb", "subscribe_ticker"))
    }

    async fn subscribe_candles(&mut self, _symbol: &str, _timeframe: &str) -> Result<()> {
        Err(super::unsupported_operation("Bithumb", "subscribe_candles"))
    }

    async fn subscribe_orderbook(&mut self, _symbol: &str) -> Result<()> {
        Err(super::unsupported_operation(
            "Bithumb",
            "subscribe_orderbook",
        ))
    }

    async fn place_order(&self, _order: &Order) -> Result<String> {
        Err(super::unsupported_operation("Bithumb", "place_order"))
    }

    async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> Result<()> {
        Err(super::unsupported_operation("Bithumb", "cancel_order"))
    }

    async fn get_balance(&self) -> Result<HashMap<String, f64>> {
        Err(super::unsupported_operation("Bithumb", "get_balance"))
    }

    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_tx.subscribe()
    }
}
