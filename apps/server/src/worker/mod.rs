use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::sync::{broadcast, RwLock};
use tokio::time::{interval, Duration};
use tracing::{info, warn};

use crate::core::config::Config;
use crate::core::database::Database;
use crate::core::models::{normalize_market_symbol, Order};
use crate::core::order_manager::OrderManager;
use crate::core::portfolio::PortfolioManager;
use crate::core::risk_manager::{RiskConfig, RiskManager};
use crate::exchange::binance::BinanceClient;
use crate::exchange::{Exchange, ExchangeConfig, ExchangeEvent};
use crate::strategy::ma_cross::MaCrossStrategy;
use crate::strategy::rsi::RsiStrategy;
use crate::strategy::{Signal, Strategy, StrategyBinding};

#[cfg(test)]
mod tests;

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
    order_manager: Arc<OrderManager>,
    portfolio_manager: Arc<PortfolioManager>,
    risk_manager: Arc<RwLock<RiskManager>>,
    strategies: Arc<RwLock<Vec<StrategyBinding>>>,
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
        config.validate()?;

        std::fs::create_dir_all("data")?;
        let db = Arc::new(Database::new("sqlite://data/trading.db").await?);

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
        // Only a fully delivered frame restores the stream. Cancellation drops
        // this local owner as well, so shutdown cannot append to a partial frame.
        if let Some(mut stream) = master_tx.take() {
            let json = serde_json::to_string(&message)?;
            let bytes = json.as_bytes();
            let len = bytes.len() as u32;
            let result = tokio::time::timeout(Duration::from_secs(5), async {
                stream.write_all(&len.to_be_bytes()).await?;
                stream.write_all(bytes).await
            })
            .await;
            if matches!(result, Ok(Ok(()))) {
                *master_tx = Some(stream);
                Ok(())
            } else {
                // A partial frame cannot be reused as a healthy master connection.
                Err(anyhow::anyhow!(
                    "Master message delivery failed or timed out"
                ))
            }
        } else {
            Err(anyhow::anyhow!("Not connected to master"))
        }
    }

    pub async fn run(self) -> Result<()> {
        self.connect_to_master().await?;
        self.run_connected().await
    }

    async fn run_connected(self) -> Result<()> {
        self.init_strategies().await?;
        self.restore_state().await?;
        self.init_exchanges().await?;

        // Acquire the receiver before connecting, then transfer socket ownership.
        // No shared lock is held while the transport waits for market messages.
        let mut client = self
            .binance_client
            .write()
            .await
            .take()
            .ok_or_else(|| anyhow::anyhow!("Public market client is unavailable"))?;
        let mut event_rx = client.get_event_receiver();
        if let Err(error) = client.connect().await {
            self.send_to_master(WorkerMessage::SystemStatus {
                status: "feed_unavailable".into(),
                message: "Public market connection failed; observations stopped".into(),
            })
            .await
            .ok();
            return Err(error);
        }
        self.send_to_master(WorkerMessage::SystemStatus {
            status: "observation_only".into(),
            message: "Public market observations and strategy signals; execution unavailable"
                .into(),
        })
        .await?;

        let mut shutdown_rx = self.shutdown_tx.subscribe();
        let feed_task = tokio::spawn(async move { client.run_message_loop().await });
        let worker = Arc::new(self);
        let result = tokio::select! {
            result = worker.run_trading_loop(
                &mut event_rx, worker.order_manager.clone(), worker.portfolio_manager.clone(),
                worker.risk_manager.clone(), worker.strategies.clone(), worker.db.clone(),
            ) => result,
            _ = shutdown_rx.recv() => Ok(()),
            result = tokio::signal::ctrl_c() => result.map_err(anyhow::Error::from),
            result = worker.send_heartbeats() => result,
        };

        // Do not leave a detached socket task alive after an error or shutdown.
        if !feed_task.is_finished() {
            feed_task.abort();
        }
        let _ = feed_task.await;
        worker
            .send_to_master(WorkerMessage::SystemStatus {
                status: if result.is_ok() {
                    "stopped"
                } else {
                    "feed_halted"
                }
                .into(),
                message: "Market observations stopped; no financial state was changed".into(),
            })
            .await
            .ok();
        worker.cleanup().await?;
        result
    }

    async fn send_heartbeats(&self) -> Result<()> {
        let mut timer = interval(Duration::from_secs(30));
        loop {
            timer.tick().await;
            self.send_to_master(WorkerMessage::Heartbeat).await?;
        }
    }

    async fn init_exchanges(&self) -> Result<()> {
        let exchange_config = ExchangeConfig {
            id: self.config.exchange.id.clone(),
            // Market observation never forwards account credentials to its transport.
            api_key: String::new(),
            api_secret: String::new(),
            ws_url: self.config.exchange.ws_url.clone(),
            rest_url: self.config.exchange.rest_url.clone(),
            testnet: self.config.exchange.testnet,
        };
        let mut client = BinanceClient::new(exchange_config);
        for config in self
            .config
            .strategies
            .iter()
            .filter(|config| config.enabled)
        {
            client
                .subscribe_candles(&config.symbol, &config.timeframe)
                .await?;
            client.subscribe_ticker(&config.symbol).await?;
        }
        *self.binance_client.write().await = Some(client);
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
                    let period = strategy_config
                        .params
                        .get("period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(14) as usize;
                    let oversold = strategy_config
                        .params
                        .get("oversold")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(30.0);
                    let overbought = strategy_config
                        .params
                        .get("overbought")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(70.0);

                    let strategy: Box<dyn Strategy> = Box::new(RsiStrategy::new(
                        strategy_config.name.clone(),
                        period,
                        oversold,
                        overbought,
                    ));
                    strategies.push(StrategyBinding::new(
                        &strategy_config.symbol,
                        &strategy_config.timeframe,
                        strategy,
                    )?);
                    info!("Initialized RSI strategy: {}", strategy_config.name);
                }
                "ma_cross" => {
                    let fast = strategy_config
                        .params
                        .get("fast_period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(10) as usize;
                    let slow = strategy_config
                        .params
                        .get("slow_period")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(20) as usize;

                    let strategy: Box<dyn Strategy> = Box::new(MaCrossStrategy::new(
                        strategy_config.name.clone(),
                        fast,
                        slow,
                    ));
                    strategies.push(StrategyBinding::new(
                        &strategy_config.symbol,
                        &strategy_config.timeframe,
                        strategy,
                    )?);
                    info!("Initialized MA Cross strategy: {}", strategy_config.name);
                }
                _ => {
                    anyhow::bail!("Unsupported enabled strategy type");
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
            self.order_manager.restore_order(order.clone())?;
            info!("Restored open order: {}", order.id);
        }

        // Historical positions are inspected, never converted into fabricated fills.
        let position_count = self.db.get_all_positions().await?.len();
        info!(
            "State inspected: {} unresolved orders, {} historical positions",
            order_count, position_count
        );
        Ok(())
    }

    async fn run_trading_loop(
        &self,
        event_rx: &mut broadcast::Receiver<ExchangeEvent>,
        order_manager: Arc<OrderManager>,
        portfolio_manager: Arc<PortfolioManager>,
        risk_manager: Arc<RwLock<RiskManager>>,
        strategies: Arc<RwLock<Vec<StrategyBinding>>>,
        db: Arc<Database>,
    ) -> Result<()> {
        loop {
            let event = match event_rx.recv().await {
                Ok(event) => event,
                Err(broadcast::error::RecvError::Lagged(_)) => {
                    anyhow::bail!("Public market events were lost; strategy observations halted")
                }
                Err(broadcast::error::RecvError::Closed) => {
                    anyhow::bail!("Public market event channel closed")
                }
            };
            match event {
                ExchangeEvent::Ticker(ticker) => {
                    if normalize_market_symbol(&ticker.symbol)? != ticker.symbol
                        || !ticker.last.is_finite()
                        || ticker.last <= 0.0
                        || ticker.timestamp.timestamp_millis() < 0
                    {
                        anyhow::bail!("Invalid market ticker observation");
                    }
                    self.send_to_master(WorkerMessage::PriceUpdate {
                        symbol: ticker.symbol,
                        price: ticker.last,
                    })
                    .await?;
                }
                ExchangeEvent::Candle(candle) => {
                    candle.validate_closed()?;
                    let mut bindings = strategies.write().await;
                    if !bindings.iter().any(|binding| binding.matches(&candle)) {
                        continue;
                    }
                    // Persistence must succeed before a strategy consumes the bar.
                    db.save_candle(&candle).await?;
                    let mut signals = Vec::new();
                    for binding in bindings.iter_mut() {
                        if let Some(signal) = binding.on_candle(&candle)? {
                            if signal != Signal::Hold {
                                signals.push((binding.name().to_string(), signal));
                            }
                        }
                    }
                    drop(bindings);
                    for (name, signal) in signals {
                        self.send_to_master(WorkerMessage::TradeSignal {
                            symbol: candle.symbol.clone(),
                            signal: format!(
                                "{:?} (observation only; {} {})",
                                signal, name, candle.timeframe
                            ),
                        })
                        .await?;
                        let refusal = match signal {
                            Signal::Buy => {
                                self.handle_buy_signal(
                                    &candle.symbol,
                                    candle.close,
                                    &order_manager,
                                    &portfolio_manager,
                                    &risk_manager,
                                    &db,
                                )
                                .await
                            }
                            Signal::Sell => {
                                self.handle_sell_signal(
                                    &candle.symbol,
                                    candle.close,
                                    &portfolio_manager,
                                    &db,
                                )
                                .await
                            }
                            Signal::Hold => Ok(()),
                        };
                        if let Err(error) = refusal {
                            warn!("{}", error);
                        }
                    }
                }
                ExchangeEvent::Connected => {
                    self.send_to_master(WorkerMessage::SystemStatus {
                        status: "observation_only".into(),
                        message: "Public market transport connected; execution unavailable".into(),
                    })
                    .await?;
                }
                ExchangeEvent::Disconnected => {
                    anyhow::bail!("Public market feed disconnected; restart required")
                }
                ExchangeEvent::Error(_) => anyhow::bail!("Public market feed reported an error"),
                ExchangeEvent::TradeUpdate(_) => {
                    anyhow::bail!("Account update is not supported on the public observation path")
                }
                ExchangeEvent::OrderBook(_) => {}
            }
        }
    }

    async fn handle_buy_signal(
        &self,
        _symbol: &str,
        _price: f64,
        _order_manager: &Arc<OrderManager>,
        _portfolio_manager: &Arc<PortfolioManager>,
        _risk_manager: &Arc<RwLock<RiskManager>>,
        _db: &Arc<Database>,
    ) -> Result<()> {
        anyhow::bail!(
            "Rust paper execution is unavailable: cash, fee and risk accounting is not validated"
        )
    }

    async fn handle_sell_signal(
        &self,
        _symbol: &str,
        _price: f64,
        _portfolio_manager: &Arc<PortfolioManager>,
        _db: &Arc<Database>,
    ) -> Result<()> {
        anyhow::bail!(
            "Rust paper execution is unavailable: cash, fee and risk accounting is not validated"
        )
    }

    async fn cleanup(&self) -> Result<()> {
        // Observation shutdown must not rewrite historical orders or positions.
        info!("Observation worker stopped; persisted financial records preserved");
        Ok(())
    }
}
