use anyhow::{anyhow, ensure, Result};
use chrono::{DateTime, Datelike, Months, Timelike, Utc};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::collections::HashMap;
use tokio::net::TcpStream;
use tokio::sync::broadcast;
use tokio::time::{timeout, Duration};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{protocol::WebSocketConfig, Message},
    MaybeTlsStream, WebSocketStream,
};
use tracing::info;

use crate::core::models::{is_supported_timeframe, normalize_market_symbol, Candle, Order, Ticker};
use crate::exchange::{Exchange, ExchangeConfig, ExchangeEvent, OrderBook};

const MAX_MESSAGE_BYTES: usize = 1_048_576;
const TRANSPORT_TIMEOUT: Duration = Duration::from_secs(10);

fn invalid_message() -> anyhow::Error {
    anyhow!("Invalid Binance public-feed message")
}

fn required_string<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(invalid_message)
}

fn source_symbol(value: &Value) -> Result<&str> {
    let symbol = required_string(value, "s")?;
    let normalized = normalize_market_symbol(symbol).map_err(|_| invalid_message())?;
    ensure!(normalized == symbol, "Invalid Binance public-feed message");
    Ok(symbol)
}

fn source_time(value: &Value, key: &str) -> Result<DateTime<Utc>> {
    let millis = value
        .get(key)
        .and_then(Value::as_i64)
        .ok_or_else(invalid_message)?;
    ensure!(millis >= 0, "Invalid Binance public-feed message");
    DateTime::from_timestamp_millis(millis).ok_or_else(invalid_message)
}

fn number(value: Option<&Value>, positive: bool) -> Result<f64> {
    let text = value.and_then(Value::as_str).ok_or_else(invalid_message)?;
    ensure!(
        !text.is_empty() && text.len() <= 64,
        "Invalid Binance public-feed message"
    );
    let parsed = text.parse::<f64>().map_err(|_| invalid_message())?;
    ensure!(
        parsed.is_finite()
            && if positive {
                parsed > 0.0
            } else {
                parsed >= 0.0
            },
        "Invalid Binance public-feed message"
    );
    Ok(parsed)
}

fn expected_close(open: DateTime<Utc>, timeframe: &str) -> Result<i64> {
    let millis = match timeframe {
        "1s" => 1_000,
        "1m" => 60_000,
        "3m" => 180_000,
        "5m" => 300_000,
        "15m" => 900_000,
        "30m" => 1_800_000,
        "1h" => 3_600_000,
        "2h" => 7_200_000,
        "4h" => 14_400_000,
        "6h" => 21_600_000,
        "8h" => 28_800_000,
        "12h" => 43_200_000,
        "1d" => 86_400_000,
        "3d" => 259_200_000,
        "1w" => 604_800_000,
        "1M" => {
            ensure!(
                open.day() == 1 && open.num_seconds_from_midnight() == 0 && open.nanosecond() == 0,
                "Invalid Binance public-feed message"
            );
            return open
                .checked_add_months(Months::new(1))
                .and_then(|next| next.timestamp_millis().checked_sub(1))
                .ok_or_else(invalid_message);
        }
        _ => return Err(invalid_message()),
    };
    open.timestamp_millis()
        .checked_add(millis)
        .and_then(|next| next.checked_sub(1))
        .ok_or_else(invalid_message)
}

fn depth_levels(value: Option<&Value>) -> Result<Vec<(f64, f64)>> {
    let levels = value
        .and_then(Value::as_array)
        .ok_or_else(invalid_message)?;
    ensure!(levels.len() <= 5_000, "Invalid Binance public-feed message");
    levels
        .iter()
        .map(|level| {
            let pair = level.as_array().ok_or_else(invalid_message)?;
            ensure!(pair.len() == 2, "Invalid Binance public-feed message");
            // A zero quantity removes a level in Binance's diff-depth stream.
            Ok((number(pair.first(), true)?, number(pair.get(1), false)?))
        })
        .collect()
}

pub struct BinanceClient {
    config: ExchangeConfig,
    ws_stream: Option<WebSocketStream<MaybeTlsStream<TcpStream>>>,
    event_tx: broadcast::Sender<ExchangeEvent>,
    // Keep requested subscriptions after disconnect for an explicit reconnect.
    pending_streams: Vec<String>,
    next_request_id: u64,
    is_connected: bool,
}

