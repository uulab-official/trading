use crate::core::models::{is_supported_timeframe, normalize_market_symbol};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub bot: BotConfig,
    pub exchange: ExchangeConfig,
    pub telegram: TelegramConfig,
    pub risk: RiskConfig,
    pub strategies: Vec<StrategyConfig>,
    pub web: WebConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BotConfig {
    pub id: String,
    pub name: String,
    pub mode: TradingMode,
    pub timezone: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TradingMode {
    Paper,
    Live,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExchangeConfig {
    pub id: String,
    pub sandbox: bool,
    pub testnet: bool,
    pub api_key: Option<String>,
    pub api_secret: Option<String>,
    pub ws_url: String,
    pub rest_url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TelegramConfig {
    pub enabled: bool,
    pub bot_token: Option<String>,
    pub chat_id: Option<String>,
    pub notifications: NotificationConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NotificationConfig {
    pub trade: bool,
    pub error: bool,
    pub daily_report: bool,
    pub position: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskConfig {
    pub max_daily_loss_pct: f64,
    pub max_positions: usize,
    pub risk_per_trade_pct: f64,
    pub max_leverage: f64,
    pub circuit_breaker_threshold: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyConfig {
    pub name: String,
    pub enabled: bool,
    pub strategy_type: String,
    pub symbol: String,
    pub timeframe: String,
    pub params: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebConfig {
    pub enabled: bool,
    pub port: u16,
    pub host: String,
    pub ws_update_interval_ms: u64,
}

impl Config {
    pub fn from_file(path: &str) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        // TOML errors render source excerpts, which can contain credentials.
        // Discard the parser error rather than retaining it as an error cause.
        let config: Config = toml::from_str(&content).map_err(|_| {
            anyhow::anyhow!("Invalid TOML configuration; check the local config file")
        })?;
        Ok(config)
    }

    pub fn validate(&self) -> anyhow::Result<()> {
        if matches!(self.bot.mode, TradingMode::Live) {
            return Err(anyhow::anyhow!(
                "Live trading is unavailable: Rust broker execution and risk controls are not validated"
            ));
        }

        if self.strategies.is_empty() {
            return Err(anyhow::anyhow!("At least one strategy must be configured"));
        }

        if self.exchange.id != "binance" {
            anyhow::bail!("Only the Binance public market observation adapter is implemented");
        }
        let mut names = HashSet::new();
        for strategy in self.strategies.iter().filter(|strategy| strategy.enabled) {
            if strategy.name.is_empty()
                || strategy.name.len() > 256
                || strategy.name.chars().any(char::is_control)
                || !names.insert(&strategy.name)
            {
                anyhow::bail!("Enabled strategy names must be unique, 1 to 256 bytes, and contain no control characters");
            }
            normalize_market_symbol(&strategy.symbol)?;
            if !is_supported_timeframe(&strategy.timeframe) {
                anyhow::bail!("Unsupported strategy timeframe");
            }
            let period = |name: &str, default: u64| -> anyhow::Result<u64> {
                let value = match strategy.params.get(name) {
                    Some(value) => value.as_u64().ok_or_else(|| {
                        anyhow::anyhow!("Strategy periods must be positive integers")
                    })?,
                    None => default,
                };
                if !(1..=10_000).contains(&value) {
                    anyhow::bail!("Strategy periods must be between 1 and 10000");
                }
                Ok(value)
            };
            match strategy.strategy_type.as_str() {
                "ma_cross" => {
                    if period("fast_period", 10)? >= period("slow_period", 20)? {
                        anyhow::bail!("MA fast period must be smaller than slow period");
                    }
                }
                "rsi" => {
                    period("period", 14)?;
                    let threshold = |name: &str, default: f64| -> anyhow::Result<f64> {
                        let value = match strategy.params.get(name) {
                            Some(value) => value
                                .as_f64()
                                .ok_or_else(|| anyhow::anyhow!("RSI thresholds must be numeric"))?,
                            None => default,
                        };
                        if !value.is_finite() || !(0.0..=100.0).contains(&value) {
                            anyhow::bail!("RSI thresholds must be finite percentages");
                        }
                        Ok(value)
                    };
                    if threshold("oversold", 30.0)? >= threshold("overbought", 70.0)? {
                        anyhow::bail!("RSI oversold must be smaller than overbought");
                    }
                }
                _ => anyhow::bail!("Unsupported enabled strategy type"),
            }
        }
        if names.is_empty() {
            anyhow::bail!("At least one strategy must be enabled");
        }

        // Paper configuration does not authenticate or enable broker execution.
        Ok(())
    }
}
