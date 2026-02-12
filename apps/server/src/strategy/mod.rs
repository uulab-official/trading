use crate::core::models::{Candle, OrderSide};

pub mod ma_cross;
pub mod rsi;

#[derive(Debug, Clone)]
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

#[derive(Clone)]
pub struct StrategyConfig {
    pub name: String,
    pub symbol: String,
    pub timeframe: String,
    pub params: serde_json::Map<String, serde_json::Value>,
}