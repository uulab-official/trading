use anyhow::Result;
use chrono::{DateTime, Utc};
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;
use std::collections::HashMap;
use tokio::net::TcpStream;
use tokio::sync::broadcast;
use tokio_tungstenite::{connect_async, tungstenite::Message, MaybeTlsStream, WebSocketStream};
use tracing::{error, info, warn};

use crate::core::models::{Candle, Order, OrderSide, OrderStatus, OrderType, Ticker};
use crate::exchange::{Exchange, ExchangeConfig, ExchangeEvent, OrderBook};

pub struct BinanceClient {
    config: ExchangeConfig,
    ws_stream: Option<WebSocketStream<MaybeTlsStream<TcpStream>>>,
    event_tx: broadcast::Sender<ExchangeEvent>,
}

impl BinanceClient {
    pub fn new(config: ExchangeConfig) -> Self {
        let (event_tx, _) = broadcast::channel(1000);
        Self {
            config,
            ws_stream: None,
            event_tx,
        }
    }

    fn get_ws_url(&self) -> String {
        if self.config.testnet {
            "wss://testnet.binance.vision/ws".to_string()
        } else {
            self.config.ws_url.clone()
        }
    }

    async fn handle_message(&self, msg: Message) -> Result<()> {
        match msg {
            Message::Text(text) => {
                let data: Value = serde_json::from_str(&text)?;
                
                if let Some(event_type) = data.get("e").and_then(|v| v.as_str()) {
                    match event_type {
                        "aggTrade" => self.parse_trade(&data).await?,
                        "kline" => self.parse_kline(&data).await?,
                        "ticker" | "24hrTicker" => self.parse_ticker(&data).await?,
                        "depthUpdate" => self.parse_orderbook(&data).await?,
                        _ => {}
                    }
                }
            }
            Message::Close(_) => {
                self.event_tx.send(ExchangeEvent::Disconnected)?;
            }
            _ => {}
        }
        Ok(())
    }

    async fn parse_trade(&self, data: &Value) -> Result<()> {
        let symbol = data.get("s").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let price = data.get("p").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let qty = data.get("q").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        
        info!("Trade: {} @ {} x {}", symbol, price, qty);
        Ok(())
    }

    async fn parse_kline(&self, data: &Value) -> Result<()> {
        if let Some(k) = data.get("k") {
            let symbol = k.get("s").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let open = k.get("o").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
            let high = k.get("h").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
            let low = k.get("l").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
            let close = k.get("c").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
            let volume = k.get("v").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
            let is_closed = k.get("x").and_then(|v| v.as_bool()).unwrap_or(false);
            
            if is_closed {
                let candle = Candle {
                    symbol: symbol.clone(),
                    timestamp: Utc::now(),
                    open,
                    high,
                    low,
                    close,
                    volume,
                };
                let _ = self.event_tx.send(ExchangeEvent::Candle(candle));
            }
        }
        Ok(())
    }

    async fn parse_ticker(&self, data: &Value) -> Result<()> {
        let symbol = data.get("s").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let bid = data.get("b").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let ask = data.get("a").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let last = data.get("c").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let volume = data.get("v").and_then(|v| v.as_str()).unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        
        let ticker = Ticker {
            symbol,
            bid,
            ask,
            last,
            volume,
            timestamp: Utc::now(),
        };
        
        let _ = self.event_tx.send(ExchangeEvent::Ticker(ticker));
        Ok(())
    }

