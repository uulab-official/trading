use std::path::Path;
use tracing::{error, info};

pub async fn execute_deploy(binary_path: String) -> anyhow::Result<()> {
    info!("Deploying new binary from: {}", binary_path);

    if !Path::new(&binary_path).exists() {
        return Err(anyhow::anyhow!(
            "Binary not found at path: {}",
            binary_path
        ));
    }

    let master_socket = std::path::PathBuf::from("/tmp/trading-master.sock");
    if !master_socket.exists() {
        return Err(anyhow::anyhow!(
            "Master process not running (socket not found)"
        ));
    }

    match send_deploy_command(&binary_path).await {
        Ok(_) => {
            info!("Deployment command sent to master process");
            Ok(())
        }
        Err(e) => {
            error!("Failed to send deployment command: {}", e);
            Err(e)
        }
    }
}

async fn send_deploy_command(binary_path: &str) -> anyhow::Result<()> {
    use tokio::io::AsyncWriteExt;
    use tokio::net::UnixStream;

    let mut stream = UnixStream::connect("/tmp/trading-master.sock").await?;

    let command = format!("DEPLOY:{}", binary_path);
    stream.write_all(command.as_bytes()).await?;

    info!("Hot-swap command sent to master");

    Ok(())
}