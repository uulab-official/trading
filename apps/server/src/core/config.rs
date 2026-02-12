use serde::{Deserialize, Serialize};
use std::collections::HashMap;

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
        let config: Config = toml::from_str(&content)?;
        Ok(config)
    }
    
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.strategies.is_empty() {
            return Err(anyhow::anyhow!("At least one strategy must be configured"));
        }
        
        if self.exchange.api_key.is_none() || self.exchange.api_secret.is_none() {
            return Err(anyhow::anyhow!("Exchange API credentials are required"));
        }
        
        Ok(())
    }
}