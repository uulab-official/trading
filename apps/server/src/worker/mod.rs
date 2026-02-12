use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::sync::{broadcast, RwLock};
use tokio::time::{interval, Duration};
use tracing::{error, info, warn};

use crate::core::config::Config;
use crate::core::database::Database;
use crate::core::models::{Candle, Order, OrderSide, OrderStatus, OrderType};
use crate::core::order_manager::OrderManager;
use crate::core::portfolio::PortfolioManager;
use crate::core::risk_manager::{RiskConfig, RiskManager};
use crate::exchange::{Exchange, ExchangeConfig, ExchangeEvent, ExchangeManager};
use crate::exchange::binance::BinanceClient;
use crate::strategy::{Signal, Strategy};
use crate::strategy::rsi::RsiStrategy;
use crate::strategy::ma_cross::MaCrossStrategy;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum WorkerMessage {
    PriceUpdate { symbol: String, price: f64 },
    OrderUpdate(Order),
    PositionUpdate(crate::core::models::Position),
    TradeSignal { symbol: String, signal: String },
    SystemStatus { status: String, message: String },
    Heartbeat,
}

pub struct WorkerProcess {
    config: Config,
    master_socket: String,
    db: Arc<Database>,
    exchange_manager: Arc<ExchangeManager>,
    order_manager: Arc<OrderManager>,
    portfolio_manager: Arc<PortfolioManager>,
    risk_manager: Arc<RwLock<RiskManager>>,
    strategies: Arc<RwLock<Vec<Box<dyn Strategy>>>>,
    shutdown_tx: broadcast::Sender<()>,
    binance_client: Arc<RwLock<Option<BinanceClient>>>,
    master_tx: RwLock<Option<UnixStream>>,
}

impl std::fmt::Debug for WorkerProcess {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkerProcess")
            .field("config", &self.config)
            .field("master_socket", &self.master_socket)
            .finish_non_exhaustive()
    }
}

impl WorkerProcess {
    pub async fn new(config_path: String, master_socket: String) -> Result<Self> {
        let config = Config::from_file(&format!("{}/bot.toml", config_path))?;
        
        std::fs::create_dir_all("data")?;
        let db = Arc::new(Database::new("sqlite://data/trading.db").await?);
        
        let exchange_manager = Arc::new(ExchangeManager::new());
        let order_manager = Arc::new(OrderManager::new());
        let portfolio_manager = Arc::new(PortfolioManager::new());
        
        let risk_config = RiskConfig {
            max_daily_loss_pct: config.risk.max_daily_loss_pct,
            max_positions: config.risk.max_positions,
            risk_per_trade_pct: config.risk.risk_per_trade_pct,
            max_leverage: config.risk.max_leverage,
            circuit_breaker_threshold: config.risk.circuit_breaker_threshold,
        };
        let risk_manager = Arc::new(RwLock::new(RiskManager::new(risk_config)));
        
        let (shutdown_tx, _) = broadcast::channel(1);

        info!("Worker process initialized");

        Ok(Self {
            config,
            master_socket,
            db,
            exchange_manager,
            order_manager,
            portfolio_manager,
            risk_manager,
            strategies: Arc::new(RwLock::new(Vec::new())),
            shutdown_tx,
            binance_client: Arc::new(RwLock::new(None)),
            master_tx: RwLock::new(None),
        })
    }

    async fn connect_to_master(&self) -> Result<()> {
        let stream = UnixStream::connect(&self.master_socket).await?;
        let mut master_tx = self.master_tx.write().await;
        *master_tx = Some(stream);
        info!("Connected to master at {}", self.master_socket);
        Ok(())
    }

    async fn send_to_master(&self, message: WorkerMessage) -> Result<()> {
        let mut master_tx = self.master_tx.write().await;
        if let Some(stream) = master_tx.as_mut() {
            let json = serde_json::to_string(&message)?;
            let bytes = json.as_bytes();
            let len = bytes.len() as u32;
            stream.write_all(&len.to_be_bytes()).await?;
            stream.write_all(bytes).await?;
            Ok(())
        } else {
            Err(anyhow::anyhow!("Not connected to master"))
        }
    }
    