impl BinanceClient {
    pub fn new(config: ExchangeConfig) -> Self {
        let (event_tx, _) = broadcast::channel(1000);
        Self {
            config,
            ws_stream: None,
            event_tx,
            pending_streams: Vec::new(),
            next_request_id: 1,
            is_connected: false,
        }
    }

    fn get_ws_base_url(&self) -> String {
        if self.config.testnet {
            "wss://testnet.binance.vision".to_string()
        } else {
            let base = self.config.ws_url.trim_end_matches('/');
            base.strip_suffix("/ws").unwrap_or(base).to_string()
        }
    }

    fn build_stream_url(&self) -> String {
        let base = self.get_ws_base_url();
        if self.pending_streams.is_empty() {
            format!("{base}/ws")
        } else {
            format!("{base}/stream?streams={}", self.pending_streams.join("/"))
        }
    }

    fn validate_endpoint(&self) -> Result<()> {
        let base = self.get_ws_base_url();
        let parsed = reqwest::Url::parse(&base)
            .map_err(|_| anyhow!("Invalid Binance public-feed endpoint"))?;
        ensure!(
            matches!(parsed.scheme(), "ws" | "wss")
                && parsed.host_str().is_some()
                && parsed.username().is_empty()
                && parsed.password().is_none()
                && parsed.query().is_none()
                && parsed.fragment().is_none()
                && matches!(parsed.path(), "" | "/"),
            "Invalid Binance public-feed endpoint"
        );
        Ok(())
    }

    async fn handle_message(&self, msg: Message) -> Result<()> {
        let Message::Text(text) = msg else {
            return match msg {
                Message::Ping(_) | Message::Pong(_) | Message::Close(_) => Ok(()),
                _ => Err(invalid_message()),
            };
        };
        ensure!(
            text.len() <= MAX_MESSAGE_BYTES,
            "Invalid Binance public-feed message"
        );
        let envelope: Value = serde_json::from_str(&text).map_err(|_| invalid_message())?;
        ensure!(envelope.is_object(), "Invalid Binance public-feed message");
        let (data, stream) = if envelope.get("stream").is_some() || envelope.get("data").is_some() {
            let stream = required_string(&envelope, "stream")?;
            ensure!(stream.len() <= 100, "Invalid Binance public-feed message");
            let data = envelope
                .get("data")
                .filter(|item| item.is_object())
                .ok_or_else(invalid_message)?;
            (data, Some(stream))
        } else {
            (&envelope, None)
        };
        if data.get("code").is_some() || data.get("msg").is_some() {
            return Err(anyhow!("Binance public-feed control request was rejected"));
        }
        if data.get("result").is_some() {
            ensure!(
                stream.is_none()
                    && data.get("result").is_some_and(Value::is_null)
                    && data.get("id").and_then(Value::as_u64).is_some(),
                "Invalid Binance public-feed message"
            );
            return Ok(());
        }
        let event_type = required_string(data, "e")?;
        if let Some(stream) = stream {
            let expected = match event_type {
                "kline" => {
                    let candle = data.get("k").ok_or_else(invalid_message)?;
                    format!(
                        "{}@kline_{}",
                        source_symbol(data)?.to_ascii_lowercase(),
                        required_string(candle, "i")?
                    )
                }
                "ticker" | "24hrTicker" => {
                    format!("{}@ticker", source_symbol(data)?.to_ascii_lowercase())
                }
                "depthUpdate" => format!("{}@depth", source_symbol(data)?.to_ascii_lowercase()),
                // This adapter does not subscribe to other event types.
                _ => return Ok(()),
            };
            ensure!(stream == expected, "Invalid Binance public-feed message");
        }
        match event_type {
            "kline" => self.parse_kline(data).await,
            "ticker" | "24hrTicker" => self.parse_ticker(data).await,
            "depthUpdate" => self.parse_orderbook(data).await,
            _ => Ok(()),
        }
    }

