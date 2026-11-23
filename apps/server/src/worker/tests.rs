use super::*;
use crate::core::models::{Candle, OrderSide, OrderStatus, OrderType, Ticker};
use chrono::Utc;
use tokio::io::AsyncReadExt;
use tokio::time::timeout;

async fn fixture_worker() -> WorkerProcess {
    let config: Config = toml::from_str(include_str!("../../config/bot.toml")).unwrap();
    let risk = RiskConfig {
        max_daily_loss_pct: config.risk.max_daily_loss_pct,
        max_positions: config.risk.max_positions,
        risk_per_trade_pct: config.risk.risk_per_trade_pct,
        max_leverage: config.risk.max_leverage,
        circuit_breaker_threshold: config.risk.circuit_breaker_threshold,
    };
    let (shutdown_tx, _) = broadcast::channel(1);
    WorkerProcess {
        config,
        master_socket: "unused-synthetic-socket".into(),
        db: Arc::new(Database::new("sqlite::memory:").await.unwrap()),
        order_manager: Arc::new(OrderManager::new()),
        portfolio_manager: Arc::new(PortfolioManager::new()),
        risk_manager: Arc::new(RwLock::new(RiskManager::new(risk))),
        strategies: Arc::new(RwLock::new(Vec::new())),
        shutdown_tx,
        binance_client: Arc::new(RwLock::new(None)),
        master_tx: RwLock::new(None),
    }
}

#[tokio::test]
async fn buy_signal_cannot_fabricate_fills_without_an_execution_ledger() {
    let worker = fixture_worker().await;
    let result = worker
        .handle_buy_signal(
            "BTCUSDT",
            100.0,
            &worker.order_manager,
            &worker.portfolio_manager,
            &worker.risk_manager,
            &worker.db,
        )
        .await;
    assert!(result.is_err(), "Unimplemented execution must be explicit");
    assert!(worker.order_manager.get_all_orders().is_empty());
    assert!(worker.portfolio_manager.get_all_positions().is_empty());
    assert!(worker.db.get_open_orders().await.unwrap().is_empty());
    assert!(worker.db.get_all_positions().await.unwrap().is_empty());
}

#[tokio::test]
async fn sell_signal_cannot_erase_holdings_without_an_execution_ledger() {
    let worker = fixture_worker().await;
    let now = Utc::now();
    let order = Order {
        id: "synthetic-prior-fill".into(),
        symbol: "BTCUSDT".into(),
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity: 2.0,
        price: Some(100.0),
        status: OrderStatus::Filled,
        created_at: now,
        updated_at: now,
        exchange_order_id: None,
        filled_quantity: 2.0,
        average_price: Some(100.0),
        commission: None,
        commission_asset: None,
    };
    worker
        .portfolio_manager
        .open_position(&order, 100.0)
        .unwrap();
    let position = worker.portfolio_manager.get_position("BTCUSDT").unwrap();
    worker.db.save_position(&position).await.unwrap();
    let result = worker
        .handle_sell_signal("BTCUSDT", 200.0, &worker.portfolio_manager, &worker.db)
        .await;
    assert!(result.is_err(), "Unimplemented execution must be explicit");
    assert_eq!(
        worker
            .portfolio_manager
            .get_position("BTCUSDT")
            .unwrap()
            .quantity,
        2.0
    );
    assert_eq!(worker.db.get_all_positions().await.unwrap().len(), 1);
}

#[tokio::test]
async fn closed_event_channel_returns_an_error_instead_of_spinning() {
    let worker = fixture_worker().await;
    let (tx, mut rx) = broadcast::channel(2);
    drop(tx);
    let result = timeout(
        Duration::from_secs(1),
        worker.run_trading_loop(
            &mut rx,
            worker.order_manager.clone(),
            worker.portfolio_manager.clone(),
            worker.risk_manager.clone(),
            worker.strategies.clone(),
            worker.db.clone(),
        ),
    )
    .await
    .expect("Closed event channel must finish promptly");
    assert!(result.is_err());
}

