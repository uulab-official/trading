use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum OrderType {
    Market,
    Limit,
    StopLoss,
    TakeProfit,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum OrderStatus {
    Pending,
    Open,
    PartiallyFilled,
    Filled,
    Canceled,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Order {
    pub id: String,
    pub symbol: String,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: f64,
    pub price: Option<f64>,
    pub status: OrderStatus,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub exchange_order_id: Option<String>,
    pub filled_quantity: f64,
    pub average_price: Option<f64>,
    pub commission: Option<f64>,
    pub commission_asset: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub symbol: String,
    pub side: OrderSide,
    pub quantity: f64,
    pub entry_price: f64,
    pub current_price: f64,
    pub unrealized_pnl: f64,
    pub realized_pnl: f64,
    pub opened_at: DateTime<Utc>,
    pub leverage: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    pub id: String,
    pub order_id: String,
    pub symbol: String,
    pub side: OrderSide,
    pub quantity: f64,
    pub price: f64,
    pub timestamp: DateTime<Utc>,
    pub commission: f64,
    pub commission_asset: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ticker {
    pub symbol: String,
    pub bid: f64,
    pub ask: f64,
    pub last: f64,
    pub volume: f64,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candle {
    pub symbol: String,
    /// Binance UTC interval; legacy serialized candles default to an unusable empty value.
    #[serde(default)]
    pub timeframe: String,
    /// Exchange candle opening time, never the local receipt time.
    pub timestamp: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

pub fn is_supported_timeframe(timeframe: &str) -> bool {
    matches!(
        timeframe,
        "1s" | "1m"
            | "3m"
            | "5m"
            | "15m"
            | "30m"
            | "1h"
            | "2h"
            | "4h"
            | "6h"
            | "8h"
            | "12h"
            | "1d"
            | "3d"
            | "1w"
            | "1M"
    )
}

pub fn normalize_market_symbol(symbol: &str) -> anyhow::Result<String> {
    let parts: Vec<&str> = symbol.split('/').collect();
    if symbol.len() > 64
        || parts.len() > 2
        || parts
            .iter()
            .any(|part| part.is_empty() || !part.bytes().all(|c| c.is_ascii_alphanumeric()))
    {
        anyhow::bail!("Invalid market symbol");
    }
    Ok(parts.concat().to_ascii_uppercase())
}

impl Candle {
    pub fn validate_closed(&self) -> anyhow::Result<()> {
        if normalize_market_symbol(&self.symbol)? != self.symbol
            || !is_supported_timeframe(&self.timeframe)
            || self.timestamp.timestamp_millis() < 0
        {
            anyhow::bail!("Invalid closed candle identity");
        }
        if [self.open, self.high, self.low, self.close]
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || !self.volume.is_finite()
            || self.volume < 0.0
            || self.low > self.open.min(self.close)
            || self.high < self.open.max(self.close)
        {
            anyhow::bail!("Invalid closed candle values");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Portfolio {
    pub total_balance: f64,
    pub available_balance: f64,
    pub positions: Vec<Position>,
    pub timestamp: DateTime<Utc>,
}