    pub async fn run(self) -> Result<()> {
        info!("Worker process starting...");

        // Connect to master first
        if let Err(e) = self.connect_to_master().await {
            error!("Failed to connect to master: {}", e);
            return Err(e);
        }

        self.send_to_master(WorkerMessage::SystemStatus {
            status: "initializing".to_string(),
            message: "Worker starting up".to_string(),
        }).await.ok();

        self.init_exchanges().await?;
        self.init_strategies().await?;
        self.restore_state().await?;

        self.send_to_master(WorkerMessage::SystemStatus {
            status: "running".to_string(),
            message: "Worker initialized and running".to_string(),
        }).await.ok();

        let mut shutdown_rx = self.shutdown_tx.subscribe();
        let mut health_interval = interval(Duration::from_secs(30));

        let order_manager = self.order_manager.clone();
        let portfolio_manager = self.portfolio_manager.clone();
        let risk_manager = self.risk_manager.clone();
        let strategies = self.strategies.clone();
        let db = self.db.clone();

        let mut binance_rx = {
            let client = self.binance_client.read().await;
            match client.as_ref() {
                Some(client) => client.get_event_receiver(),
                None => {
                    warn!("No Binance client available, running in limited mode");
                    let (tx, rx) = broadcast::channel(1);
                    drop(tx);
                    rx
                }
            }
        };

        let worker = Arc::new(self);

        let has_binance = {
            let client = worker.binance_client.read().await;
            client.is_some()
        };

        let binance_task = {
            let worker = Arc::clone(&worker);
            tokio::spawn(async move {
                if !has_binance {
                    futures_util::future::pending::<()>().await;
                    return;
                }
                let mut client = worker.binance_client.write().await;
                if let Some(ref mut binance) = client.as_mut() {
                    info!("Starting Binance message loop");
                    if let Err(e) = binance.run_message_loop().await {
                        error!("Binance message loop error: {}", e);
                    }
                }
            })
        };

        let forwarder_task = {
            let worker = Arc::clone(&worker);
            tokio::spawn(async move {
                let mut rx = {
                    let client = worker.binance_client.read().await;
                    match client.as_ref() {
                        Some(client) => client.get_event_receiver(),
                        None => {
                            let (tx, rx) = broadcast::channel(1);
                            drop(tx);
                            rx
                        }
                    }
                };

                info!("Starting price forwarder task");
                loop {
                    match rx.recv().await {
                        Ok(event) => {
                            match event {
                                crate::exchange::ExchangeEvent::Ticker(ticker) => {
                                    worker.send_to_master(WorkerMessage::PriceUpdate {
                                        symbol: ticker.symbol.clone(),
                                        price: ticker.last,
                                    }).await.ok();
                                }
                                crate::exchange::ExchangeEvent::Candle(candle) => {
                                    worker.send_to_master(WorkerMessage::SystemStatus {
                                        status: "candle".to_string(),
                                        message: format!("{} {} closed at {}", 
                                            candle.symbol, candle.timestamp, candle.close),
                                    }).await.ok();
                                }
                                _ => {}
                            }
                        }
                        Err(_) => {
                            tokio::time::sleep(Duration::from_secs(1)).await;
                            continue;
                        }
                    }
                }
            })
        };

        let mock_price_task = {
            let worker = Arc::clone(&worker);
            tokio::spawn(async move {
                if has_binance {
                    futures_util::future::pending::<()>().await;
                    return;
                }
                
                info!("Starting mock price generator (no Binance connection)");
                let mut interval = interval(Duration::from_secs(5));
                let mut price = 45000.0;
                
                loop {
                    interval.tick().await;
                    price += (rand::random::<f64>() - 0.5) * 100.0;
                    worker.send_to_master(WorkerMessage::PriceUpdate {
                        symbol: "BTC/USDT".to_string(),
                        price,
                    }).await.ok();
                }
            })
        };
        
        tokio::select! {
            _ = worker.run_trading_loop(
                &mut binance_rx,
                order_manager,
                portfolio_manager,
                risk_manager,
                strategies,
                db,
            ) => {
                info!("Trading loop ended");
            }
            _ = shutdown_rx.recv() => {
                info!("Shutdown signal received");
            }
            _ = tokio::signal::ctrl_c() => {
                info!("Ctrl+C received, shutting down");
            }
            _ = async {
                loop {
                    health_interval.tick().await;
                    worker.send_to_master(WorkerMessage::Heartbeat).await.ok();
                }
            } => {}
            _ = binance_task => {
                warn!("Binance message loop ended");
            }
            _ = forwarder_task => {
                warn!("Price forwarder task ended");
            }
            _ = mock_price_task => {
                warn!("Mock price task ended");
            }
            else => {
                warn!("All tasks completed unexpectedly");
            }
        }

        worker.send_to_master(WorkerMessage::SystemStatus {
            status: "shutdown".to_string(),
            message: "Worker shutting down".to_string(),
        }).await.ok();

        worker.cleanup().await?;
        info!("Worker process stopped");

        Ok(())
    }
    