#[tokio::test]
async fn canceled_partial_master_frame_cannot_be_reused() {
    let worker = Arc::new(fixture_worker().await);
    let (writer, mut reader) = UnixStream::pair().unwrap();
    *worker.master_tx.write().await = Some(writer);
    let sending = worker.clone();
    let task = tokio::spawn(async move {
        sending
            .send_to_master(WorkerMessage::SystemStatus {
                status: "fixture".into(),
                message: "x".repeat(8 * 1024 * 1024),
            })
            .await
    });
    // Observe the frame header, then deliberately leave the large body unread.
    let length = timeout(Duration::from_secs(2), reader.read_u32())
        .await
        .unwrap()
        .unwrap();
    assert!(length > 8 * 1024 * 1024);
    assert!(!task.is_finished());
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    assert!(
        worker.master_tx.read().await.is_none(),
        "A canceled partial frame must close its connection"
    );
    assert!(worker
        .send_to_master(WorkerMessage::Heartbeat)
        .await
        .is_err());
}

fn bar(symbol: &str, minute: i64, close: f64) -> Candle {
    Candle {
        symbol: symbol.into(),
        timeframe: "1m".into(),
        timestamp: chrono::DateTime::from_timestamp(1_700_000_040 + minute * 60, 0).unwrap(),
        open: close,
        high: close,
        low: close,
        close,
        volume: 1.0,
    }
}

async fn observation_worker() -> WorkerProcess {
    let mut worker = fixture_worker().await;
    worker.config.strategies.truncate(1);
    let strategy = &mut worker.config.strategies[0];
    strategy.timeframe = "1m".into();
    strategy
        .params
        .insert("fast_period".into(), serde_json::json!(1));
    strategy
        .params
        .insert("slow_period".into(), serde_json::json!(2));
    worker
}

async fn observe_events(
    worker: &WorkerProcess,
    events: Vec<ExchangeEvent>,
) -> (Result<()>, Vec<WorkerMessage>) {
    let (writer, mut reader) = UnixStream::pair().unwrap();
    *worker.master_tx.write().await = Some(writer);
    let reading = tokio::spawn(async move {
        let mut messages = Vec::new();
        while let Ok(length) = reader.read_u32().await {
            assert!(length < 65536);
            let mut payload = vec![0; length as usize];
            reader.read_exact(&mut payload).await.unwrap();
            messages.push(serde_json::from_slice(&payload).unwrap());
        }
        messages
    });
    let (tx, mut rx) = broadcast::channel(events.len().max(2));
    for event in events {
        tx.send(event).unwrap();
    }
    drop(tx);
    let result = timeout(
        Duration::from_secs(2),
        worker.run_trading_loop(
            &mut rx,
            worker.order_manager.clone(),
            worker.portfolio_manager.clone(),
            worker.risk_manager.clone(),
            worker.strategies.clone(),
            worker.db.clone(),
        ),
    )
    .await
    .unwrap();
    worker.master_tx.write().await.take();
    (
        result,
        timeout(Duration::from_secs(2), reading)
            .await
            .unwrap()
            .unwrap(),
    )
}

#[tokio::test]
async fn real_event_path_delivers_prices_and_signals_without_financial_mutation() {
    let worker = observation_worker().await;
    worker.init_strategies().await.unwrap();
    let ticker = Ticker {
        symbol: "BTCUSDT".into(),
        bid: 100.0,
        ask: 102.0,
        last: 101.0,
        volume: 1.0,
        timestamp: bar("BTCUSDT", 0, 100.0).timestamp,
    };
    let (result, messages) = observe_events(
        &worker,
        vec![
            ExchangeEvent::Ticker(ticker),
            ExchangeEvent::Candle(bar("BTCUSDT", 0, 100.0)),
            ExchangeEvent::Candle(bar("BTCUSDT", 1, 101.0)),
            ExchangeEvent::Candle(bar("BTCUSDT", 1, 101.0)),
        ],
    )
    .await;
    assert!(result.unwrap_err().to_string().contains("channel closed"));
    assert_eq!(messages.len(), 2);
    assert!(
        matches!(&messages[0], WorkerMessage::PriceUpdate { symbol, price } if symbol == "BTCUSDT" && *price == 101.0)
    );
    assert!(
        matches!(&messages[1], WorkerMessage::TradeSignal { symbol, signal } if symbol == "BTCUSDT" && signal.contains("observation only"))
    );
    assert!(worker.order_manager.get_all_orders().is_empty());
    assert!(worker.portfolio_manager.get_all_positions().is_empty());
    assert!(worker.db.get_open_orders().await.unwrap().is_empty());
    assert!(worker.db.get_all_positions().await.unwrap().is_empty());
}

