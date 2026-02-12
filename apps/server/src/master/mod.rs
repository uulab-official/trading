use anyhow::Result;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use tokio::net::{TcpListener, UnixListener};
use tokio::sync::RwLock;
use tracing::{error, info, warn};

pub mod deploy;
pub mod web;

pub struct MasterProcess {
    config_path: String,
    web_port: u16,
    worker_port: u16,
    socket_path: PathBuf,
    current_worker: Arc<RwLock<Option<Child>>>,
}

impl MasterProcess {
    pub async fn new(config_path: String, web_port: u16, worker_port: u16) -> Result<Self> {
        let socket_path = PathBuf::from("/tmp/trading-master.sock");

        if socket_path.exists() {
            std::fs::remove_file(&socket_path)?;
        }

        Ok(Self {
            config_path,
            web_port,
            worker_port,
            socket_path,
            current_worker: Arc::new(RwLock::new(None)),
        })
    }

    pub async fn run(mut self) -> Result<()> {
        info!(
            web_port = self.web_port,
            worker_port = self.worker_port,
            socket = %self.socket_path.display(),
            "Master process starting"
        );

        let web_listener = TcpListener::bind(format!("0.0.0.0:{}", self.web_port)).await?;
        let unix_listener = UnixListener::bind(&self.socket_path)?;

        info!("Web server listening on port {}", self.web_port);
        info!("Unix socket created at {:?}", self.socket_path);

        self.start_worker().await?;

        tokio::select! {
            result = self.run_web_server(web_listener) => {
                if let Err(e) = result {
                    error!("Web server error: {}", e);
                }
            }
            result = self.handle_unix_socket(unix_listener) => {
                if let Err(e) = result {
                    error!("Unix socket error: {}", e);
                }
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
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        info!("Worker process started with PID: {:?}", child.id());
        *worker = Some(child);

        Ok(())
    }

    async fn run_web_server(&self, listener: TcpListener) -> Result<()> {
        info!("Web server started");

        loop {
            let (stream, addr) = listener.accept().await?;
            info!("New web connection from: {:?}", addr);

            tokio::spawn(async move {
                let _ = stream;
            });
        }
    }

    async fn handle_unix_socket(&self, listener: UnixListener) -> Result<()> {
        info!("Unix socket handler started");

        loop {
            let (stream, _) = listener.accept().await?;
            let worker = self.current_worker.clone();

            tokio::spawn(async move {
                if let Err(e) = handle_worker_connection(stream, worker).await {
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
    _stream: tokio::net::UnixStream,
    _worker: Arc<RwLock<Option<Child>>>,
) -> Result<()> {
    Ok(())
}