    async fn init_exchanges(&self) -> Result<()> {
        let exchange_config = ExchangeConfig {
            id: self.config.exchange.id.clone(),
            api_key: self.config.exchange.api_key.clone().unwrap_or_default(),
            api_secret: self.config.exchange.api_secret.clone().unwrap_or_default(),
            ws_url: self.config.exchange.ws_url.clone(),
            rest_url: self.config.exchange.rest_url.clone(),
            testnet: self.config.exchange.testnet,
        };

        let mut binance = BinanceClient::new(exchange_config);

        match binance.connect().await {
            Ok(_) => {
                for strategy_config in &self.config.strategies {
                    if !strategy_config.enabled {
                        continue;
                    }

                    let symbol = &strategy_config.symbol;
                    let timeframe = &strategy_config.timeframe;

                    if let Err(e) = binance.subscribe_candles(symbol, timeframe).await {
                        warn!("Failed to subscribe to {} candles: {}", symbol, e);
                    } else {
                        info!("Subscribed to {} candles ({})", symbol, timeframe);
                    }

                    if let Err(e) = binance.subscribe_ticker(symbol).await {
                        warn!("Failed to subscribe to {} ticker: {}", symbol, e);
                    } else {
                        info!("Subscribed to {} ticker", symbol);
                    }
                }

                {
                    let mut client = self.binance_client.write().await;
                    *client = Some(binance);
                }

                info!("Exchanges initialized and WebSocket connected");
            }
            Err(e) => {
                warn!("Failed to connect to exchange: {}. Running in mock mode.", e);
                self.send_to_master(WorkerMessage::SystemStatus {
                    status: "mock_mode".to_string(),
                    message: "Running without exchange connection".to_string(),
                }).await.ok();
            }
        }

        Ok(())
    }
    
