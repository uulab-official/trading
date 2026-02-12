use trading_server::core::config::{Config, BotConfig, TradingMode, ExchangeConfig, TelegramConfig, NotificationConfig, RiskConfig, WebConfig};
use trading_server::core::models::{Order, OrderSide, OrderType, OrderStatus};
use chrono::Utc;

#[tokio::test]
async fn test_config_loading() {
    let config = Config {
        bot: BotConfig {
            id: "test".to_string(),
            name: "Test Bot".to_string(),
            mode: TradingMode::Paper,
            timezone: "Asia/Seoul".to_string(),
        },
        exchange: ExchangeConfig {
            id: "binance".to_string(),
            sandbox: true,
            testnet: true,
            api_key: Some("test".to_string()),
            api_secret: Some("test".to_string()),
            ws_url: "wss://testnet.binance.vision/ws".to_string(),
            rest_url: "https://testnet.binance.vision".to_string(),
        },
        telegram: TelegramConfig {
            enabled: false,
            bot_token: None,
            chat_id: None,
            notifications: NotificationConfig {
                trade: true,
                error: true,
                daily_report: false,
                position: false,
            },
        },
        risk: RiskConfig {
            max_daily_loss_pct: 2.0,
            max_positions: 2,
            risk_per_trade_pct: 0.5,
            max_leverage: 1.0,
            circuit_breaker_threshold: 5.0,
        },
        strategies: vec![],
        web: WebConfig {
            enabled: true,
            port: 8080,
            host: "0.0.0.0".to_string(),
            ws_update_interval_ms: 1000,
        },
    };

    assert_eq!(config.bot.id, "test");
    assert!(matches!(config.bot.mode, TradingMode::Paper));
}

#[test]
fn test_order_creation() {
    let order = Order {
        id: "test-123".to_string(),
        symbol: "BTC/USDT".to_string(),
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity: 0.1,
        price: None,
        status: OrderStatus::Pending,
        created_at: Utc::now(),
        updated_at: Utc::now(),
        exchange_order_id: None,
        filled_quantity: 0.0,
        average_price: None,
        commission: None,
        commission_asset: None,
    };

    assert_eq!(order.symbol, "BTC/USDT");
    assert!(matches!(order.side, OrderSide::Buy));
}