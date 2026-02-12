use crate::core::models::{Candle, OrderSide};
use crate::strategy::{Signal, Strategy};
use serde_json::json;

pub struct RsiStrategy {
    name: String,
    period: usize,
    oversold: f64,
    overbought: f64,
    prices: Vec<f64>,
    last_rsi: Option<f64>,
}

impl RsiStrategy {
    pub fn new(name: String, period: usize, oversold: f64, overbought: f64) -> Self {
        Self {
            name,
            period,
            oversold,
            overbought,
            prices: Vec::with_capacity(period + 1),
            last_rsi: None,
        }
    }

    fn calculate_rsi(&self) -> Option<f64> {
        if self.prices.len() < self.period + 1 {
            return None;
        }

        let mut gains = 0.0;
        let mut losses = 0.0;

        for i in 1..=self.period {
            let change =
                self.prices[self.prices.len() - i] - self.prices[self.prices.len() - i - 1];
            if change > 0.0 {
                gains += change;
            } else {
                losses += -change;
            }
        }

        if losses == 0.0 {
            return Some(100.0);
        }

        let rs = gains / losses;
        let rsi = 100.0 - (100.0 / (1.0 + rs));
        Some(rsi)
    }
}

impl Strategy for RsiStrategy {
    fn name(&self) -> &str {
        &self.name
    }

    fn on_candle(&mut self, candle: &Candle) -> Signal {
        self.prices.push(candle.close);

        if self.prices.len() > self.period * 2 {
            self.prices.remove(0);
        }

        if let Some(rsi) = self.calculate_rsi() {
            self.last_rsi = Some(rsi);

            if rsi < self.oversold {
                return Signal::Buy;
            } else if rsi > self.overbought {
                return Signal::Sell;
            }
        }

        Signal::Hold
    }

    fn get_parameters(&self) -> serde_json::Value {
        json!({
            "period": self.period,
            "oversold": self.oversold,
            "overbought": self.overbought,
            "last_rsi": self.last_rsi,
        })
    }
}