    async fn init_strategies(&self) -> Result<()> {
        let mut strategies = self.strategies.write().await;
        
        for strategy_config in &self.config.strategies {
            if !strategy_config.enabled {
                continue;
            }
            
            match strategy_config.strategy_type.as_str() {
                "rsi" => {
                    let period = strategy_config.params.get("period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(14) as usize;
                    let oversold = strategy_config.params.get("oversold")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(30.0);
                    let overbought = strategy_config.params.get("overbought")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(70.0);
                    
                    strategies.push(Box::new(RsiStrategy::new(
                        strategy_config.name.clone(),
                        period,
                        oversold,
                        overbought,
                    )));
                    info!("Initialized RSI strategy: {}", strategy_config.name);
                }
                "ma_cross" => {
                    let fast = strategy_config.params.get("fast_period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(10) as usize;
                    let slow = strategy_config.params.get("slow_period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(20) as usize;
                    
                    strategies.push(Box::new(MaCrossStrategy::new(
                        strategy_config.name.clone(),
                        fast,
                        slow,
                    )));
                    info!("Initialized MA Cross strategy: {}", strategy_config.name);
                }
                _ => {
                    warn!("Unknown strategy type: {}", strategy_config.strategy_type);
                }
            }
        }
        
        info!("Initialized {} strategies", strategies.len());
        Ok(())
    }
    
    async fn restore_state(&self) -> Result<()> {
        let open_orders = self.db.get_open_orders().await?;
        let order_count = open_orders.len();
        for order in &open_orders {
            info!("Restored open order: {}", order.id);
        }

        let positions = self.db.get_all_positions().await?;
        let position_count = positions.len();
        for position in &positions {
            self.portfolio_manager.open_position(
                &Order {
                    id: format!("restore-{}", position.symbol),
                    symbol: position.symbol.clone(),
                    side: position.side.clone(),
                    order_type: OrderType::Market,
                    quantity: position.quantity,
                    price: Some(position.entry_price),
                    status: OrderStatus::Filled,
                    created_at: position.opened_at,
                    updated_at: position.opened_at,
                    exchange_order_id: None,
                    filled_quantity: position.quantity,
                    average_price: Some(position.entry_price),
                    commission: None,
                    commission_asset: None,
                },
                position.entry_price,
            ).ok();
            info!("Restored position: {} {:?}", position.symbol, position.side);
        }

        info!("State restored: {} orders, {} positions", order_count, position_count);
        Ok(())
    }
    
    async fn run_trading_loop(
        &self,
        event_rx: &mut broadcast::Receiver<ExchangeEvent>,
        order_manager: Arc<OrderManager>,
        portfolio_manager: Arc<PortfolioManager>,
        risk_manager: Arc<RwLock<RiskManager>>,
        strategies: Arc<RwLock<Vec<Box<dyn Strategy>>>>,
        db: Arc<Database>,
    ) -> Result<()> {
        let mut last_candles: HashMap<String, Candle> = HashMap::new();
        
        info!("Trading loop started");
        
        loop {
            tokio::select! {
                Ok(event) = event_rx.recv() => {
                    match event {
                        ExchangeEvent::Ticker(ticker) => {
                            portfolio_manager.update_position_prices(&ticker.symbol, ticker.last);
                        }
                        ExchangeEvent::Candle(candle) => {
                            db.save_candle(&candle).await.ok();
                            
                            let mut strategies = strategies.write().await;
                            for strategy in strategies.iter_mut() {
                                let signal = strategy.on_candle(&candle);
                                
                                match signal {
                                    Signal::Buy => {
                                        if let Err(e) = self.handle_buy_signal(
                                            &candle.symbol,
                                            candle.close,
                                            &order_manager,
                                            &portfolio_manager,
                                            &risk_manager,
                                            &db,
                                        ).await {
                                            error!("Failed to execute buy signal: {}", e);
                                        }
                                    }
                                    Signal::Sell => {
                                        if let Err(e) = self.handle_sell_signal(
                                            &candle.symbol,
                                            candle.close,
                                            &order_manager,
                                            &portfolio_manager,
                                            &db,
                                        ).await {
                                            error!("Failed to execute sell signal: {}", e);
                                        }
                                    }
                                    Signal::Hold => {}
                                }
                            }
                            
                            last_candles.insert(candle.symbol.clone(), candle);
                        }
                        ExchangeEvent::TradeUpdate(order) => {
                            order_manager.handle_exchange_event(&ExchangeEvent::TradeUpdate(order));
                        }
                        _ => {}
                    }
                }
                else => {
                    tokio::time::sleep(Duration::from_secs(1)).await;
                }
            }
        }
    }
    
    async fn handle_buy_signal(
        &self,
        symbol: &str,
        price: f64,
        order_manager: &Arc<OrderManager>,
        portfolio_manager: &Arc<PortfolioManager>,
        risk_manager: &Arc<RwLock<RiskManager>>,
        db: &Arc<Database>,
    ) -> Result<()> {
        let risk = risk_manager.read().await;
        
        if !risk.is_trading_allowed() {
            return Ok(());
        }
        
        if portfolio_manager.get_position(symbol).is_some() {
            return Ok(());
        }
        
        let balance = 10000.0;
        let stop_loss = risk.calculate_stop_loss(price, &OrderSide::Buy, price * 0.02);
        let position_size = risk.calculate_position_size(balance, price, stop_loss);
        
        if position_size <= 0.0 {
            return Ok(());
        }
        
        drop(risk);
        
        let order = order_manager.create_order(
            symbol.to_string(),
            OrderSide::Buy,
            OrderType::Market,
            position_size,
            Some(price),
        ).await?;
        
        db.save_order(&order).await?;
        
        portfolio_manager.open_position(&order, price)?;
        db.save_position(&portfolio_manager.get_position(symbol).unwrap()).await?;
        
        info!(
            symbol = symbol,
            price = price,
            quantity = position_size,
            "Buy signal executed"
        );
        
        Ok(())
    }
    
    async fn handle_sell_signal(
        &self,
        symbol: &str,
        price: f64,
        order_manager: &Arc<OrderManager>,
        portfolio_manager: &Arc<PortfolioManager>,
        db: &Arc<Database>,
    ) -> Result<()> {
        if let Some(position) = portfolio_manager.get_position(symbol) {
            if position.side == OrderSide::Buy {
                let pnl = portfolio_manager.close_position(symbol, price)?;
                db.delete_position(symbol).await?;
                
                let mut risk = self.risk_manager.write().await;
                risk.update_pnl(pnl);
                
                info!(
                    symbol = symbol,
                    price = price,
                    pnl = pnl,
                    "Sell signal executed - position closed"
                );
            }
        }
        
        Ok(())
    }
    
    async fn cleanup(&self) -> Result<()> {
        info!("Cleaning up worker process...");
        
        let orders = self.order_manager.get_all_orders();
        for order in orders {
            self.db.save_order(&order).await?;
        }
        
        let positions = self.portfolio_manager.get_all_positions();
        for position in positions {
            self.db.save_position(&position).await?;
        }
        
        info!("State saved to database");
        Ok(())
    }
}