    async fn parse_kline(&self, data: &Value) -> Result<()> {
        let symbol = source_symbol(data)?;
        let observed = source_time(data, "E")?;
        let k = data
            .get("k")
            .filter(|item| item.is_object())
            .ok_or_else(invalid_message)?;
        ensure!(
            source_symbol(k)? == symbol,
            "Invalid Binance public-feed message"
        );
        let timeframe = required_string(k, "i")?;
        ensure!(
            is_supported_timeframe(timeframe),
            "Invalid Binance public-feed message"
        );
        let open = source_time(k, "t")?;
        let close = source_time(k, "T")?;
        let closed = k
            .get("x")
            .and_then(Value::as_bool)
            .ok_or_else(invalid_message)?;
        ensure!(
            close.timestamp_millis() == expected_close(open, timeframe)?
                && observed >= open
                && (!closed || observed >= close),
            "Invalid Binance public-feed message"
        );
        let candle = Candle {
            symbol: symbol.to_string(),
            timeframe: timeframe.to_string(),
            timestamp: open,
            open: number(k.get("o"), true)?,
            high: number(k.get("h"), true)?,
            low: number(k.get("l"), true)?,
            close: number(k.get("c"), true)?,
            volume: number(k.get("v"), false)?,
        };
        candle.validate_closed().map_err(|_| invalid_message())?;
        if closed {
            let _ = self.event_tx.send(ExchangeEvent::Candle(candle));
        }
        Ok(())
    }

    async fn parse_ticker(&self, data: &Value) -> Result<()> {
        let ticker = Ticker {
            symbol: source_symbol(data)?.to_string(),
            bid: number(data.get("b"), true)?,
            ask: number(data.get("a"), true)?,
            last: number(data.get("c"), true)?,
            volume: number(data.get("v"), false)?,
            timestamp: source_time(data, "E")?,
        };
        ensure!(
            ticker.bid <= ticker.ask,
            "Invalid Binance public-feed message"
        );
        let _ = self.event_tx.send(ExchangeEvent::Ticker(ticker));
        Ok(())
    }

    async fn parse_orderbook(&self, data: &Value) -> Result<()> {
        let first = data
            .get("U")
            .and_then(Value::as_u64)
            .ok_or_else(invalid_message)?;
        let last = data
            .get("u")
            .and_then(Value::as_u64)
            .ok_or_else(invalid_message)?;
        ensure!(
            first > 0 && last >= first,
            "Invalid Binance public-feed message"
        );
        let book = OrderBook {
            symbol: source_symbol(data)?.to_string(),
            bids: depth_levels(data.get("b"))?,
            asks: depth_levels(data.get("a"))?,
            timestamp: source_time(data, "E")?,
        };
        // This is a validated diff-depth event, not a reconstructed depth snapshot.
        let _ = self.event_tx.send(ExchangeEvent::OrderBook(book));
        Ok(())
    }

    fn clear_connection(&mut self) {
        let was_connected = self.is_connected || self.ws_stream.is_some();
        self.ws_stream = None;
        self.is_connected = false;
        if was_connected {
            let _ = self.event_tx.send(ExchangeEvent::Disconnected);
        }
    }

    async fn add_subscription(&mut self, stream_name: String) -> Result<()> {
        if self.pending_streams.contains(&stream_name) {
            return Ok(());
        }
        let request_id = self.next_request_id;
        self.next_request_id = request_id
            .checked_add(1)
            .ok_or_else(|| anyhow!("Binance subscription identifier exhausted"))?;
        // Preserve the requested subscription even if the connection fails while sending it.
        self.pending_streams.push(stream_name.clone());
        if !self.is_connected {
            return Ok(());
        }
        let request = json!({"method":"SUBSCRIBE", "params":[stream_name], "id":request_id});
        let result = match self.ws_stream.as_mut() {
            Some(socket) => matches!(
                timeout(
                    TRANSPORT_TIMEOUT,
                    socket.send(Message::Text(request.to_string()))
                )
                .await,
                Ok(Ok(()))
            ),
            None => false,
        };
        if !result {
            self.clear_connection();
            return Err(anyhow!("Binance public-feed subscription transport failed"));
        }
        Ok(())
    }

    async fn receive_messages(&mut self) -> Result<()> {
        loop {
            let next = self
                .ws_stream
                .as_mut()
                .ok_or_else(|| anyhow!("Binance public-feed transport is disconnected"))?
                .next()
                .await;
            match next {
                Some(Ok(Message::Close(_))) => {
                    // Tungstenite queued the close reply while reading this frame.
                    if let Some(socket) = self.ws_stream.as_mut() {
                        let _ = timeout(TRANSPORT_TIMEOUT, socket.flush()).await;
                    }
                    return Ok(());
                }
                Some(Ok(Message::Ping(_))) => {
                    // Flush tungstenite's automatic pong before waiting for another frame.
                    let socket = self
                        .ws_stream
                        .as_mut()
                        .ok_or_else(|| anyhow!("Binance public-feed transport is disconnected"))?;
                    ensure!(
                        matches!(timeout(TRANSPORT_TIMEOUT, socket.flush()).await, Ok(Ok(()))),
                        "Binance public-feed heartbeat transport failed"
                    );
                }
                Some(Ok(Message::Pong(_))) => {}
                Some(Ok(message)) => self.handle_message(message).await?,
                Some(Err(_)) => return Err(anyhow!("Binance public-feed transport failed")),
                None => return Ok(()),
            }
        }
    }

