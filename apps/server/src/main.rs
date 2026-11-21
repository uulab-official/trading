use anyhow::Result;
use clap::Parser;
use std::sync::Arc;
use tracing::info;
use trading_server::{core, master, worker};

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

#[cfg(test)]
mod tests {
    use super::Cli;
    use clap::{error::ErrorKind, Parser};

    #[test]
    fn master_preserves_defaults() {
        let cli = Cli::try_parse_from(["trading-server", "master"]).unwrap();
        match cli {
            Cli::Master {
                config_path,
                web_port,
                worker_port,
            } => {
                assert_eq!(config_path, "config");
                assert_eq!(web_port, 8080);
                assert_eq!(worker_port, 9000);
            }
            parsed => panic!("Expected master command, got {parsed:?}"),
        }
    }

    #[test]
    fn master_accepts_explicit_configuration_and_ports() {
        for arguments in [
            [
                "trading-server",
                "master",
                "--config-path",
                "paper-config",
                "--web-port",
                "18080",
                "--worker-port",
                "19000",
            ],
            [
                "trading-server",
                "master",
                "-c",
                "paper-config",
                "-p",
                "18080",
                "-k",
                "19000",
            ],
        ] {
            match Cli::try_parse_from(arguments).unwrap() {
                Cli::Master {
                    config_path,
                    web_port,
                    worker_port,
                } => {
                    assert_eq!(config_path, "paper-config");
                    assert_eq!(web_port, 18080);
                    assert_eq!(worker_port, 19000);
                }
                parsed => panic!("Expected master command, got {parsed:?}"),
            }
        }
    }

    #[test]
    fn worker_requires_a_master_socket() {
        let error = Cli::try_parse_from(["trading-server", "worker"]).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::MissingRequiredArgument);
    }

    #[test]
    fn worker_preserves_default_and_explicit_configuration() {
        let cases: &[(&[&str], &str)] = &[
            (
                &[
                    "trading-server",
                    "worker",
                    "--master-socket",
                    "/tmp/trading-test.sock",
                ],
                "config",
            ),
            (
                &[
                    "trading-server",
                    "worker",
                    "-c",
                    "paper-config",
                    "-m",
                    "/tmp/trading-test.sock",
                ],
                "paper-config",
            ),
        ];
        for &(arguments, expected_config) in cases {
            match Cli::try_parse_from(arguments.iter().copied()).unwrap() {
                Cli::Worker {
                    config_path,
                    master_socket,
                } => {
                    assert_eq!(config_path, expected_config);
                    assert_eq!(master_socket, "/tmp/trading-test.sock");
                }
                parsed => panic!("Expected worker command, got {parsed:?}"),
            }
        }
    }

    #[test]
    fn deploy_requires_a_binary_path() {
        let error = Cli::try_parse_from(["trading-server", "deploy"]).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::MissingRequiredArgument);
    }

    #[test]
    fn deploy_accepts_a_binary_path() {
        for flag in ["--binary-path", "-b"] {
            match Cli::try_parse_from(["trading-server", "deploy", flag, "candidate-binary"])
                .unwrap()
            {
                Cli::Deploy { binary_path } => assert_eq!(binary_path, "candidate-binary"),
                parsed => panic!("Expected deploy command, got {parsed:?}"),
            }
        }
    }

    #[test]
    fn migrate_parses_without_running_migrations() {
        assert!(matches!(
            Cli::try_parse_from(["trading-server", "migrate"]).unwrap(),
            Cli::Migrate
        ));
    }

    #[test]
    fn help_is_available_for_root_and_every_subcommand() {
        for command in [
            None,
            Some("master"),
            Some("worker"),
            Some("deploy"),
            Some("migrate"),
        ] {
            let mut arguments = vec!["trading-server"];
            if let Some(command) = command {
                arguments.push(command);
            }
            arguments.push("--help");
            let error = Cli::try_parse_from(arguments).unwrap_err();
            assert_eq!(error.kind(), ErrorKind::DisplayHelp);
            assert_eq!(error.exit_code(), 0);
        }
    }
}
