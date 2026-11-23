use chrono::Utc;
use std::path::PathBuf;
use std::process::Command;
use tokio::sync::broadcast::error::TryRecvError;
use trading_server::core::config::{Config, TradingMode};
use trading_server::core::models::{Order, OrderSide, OrderStatus, OrderType};
use trading_server::exchange::binance::BinanceClient;
use trading_server::exchange::bithumb::BithumbClient;
use trading_server::exchange::{Exchange, ExchangeConfig, ExchangeManager};
use uuid::Uuid;

const KEY_SENTINEL: &str = "synthetic-key-must-not-appear-in-errors";
const SECRET_SENTINEL: &str = "synthetic-secret-must-not-appear-in-errors";
const ORDER_SENTINEL: &str = "synthetic-order-body-must-not-appear-in-errors";

fn adapter_config() -> ExchangeConfig {
    ExchangeConfig {
        id: "synthetic".into(),
        api_key: KEY_SENTINEL.into(),
        api_secret: SECRET_SENTINEL.into(),
        ws_url: "ws://127.0.0.1:1/never-connect".into(),
        rest_url: "http://127.0.0.1:1/never-request".into(),
        testnet: true,
    }
}

fn pending_order() -> Order {
    let now = Utc::now();
    Order {
        id: ORDER_SENTINEL.into(),
        symbol: "BTC/USDT".into(),
        side: OrderSide::Buy,
        order_type: OrderType::Limit,
        quantity: 1.0,
        price: Some(100.0),
        status: OrderStatus::Pending,
        created_at: now,
        updated_at: now,
        exchange_order_id: None,
        filled_quantity: 0.0,
        average_price: None,
        commission: None,
        commission_asset: None,
    }
}

fn assert_unsupported<T>(result: anyhow::Result<T>, operation: &str) {
    let error = match result {
        Ok(_) => panic!("Unimplemented {operation} returned fabricated success"),
        Err(error) => format!("{error:#}"),
    };
    assert!(error.contains("not implemented"), "{error}");
    assert!(error.contains(operation), "{error}");
    assert!(!error.contains(KEY_SENTINEL));
    assert!(!error.contains(SECRET_SENTINEL));
    assert!(!error.contains(ORDER_SENTINEL));
}

