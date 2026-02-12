use thiserror::Error;

#[derive(Error, Debug)]
pub enum TradingError {
    #[error("Configuration error: {0}")]
    Config(String),
    
    #[error("Exchange connection error: {0}")]
    ExchangeConnection(String),
    
    #[error("Order error: {0}")]
    Order(String),
    
    #[error("Risk limit exceeded: {0}")]
    RiskLimit(String),
    
    #[error("Database error: {0}")]
    Database(String),
    
    #[error("WebSocket error: {0}")]
    WebSocket(String),
    
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, TradingError>;