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

use web::WebDashboard;

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
        let config = crate::core::config::Config::from_file(&format!("{}/bot.toml", config_path))?;
        config.validate()?;

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
            let dashboard = self.web_dashboard.clone();
            // Reserve a generation at accept time, before scheduling the task.
            // A previous worker's delayed message or EOF cannot replace it.
            let generation = dashboard.begin_worker_connection().await?;
            tokio::spawn(async move {
                if let Err(error) = handle_worker_connection(stream, dashboard, generation).await {
                    error!("Worker observation connection stopped: {}", error);
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
    dashboard: Arc<WebDashboard>,
    generation: u64,
) -> Result<()> {
    let result = read_worker_messages(&mut stream, &dashboard, generation).await;
    dashboard
        .end_worker_connection(generation, result.is_err())
        .await;
    result
}

async fn read_worker_messages(
    stream: &mut UnixStream,
    dashboard: &WebDashboard,
    generation: u64,
) -> Result<()> {
    loop {
        // Read the first byte separately to distinguish an orderly EOF from a
        // truncated length prefix. Bound allocation before trusting IPC input.
        let mut length = [0_u8; 4];
        let count = stream
            .read(&mut length[..1])
            .await
            .map_err(|_| anyhow::anyhow!("Worker IPC header read failed"))?;
        if count == 0 {
            return Ok(());
        }
        stream
            .read_exact(&mut length[1..])
            .await
            .map_err(|_| anyhow::anyhow!("Worker IPC header is truncated"))?;
        let length = u32::from_be_bytes(length) as usize;
        anyhow::ensure!(
            (1..=65_536).contains(&length),
            "Worker IPC frame length is invalid"
        );
        let mut buffer = vec![0_u8; length];
        stream
            .read_exact(&mut buffer)
            .await
            .map_err(|_| anyhow::anyhow!("Worker IPC frame is truncated"))?;
        let message: WorkerMessage = serde_json::from_slice(&buffer)
            .map_err(|_| anyhow::anyhow!("Worker IPC message is invalid"))?;
        dashboard.record_worker_message(generation, message).await?;
    }
}

#[cfg(test)]
#[path = "connection_tests.rs"]
mod tests;