#[tokio::test]
async fn persistence_conflict_halts_before_advancing_strategy_state() {
    let worker = observation_worker().await;
    worker.init_strategies().await.unwrap();
    let original = bar("BTCUSDT", 0, 100.0);
    worker.db.save_candle(&original).await.unwrap();
    let (result, messages) = observe_events(
        &worker,
        vec![ExchangeEvent::Candle(bar("BTCUSDT", 0, 200.0))],
    )
    .await;
    assert!(result
        .unwrap_err()
        .to_string()
        .contains("Conflicting closed candle"));
    assert!(messages.is_empty());
    let mut strategies = worker.strategies.write().await;
    assert_eq!(
        strategies[0].on_candle(&original).unwrap(),
        Some(Signal::Hold)
    );
}

#[tokio::test]
async fn unrelated_streams_do_not_reach_storage_or_strategy() {
    let worker = observation_worker().await;
    worker.init_strategies().await.unwrap();
    let other_symbol = bar("ETHUSDT", 0, 2000.0);
    let mut other_interval = bar("BTCUSDT", 0, 100.0);
    other_interval.timeframe = "5m".into();
    let (_, messages) = observe_events(
        &worker,
        vec![
            ExchangeEvent::Candle(other_symbol.clone()),
            ExchangeEvent::Candle(other_interval.clone()),
        ],
    )
    .await;
    assert!(messages.is_empty());
    for mut other in [other_symbol, other_interval] {
        other.volume = 10.0;
        worker
            .db
            .save_candle(&other)
            .await
            .expect("Unsubscribed candle must not already exist");
    }
    assert_eq!(
        worker.strategies.write().await[0]
            .on_candle(&bar("BTCUSDT", 0, 100.0))
            .unwrap(),
        Some(Signal::Hold)
    );
}

#[tokio::test]
async fn lost_events_and_disconnect_halt_without_substituting_prices() {
    let worker = observation_worker().await;
    let (tx, mut rx) = broadcast::channel(1);
    tx.send(ExchangeEvent::Connected).unwrap();
    tx.send(ExchangeEvent::Connected).unwrap();
    let result = worker
        .run_trading_loop(
            &mut rx,
            worker.order_manager.clone(),
            worker.portfolio_manager.clone(),
            worker.risk_manager.clone(),
            worker.strategies.clone(),
            worker.db.clone(),
        )
        .await;
    assert!(result.unwrap_err().to_string().contains("events were lost"));
    let (result, messages) = observe_events(&worker, vec![ExchangeEvent::Disconnected]).await;
    assert!(result.unwrap_err().to_string().contains("disconnected"));
    assert!(messages.is_empty());
}

#[tokio::test]
async fn worker_owns_transport_without_blocking_observation_delivery() {
    use futures_util::SinkExt;
    use tokio::net::TcpListener;
    use tokio_tungstenite::{accept_async, tungstenite::Message};

    let (master_writer, mut master_reader) = UnixStream::pair().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let feed = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(stream).await.unwrap();
        ws.send(Message::Text(serde_json::json!({"stream":"btcusdt@ticker", "data":{
            "e":"24hrTicker", "E":1700000100000_i64, "s":"BTCUSDT", "b":"100", "a":"102", "c":"101", "v":"1"
        }}).to_string())).await.unwrap();
        ws.close(None).await.unwrap();
    });
    let monitoring = tokio::spawn(async move {
        let mut prices = Vec::new();
        while let Ok(length) = master_reader.read_u32().await {
            let mut payload = vec![0; length as usize];
            master_reader.read_exact(&mut payload).await.unwrap();
            if let WorkerMessage::PriceUpdate { symbol, price } =
                serde_json::from_slice(&payload).unwrap()
            {
                prices.push((symbol, price));
            }
        }
        prices
    });
    let mut worker = observation_worker().await;
    *worker.master_tx.write().await = Some(master_writer);
    worker.config.exchange.testnet = false;
    worker.config.exchange.ws_url = format!("ws://{address}/ws");
    let result = timeout(Duration::from_secs(3), worker.run_connected())
        .await
        .unwrap();
    assert!(result.is_err(), "Closed feed must end observation");
    timeout(Duration::from_secs(2), feed)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        timeout(Duration::from_secs(2), monitoring)
            .await
            .unwrap()
            .unwrap(),
        vec![("BTCUSDT".into(), 101.0)]
    );
}
