use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use tokio::io::AsyncReadExt;
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::RwLock;
use tracing::{error, info, warn};

pub mod deploy;
pub mod web;

use web::{DashboardEvent, WebDashboard};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum WorkerMessage {
    PriceUpdate { symbol: String, price: f64 },
    OrderUpdate(crate::core::models::Order),
    PositionUpdate(crate::core::models::Position),
    TradeSignal { symbol: String, signal: String },
    SystemStatus { status: String, message: String },
    Heartbeat,
}

pub struct MasterProcess {
    config_path: String,
    web_port: u16,
    worker_port: u16,
    socket_path: PathBuf,
    current_worker: Arc<RwLock<Option<Child>>>,
    web_dashboard: Arc<WebDashboard>,
}

impl MasterProcess {
    pub async fn new(config_path: String, web_port: u16, worker_port: u16) -> Result<Arc<Self>> {
        let socket_path = PathBuf::from("/tmp/trading-master.sock");

        if socket_path.exists() {
            std::fs::remove_file(&socket_path)?;
        }

        let web_dashboard = Arc::new(WebDashboard::new(web_port));

        Ok(Arc::new(Self {
            config_path,
            web_port,
            worker_port,
            socket_path,
            current_worker: Arc::new(RwLock::new(None)),
            web_dashboard,
        }))
    }

    pub async fn run(self: Arc<Self>) -> Result<()> {
        info!(
            web_port = self.web_port,
            worker_port = self.worker_port,
            socket = %self.socket_path.display(),
            "Master process starting"
        );

        let unix_listener = UnixListener::bind(&self.socket_path)?;

        info!("Unix socket created at {:?}", self.socket_path);

        self.start_worker().await?;

        let web_dashboard = self.web_dashboard.clone();
        let web_task = async move {
            if let Err(e) = web_dashboard.run().await {
                error!("Web dashboard error: {}", e);
            }
        };

        let self_clone = self.clone();
        let unix_task = async move {
            if let Err(e) = self_clone.handle_unix_socket(unix_listener).await {
                error!("Unix socket error: {}", e);
            }
        };

        tokio::select! {
            _ = web_task => {
                info!("Web dashboard stopped");
            }
            _ = unix_task => {
                info!("Unix socket handler stopped");
            }
            _ = tokio::signal::ctrl_c() => {
                info!("Shutdown signal received");
                self.shutdown().await?;
            }
        }

        Ok(())
    }

    async fn start_worker(&self) -> Result<()> {
        let mut worker = self.current_worker.write().await;

        if worker.is_some() {
            warn!("Worker already running, not starting a new one");
            return Ok(());
        }

        info!("Starting worker process");

        let child = Command::new(std::env::current_exe()?)
            .arg("worker")
            .arg("--config-path")
            .arg(&self.config_path)
            .arg("--master-socket")
            .arg(&self.socket_path)
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()?;

        info!("Worker process started with PID: {:?}", child.id());
        *worker = Some(child);

        Ok(())
    }

    async fn handle_unix_socket(&self, listener: UnixListener) -> Result<()> {
        info!("Unix socket handler started");

        loop {
            let (stream, _) = listener.accept().await?;
            let worker = self.current_worker.clone();
            let event_tx = self.web_dashboard.get_event_sender();

            tokio::spawn(async move {
                if let Err(e) = handle_worker_connection(stream, worker, event_tx).await {
                    error!("Worker connection error: {}", e);
                }
            });
        }
    }

    async fn shutdown(&self) -> Result<()> {
        info!("Shutting down master process");

        let mut worker = self.current_worker.write().await;
        if let Some(mut child) = worker.take() {
            info!("Terminating worker process");
            let _ = child.kill();
            let _ = child.wait();
        }

        if self.socket_path.exists() {
            std::fs::remove_file(&self.socket_path)?;
        }

        Ok(())
    }

    pub async fn perform_hot_swap(&self, new_binary: &str) -> Result<()> {
        info!("Initiating hot-swap deployment");

        let mut worker = self.current_worker.write().await;

        if let Some(ref mut old_worker) = *worker {
            info!("Starting new worker process");
            let new_worker = Command::new(new_binary)
                .arg("worker")
                .arg("--config-path")
                .arg(&self.config_path)
                .arg("--master-socket")
                .arg(&self.socket_path)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()?;

            info!("New worker started, signaling old worker to shutdown");

            tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;

            info!("Terminating old worker");
            let _ = old_worker.kill();
            let _ = old_worker.wait();

            *worker = Some(new_worker);
            info!("Hot-swap completed successfully");
        } else {
            return Err(anyhow::anyhow!("No worker process running"));
        }

        Ok(())
    }
}

async fn handle_worker_connection(
    mut stream: UnixStream,
    _worker: Arc<RwLock<Option<Child>>>,
    event_tx: tokio::sync::broadcast::Sender<DashboardEvent>,
) -> Result<()> {
    info!("Worker connected to master");

    loop {
        // Read message length (4 bytes)
        let mut len_bytes = [0u8; 4];
        match stream.read_exact(&mut len_bytes).await {
            Ok(_) => {}
            Err(e) => {
                if e.kind() == std::io::ErrorKind::UnexpectedEof {
                    info!("Worker disconnected");
                    break;
                }
                return Err(e.into());
            }
        }
        let len = u32::from_be_bytes(len_bytes) as usize;

        // Read message
        let mut buffer = vec![0u8; len];
        stream.read_exact(&mut buffer).await?;

        // Parse message
        let message: WorkerMessage = serde_json::from_slice(&buffer)?;

        // Convert to DashboardEvent and broadcast
        let event = match &message {
            WorkerMessage::PriceUpdate { symbol, price } => {
                info!("Received PriceUpdate from Worker: {} = ${:.2}", symbol, price);
                Some(DashboardEvent::PriceUpdate { symbol: symbol.clone(), price: *price })
            }
            WorkerMessage::TradeSignal { symbol, signal } => {
                Some(DashboardEvent::TradeSignal { symbol: symbol.clone(), signal: signal.clone() })
            }
            WorkerMessage::OrderUpdate(order) => {
                Some(DashboardEvent::OrderUpdate(order.clone()))
            }
            WorkerMessage::PositionUpdate(position) => {
                Some(DashboardEvent::PositionUpdate(position.clone()))
            }
            WorkerMessage::SystemStatus { status, message } => {
                info!("Worker status: {} - {}", status, message);
                Some(DashboardEvent::SystemStatus { status: status.clone(), message: message.clone() })
            }
            WorkerMessage::Heartbeat => {
                None
            }
        };

        if let Some(event) = event {
            let receiver_count = event_tx.send(event.clone()).unwrap_or(0);
            if receiver_count == 0 {
                info!("No WebSocket clients connected for event: {:?}", event);
            } else {
                info!("Broadcasted event to {} clients: {:?}", receiver_count, event);
            }
        }
    }

    Ok(())
}