    async fn parse_orderbook(&self, data: &Value) -> Result<()> {
        let symbol = data.get("s").and_then(|v| v.as_str()).unwrap_or("").to_string();
        
        let bids = data.get("b")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|item| {
                        if let Some(price) = item.get(0).and_then(|v| v.as_str()) {
                            if let Some(qty) = item.get(1).and_then(|v| v.as_str()) {
                                return Some((
                                    price.parse::<f64>().unwrap_or(0.0),
                                    qty.parse::<f64>().unwrap_or(0.0),
                                ));
                            }
                        }
                        None
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        
        let asks = data.get("a")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|item| {
                        if let Some(price) = item.get(0).and_then(|v| v.as_str()) {
                            if let Some(qty) = item.get(1).and_then(|v| v.as_str()) {
                                return Some((
                                    price.parse::<f64>().unwrap_or(0.0),
                                    qty.parse::<f64>().unwrap_or(0.0),
                                ));
                            }
                        }
                        None
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        
        let orderbook = OrderBook {
            symbol,
            bids,
            asks,
            timestamp: Utc::now(),
        };
        
        let _ = self.event_tx.send(ExchangeEvent::OrderBook(orderbook));
        Ok(())
    }

    fn generate_signature(&self, query_string: &str) -> String {
        type HmacSha256 = Hmac<Sha256>;
        let mut mac = HmacSha256::new_from_slice(self.config.api_secret.as_bytes())
            .expect("HMAC can take key of any size");
        mac.update(query_string.as_bytes());
        let result = mac.finalize();
        let code_bytes = result.into_bytes();
        hex::encode(code_bytes)
    }

    pub async fn run_message_loop(&mut self) -> Result<()> {
        info!("Starting Binance message loop");

        loop {
            let msg = {
                let ws_stream = self.ws_stream.as_mut().ok_or_else(|| {
                    anyhow::anyhow!("WebSocket not connected")
                })?;
                ws_stream.next().await
            };

            match msg {
                Some(Ok(msg)) => {
                    if let Err(e) = self.handle_message(msg).await {
                        error!("Error handling message: {}", e);
                    }
                }
                Some(Err(e)) => {
                    error!("WebSocket error: {}", e);
                    let _ = self.event_tx.send(ExchangeEvent::Error(e.to_string()));
                    break;
                }
                None => {
                    warn!("WebSocket stream ended");
                    break;
                }
            }
        }

        let _ = self.event_tx.send(ExchangeEvent::Disconnected);
        Ok(())
    }

    pub async fn start_background_loop(mut self) -> tokio::task::JoinHandle<Result<()>>
    where
        Self: 'static,
    {
        tokio::spawn(async move {
            self.run_message_loop().await
        })
    }
}

#[async_trait::async_trait]
impl Exchange for BinanceClient {
    async fn connect(&mut self) -> Result<()> {
        let ws_url = self.get_ws_url();
        info!("Connecting to Binance WebSocket: {}", ws_url);

        let (ws_stream, _) = connect_async(&ws_url).await?;
        self.ws_stream = Some(ws_stream);

        self.event_tx.send(ExchangeEvent::Connected)?;
        info!("Binance WebSocket connected");

        Ok(())
    }

    async fn disconnect(&mut self) -> Result<()> {
        if let Some(mut stream) = self.ws_stream.take() {
            let _ = stream.close(None).await;
        }
        self.event_tx.send(ExchangeEvent::Disconnected)?;
        info!("Binance WebSocket disconnected");
        Ok(())
    }

    async fn subscribe_ticker(&mut self, symbol: &str) -> Result<()> {
        let symbol_lower = symbol.to_lowercase().replace("/", "");
        let subscribe_msg = json!({
            "method": "SUBSCRIBE",
            "params": [format!("{}@ticker", symbol_lower)],
            "id": 1
        });
        
        if let Some(ref mut stream) = self.ws_stream {
            stream.send(Message::Text(subscribe_msg.to_string())).await?;
            info!("Subscribed to {} ticker", symbol);
        }
        
        Ok(())
    }

    async fn subscribe_candles(&mut self, symbol: &str, timeframe: &str) -> Result<()> {
        let symbol_lower = symbol.to_lowercase().replace("/", "");
        let interval = match timeframe {
            "1m" => "1m",
            "5m" => "5m",
            "15m" => "15m",
            "1h" => "1h",
            "4h" => "4h",
            "1d" => "1d",
            _ => "1h",
        };
        
        let subscribe_msg = json!({
            "method": "SUBSCRIBE",
            "params": [format!("{}@kline_{}", symbol_lower, interval)],
            "id": 2
        });
        
        if let Some(ref mut stream) = self.ws_stream {
            stream.send(Message::Text(subscribe_msg.to_string())).await?;
            info!("Subscribed to {} candles ({})", symbol, timeframe);
        }
        
        Ok(())
    }

    async fn subscribe_orderbook(&mut self, symbol: &str) -> Result<()> {
        let symbol_lower = symbol.to_lowercase().replace("/", "");
        let subscribe_msg = json!({
            "method": "SUBSCRIBE",
            "params": [format!("{}@depth", symbol_lower)],
            "id": 3
        });
        
        if let Some(ref mut stream) = self.ws_stream {
            stream.send(Message::Text(subscribe_msg.to_string())).await?;
            info!("Subscribed to {} orderbook", symbol);
        }
        
        Ok(())
    }

    async fn place_order(&self, order: &Order) -> Result<String> {
        info!("Placing order on Binance: {:?}", order);
        Ok(format!("binance-order-{}", Utc::now().timestamp()))
    }

    async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        info!("Cancelling order {} for {} on Binance", order_id, symbol);
        Ok(())
    }

    async fn get_balance(&self) -> Result<HashMap<String, f64>> {
        let mut balance = HashMap::new();
        balance.insert("USDT".to_string(), 10000.0);
        balance.insert("BTC".to_string(), 0.5);
        Ok(balance)
    }

    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_tx.subscribe()
    }
}