    pub async fn run_message_loop(&mut self) -> Result<()> {
        let outcome = self.receive_messages().await;
        if outcome.is_err() {
            let _ = self.event_tx.send(ExchangeEvent::Error(
                "Binance public-feed processing stopped".to_string(),
            ));
        }
        self.clear_connection();
        outcome
    }

    pub async fn start_background_loop(mut self) -> tokio::task::JoinHandle<Result<()>>
    where
        Self: 'static,
    {
        tokio::spawn(async move { self.run_message_loop().await })
    }
}

#[async_trait::async_trait]
impl Exchange for BinanceClient {
    async fn connect(&mut self) -> Result<()> {
        if self.is_connected && self.ws_stream.is_some() {
            return Ok(());
        }
        self.clear_connection();
        self.validate_endpoint()?;
        ensure!(
            !self.pending_streams.is_empty(),
            "At least one Binance public-feed subscription is required"
        );
        let options = WebSocketConfig {
            max_message_size: Some(MAX_MESSAGE_BYTES),
            max_frame_size: Some(MAX_MESSAGE_BYTES),
            ..WebSocketConfig::default()
        };
        let connection = timeout(
            TRANSPORT_TIMEOUT,
            connect_async_with_config(self.build_stream_url(), Some(options), false),
        )
        .await;
        let (socket, _) = match connection {
            Ok(Ok(connected)) => connected,
            // Discard endpoint/handshake diagnostics, which can quote untrusted source data.
            _ => return Err(anyhow!("Binance public-feed connection failed")),
        };
        self.ws_stream = Some(socket);
        self.is_connected = true;
        // Local listener lifetimes are independent from the remote connection.
        let _ = self.event_tx.send(ExchangeEvent::Connected);
        info!("Binance public-feed transport connected");
        Ok(())
    }

    async fn disconnect(&mut self) -> Result<()> {
        let result = match self.ws_stream.as_mut() {
            Some(socket) => matches!(
                timeout(TRANSPORT_TIMEOUT, socket.close(None)).await,
                Ok(Ok(()))
            ),
            None => true,
        };
        self.clear_connection();
        ensure!(result, "Binance public-feed close transport failed");
        Ok(())
    }

    async fn subscribe_ticker(&mut self, symbol: &str) -> Result<()> {
        let normalized = normalize_market_symbol(symbol)
            .map_err(|_| anyhow!("Invalid Binance subscription symbol"))?;
        self.add_subscription(format!("{}@ticker", normalized.to_ascii_lowercase()))
            .await
    }

    async fn subscribe_candles(&mut self, symbol: &str, timeframe: &str) -> Result<()> {
        let normalized = normalize_market_symbol(symbol)
            .map_err(|_| anyhow!("Invalid Binance subscription symbol"))?;
        ensure!(
            is_supported_timeframe(timeframe),
            "Unsupported Binance candle timeframe"
        );
        self.add_subscription(format!(
            "{}@kline_{timeframe}",
            normalized.to_ascii_lowercase()
        ))
        .await
    }

    async fn subscribe_orderbook(&mut self, symbol: &str) -> Result<()> {
        let normalized = normalize_market_symbol(symbol)
            .map_err(|_| anyhow!("Invalid Binance subscription symbol"))?;
        self.add_subscription(format!("{}@depth", normalized.to_ascii_lowercase()))
            .await
    }

    async fn place_order(&self, _order: &Order) -> Result<String> {
        Err(super::unsupported_operation("Binance", "place_order"))
    }

    async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> Result<()> {
        Err(super::unsupported_operation("Binance", "cancel_order"))
    }

    async fn get_balance(&self) -> Result<HashMap<String, f64>> {
        Err(super::unsupported_operation("Binance", "get_balance"))
    }

    fn get_event_receiver(&self) -> broadcast::Receiver<ExchangeEvent> {
        self.event_tx.subscribe()
    }
}

#[cfg(test)]
#[path = "binance_tests.rs"]
mod tests;
