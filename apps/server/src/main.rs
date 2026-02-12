mod core;
mod exchange;
mod master;
mod strategy;
mod web;
mod worker;

use anyhow::Result;
use clap::Parser;
use std::sync::Arc;
use tracing::{error, info};

#[derive(Parser, Debug)]
#[command(name = "trading-server")]
#[command(about = "High-performance crypto trading engine")]
enum Cli {
    Master {
        #[arg(short, long, default_value = "config")]
        config_path: String,
        #[arg(short = 'p', long, default_value = "8080")]
        web_port: u16,
        #[arg(short = 'k', long, default_value = "9000")]
        worker_port: u16,
    },
    Worker {
        #[arg(short, long, default_value = "config")]
        config_path: String,
        #[arg(short, long)]
        master_socket: String,
    },
    Deploy {
        #[arg(short, long)]
        binary_path: String,
    },
    Migrate,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();

    let cli = Cli::parse();

    match cli {
        Cli::Master {
            config_path,
            web_port,
            worker_port,
        } => {
            info!("Starting Trading Server Master process");
            let master = master::MasterProcess::new(config_path, web_port, worker_port).await?;
            Arc::clone(&master).run().await?;
        }
        Cli::Worker {
            config_path,
            master_socket,
        } => {
            info!("Starting Trading Server Worker process");
            let worker = worker::WorkerProcess::new(config_path, master_socket).await?;
            worker.run().await?;
        }
        Cli::Deploy { binary_path } => {
            info!("Initiating zero-downtime deployment");
            master::deploy::execute_deploy(binary_path).await?;
        }
        Cli::Migrate => {
            info!("Running database migrations");
            core::database::run_migrations().await?;
        }
    }

    Ok(())
}