async fn assert_private_operations_refused(exchange: &dyn Exchange) {
    let mut events = exchange.get_event_receiver();
    assert_unsupported(exchange.place_order(&pending_order()).await, "place_order");
    assert_unsupported(
        exchange.cancel_order("BTC/USDT", ORDER_SENTINEL).await,
        "cancel_order",
    );
    assert_unsupported(exchange.get_balance().await, "get_balance");
    assert!(matches!(events.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn binance_private_operations_refuse_invented_account_evidence() {
    let exchange = BinanceClient::new(adapter_config());
    assert_private_operations_refused(&exchange).await;
}

#[tokio::test]
async fn bithumb_private_operations_refuse_invented_account_evidence() {
    let exchange = BithumbClient::new(adapter_config());
    assert_private_operations_refused(&exchange).await;
}

#[tokio::test]
async fn manager_proxy_cannot_fabricate_broker_success() {
    let manager = ExchangeManager::new();
    manager
        .add_exchange(
            "stub".into(),
            Box::new(BithumbClient::new(adapter_config())),
        )
        .await
        .unwrap();
    let exchange = manager.get_exchange("stub").await.unwrap();
    assert_private_operations_refused(exchange.as_ref()).await;
}

async fn assert_passive_adapter_refused(exchange: &mut dyn Exchange) {
    // Keeping a receiver alive catches the previous false Connected event.
    let mut events = exchange.get_event_receiver();
    assert_unsupported(exchange.connect().await, "connect");
    assert_unsupported(
        exchange.subscribe_ticker("BTC/USDT").await,
        "subscribe_ticker",
    );
    assert_unsupported(
        exchange.subscribe_candles("BTC/USDT", "1m").await,
        "subscribe_candles",
    );
    assert_unsupported(
        exchange.subscribe_orderbook("BTC/USDT").await,
        "subscribe_orderbook",
    );
    assert_unsupported(exchange.disconnect().await, "disconnect");
    assert!(matches!(events.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn bithumb_does_not_claim_an_unimplemented_feed_connection() {
    let mut exchange = BithumbClient::new(adapter_config());
    assert_passive_adapter_refused(&mut exchange).await;
}

#[tokio::test]
async fn passive_manager_proxy_does_not_claim_active_transport_operations() {
    let manager = ExchangeManager::new();
    manager
        .add_exchange(
            "stub".into(),
            Box::new(BithumbClient::new(adapter_config())),
        )
        .await
        .unwrap();
    let mut exchange = manager.get_exchange("stub").await.unwrap();
    assert_passive_adapter_refused(exchange.as_mut()).await;
}

fn runtime_config() -> Config {
    toml::from_str(include_str!("../config/bot.toml")).unwrap()
}

#[test]
fn paper_configuration_does_not_require_broker_secrets() {
    let mut config = runtime_config();
    config.exchange.api_key = None;
    config.exchange.api_secret = None;
    assert!(config.validate().is_ok());
}

#[test]
fn live_configuration_is_refused_even_with_credentials_and_testnet() {
    for testnet in [false, true] {
        let mut config = runtime_config();
        config.bot.mode = TradingMode::Live;
        config.exchange.testnet = testnet;
        config.exchange.sandbox = testnet;
        config.exchange.api_key = Some(KEY_SENTINEL.into());
        config.exchange.api_secret = Some(SECRET_SENTINEL.into());
        let error = config
            .validate()
            .expect_err("Live must be unavailable")
            .to_string();
        assert!(error.to_lowercase().contains("live"));
        assert!(!error.contains(KEY_SENTINEL));
        assert!(!error.contains(SECRET_SENTINEL));
    }
}

#[test]
fn paper_configuration_still_requires_a_strategy() {
    let mut config = runtime_config();
    config.strategies.clear();
    assert!(config.validate().is_err());
}

#[test]
fn public_observation_requires_a_supported_exchange_and_enabled_strategy() {
    let mut config = runtime_config();
    config.exchange.id = "bithumb".into();
    assert!(config.validate().is_err());
    let mut config = runtime_config();
    for strategy in &mut config.strategies {
        strategy.enabled = false;
    }
    assert!(config.validate().is_err());
}

#[test]
fn enabled_strategy_rejects_ambiguous_stream_identity() {
    for symbol in [
        "",
        "BTC//USDT",
        "BTC USDT",
        "BTC/",
        "../BTC",
        "BTC?token=secret",
    ] {
        let mut config = runtime_config();
        config.strategies[0].symbol = symbol.into();
        assert!(config.validate().is_err(), "accepted malformed symbol");
    }
    for timeframe in ["", "2m", "4H", "1m?token=secret"] {
        let mut config = runtime_config();
        config.strategies[0].timeframe = timeframe.into();
        assert!(config.validate().is_err(), "accepted unsupported timeframe");
    }
}

#[test]
fn strategy_parameters_are_validated_before_allocating_buffers() {
    for value in [
        serde_json::json!(0),
        serde_json::json!(-1),
        serde_json::json!(1.5),
        serde_json::json!("50"),
        serde_json::json!(u64::MAX),
    ] {
        let mut config = runtime_config();
        config.strategies[0]
            .params
            .insert("fast_period".into(), value);
        assert!(config.validate().is_err());
    }
    let mut config = runtime_config();
    config.strategies[0]
        .params
        .insert("fast_period".into(), serde_json::json!(200));
    assert!(config.validate().is_err());
    let mut config = runtime_config();
    config.strategies[1]
        .params
        .insert("oversold".into(), serde_json::json!(80));
    assert!(config.validate().is_err());
    let mut config = runtime_config();
    config.strategies[0].strategy_type = "unimplemented".into();
    assert!(config.validate().is_err());
}

#[test]
fn strategy_names_fit_the_observation_message_contract() {
    for name in [
        "x".repeat(257),
        "line\nbreak".into(),
        "tab\tname".into(),
        "nul\0name".into(),
    ] {
        let mut config = runtime_config();
        config.strategies[0].name = name;
        assert!(
            config.validate().is_err(),
            "Protocol-invalid strategy names must fail before startup"
        );
    }
    let mut config = runtime_config();
    config.strategies[0].name = "x".repeat(256);
    assert!(config.validate().is_ok());
}

struct OwnedDirectory(PathBuf);

impl OwnedDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("trading-boundary-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn malformed_configuration_does_not_echo_secret_values_or_parser_causes() {
    let directory = OwnedDirectory::new();
    let path = directory.0.join("bot.toml");
    let malformed = include_str!("../config/bot.toml").replace(
        "[exchange]\n",
        &format!("[exchange]\napi_secret = \"{SECRET_SENTINEL}\n"),
    );
    std::fs::write(&path, malformed).unwrap();
    let error = Config::from_file(path.to_str().unwrap()).unwrap_err();
    for diagnostic in [
        error.to_string(),
        format!("{error:#}"),
        format!("{error:?}"),
    ] {
        assert!(!diagnostic.contains(SECRET_SENTINEL));
    }
    assert!(error
        .chain()
        .all(|cause| !cause.to_string().contains(SECRET_SENTINEL)));
}

#[test]
fn malformed_worker_configuration_is_sanitized_before_initialization() {
    let directory = OwnedDirectory::new();
    let config_directory = directory.0.join("config");
    std::fs::create_dir(&config_directory).unwrap();
    let malformed = include_str!("../config/bot.toml").replace(
        "[exchange]\n",
        &format!("[exchange]\napi_secret = \"{SECRET_SENTINEL}\n"),
    );
    std::fs::write(config_directory.join("bot.toml"), malformed).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_trading-server"))
        .env_clear()
        .current_dir(&directory.0)
        .args(["worker", "--config-path"])
        .arg(&config_directory)
        .arg("--master-socket")
        .arg(directory.0.join("never-created.sock"))
        .output()
        .unwrap();
    assert!(!output.status.success());
    for diagnostic in [&output.stdout, &output.stderr] {
        assert!(!String::from_utf8_lossy(diagnostic).contains(SECRET_SENTINEL));
    }
    assert!(!directory.0.join("data").exists());
}

#[test]
fn live_worker_refuses_before_creating_data_or_connecting_to_master() {
    let directory = OwnedDirectory::new();
    let config_directory = directory.0.join("config");
    std::fs::create_dir(&config_directory).unwrap();
    let mut config = runtime_config();
    config.bot.mode = TradingMode::Live;
    config.exchange.api_key = Some(KEY_SENTINEL.into());
    config.exchange.api_secret = Some(SECRET_SENTINEL.into());
    std::fs::write(
        config_directory.join("bot.toml"),
        toml::to_string(&config).unwrap(),
    )
    .unwrap();

    // An absent private Unix socket also stops the old worker before it can
    // initialize any market-data transport, making the RED test safe.
    let output = Command::new(env!("CARGO_BIN_EXE_trading-server"))
        .env_clear()
        .current_dir(&directory.0)
        .arg("worker")
        .arg("--config-path")
        .arg(&config_directory)
        .arg("--master-socket")
        .arg(directory.0.join("never-created.sock"))
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.to_lowercase().contains("live"), "{stderr}");
    assert!(!stderr.contains(KEY_SENTINEL));
    assert!(!stderr.contains(SECRET_SENTINEL));
    assert!(!directory.0.join("data").exists());
    assert!(!directory.0.join("never-created.sock").exists());
}
