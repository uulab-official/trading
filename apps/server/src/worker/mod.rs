use anyhow::Result;
use tracing::info;

pub struct WorkerProcess {
    config_path: String,
    master_socket: String,
}

impl WorkerProcess {
    pub async fn new(config_path: String, master_socket: String) -> Result<Self> {
        Ok(Self {
            config_path,
            master_socket,
        })
    }
    
    pub async fn run(self) -> Result<()> {
        info!(
            master_socket = self.master_socket,
            "Worker process running"
        );
        
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
        }
    }
}