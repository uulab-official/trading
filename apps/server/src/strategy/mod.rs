use crate::core::models::{is_supported_timeframe, normalize_market_symbol, Candle};
use anyhow::Result;
use chrono::{DateTime, Utc};

pub mod ma_cross;
pub mod rsi;

#[derive(Debug, Clone, PartialEq)]
pub enum Signal {
    Buy,
    Sell,
    Hold,
}

pub trait Strategy: Send + Sync {
    fn name(&self) -> &str;
    fn on_candle(&mut self, candle: &Candle) -> Signal;
    fn get_parameters(&self) -> serde_json::Value;
}

/// Each strategy owns an independent closed-bar cursor for exactly one market stream.
pub struct StrategyBinding {
    symbol: String,
    timeframe: String,
    strategy: Box<dyn Strategy>,
    last_closed_at: Option<DateTime<Utc>>,
}

impl StrategyBinding {
    pub fn new(symbol: &str, timeframe: &str, strategy: Box<dyn Strategy>) -> Result<Self> {
        let symbol = normalize_market_symbol(symbol)?;
        if !is_supported_timeframe(timeframe) {
            anyhow::bail!("Unsupported strategy timeframe");
        }
        Ok(Self {
            symbol,
            timeframe: timeframe.into(),
            strategy,
            last_closed_at: None,
        })
    }

    pub fn name(&self) -> &str {
        self.strategy.name()
    }

    pub fn matches(&self, candle: &Candle) -> bool {
        self.symbol == candle.symbol && self.timeframe == candle.timeframe
    }

    pub fn on_candle(&mut self, candle: &Candle) -> Result<Option<Signal>> {
        if !self.matches(candle) {
            return Ok(None);
        }
        candle.validate_closed()?;
        if self
            .last_closed_at
            .is_some_and(|last| candle.timestamp <= last)
        {
            return Ok(None);
        }
        let signal = self.strategy.on_candle(candle);
        self.last_closed_at = Some(candle.timestamp);
        Ok(Some(signal))
    }
}

#[derive(Clone)]
pub struct StrategyConfig {
    pub name: String,
    pub symbol: String,
    pub timeframe: String,
    pub params: serde_json::Map<String, serde_json::Value>,
}
