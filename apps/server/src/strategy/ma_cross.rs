use crate::core::models::Candle;
use crate::strategy::{Signal, Strategy};
use serde_json::json;

pub struct MaCrossStrategy {
    name: String,
    fast_period: usize,
    slow_period: usize,
    prices: Vec<f64>,
    last_signal: Signal,
}

impl MaCrossStrategy {
    pub fn new(name: String, fast_period: usize, slow_period: usize) -> Self {
        Self {
            name,
            fast_period,
            slow_period,
            prices: Vec::with_capacity(slow_period + 1),
            last_signal: Signal::Hold,
        }
    }

    fn calculate_sma(&self, period: usize) -> Option<f64> {
        if self.prices.len() < period {
            return None;
        }

        let sum: f64 = self.prices.iter().rev().take(period).sum();
        Some(sum / period as f64)
    }
}

impl Strategy for MaCrossStrategy {
    fn name(&self) -> &str {
        &self.name
    }

    fn on_candle(&mut self, candle: &Candle) -> Signal {
        self.prices.push(candle.close);

        if self.prices.len() > self.slow_period * 2 {
            self.prices.remove(0);
        }

        let fast_ma = self.calculate_sma(self.fast_period);
        let slow_ma = self.calculate_sma(self.slow_period);

        if let (Some(fast), Some(slow)) = (fast_ma, slow_ma) {
            let signal = if fast > slow {
                Signal::Buy
            } else if fast < slow {
                Signal::Sell
            } else {
                Signal::Hold
            };

            let prev_signal = self.last_signal.clone();
            self.last_signal = signal.clone();

            // Only return signal on crossover
            return match (prev_signal, signal) {
                (Signal::Hold | Signal::Sell, Signal::Buy) => Signal::Buy,
                (Signal::Hold | Signal::Buy, Signal::Sell) => Signal::Sell,
                _ => Signal::Hold,
            };
        }

        Signal::Hold
    }

    fn get_parameters(&self) -> serde_json::Value {
        let fast_ma = self.calculate_sma(self.fast_period);
        let slow_ma = self.calculate_sma(self.slow_period);

        json!({
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
        })
    }
}
