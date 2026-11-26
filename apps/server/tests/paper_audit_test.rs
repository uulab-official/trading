use std::path::PathBuf;
use trading_server::core::{
    paper_sim::{self, ReplayInput},
    paper_store::MAX_EVENTS,
};
use uuid::Uuid;

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("paper-audit-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn scenario() -> ReplayInput {
    serde_json::from_str(include_str!("../config/paper_replay_example.json")).unwrap()
}

#[tokio::test]
async fn audit_reconstructs_existing_experiment_and_reports_checkpoint_and_capacity() {
    let dir = TempDir::new();
    let scenario = scenario();
    let persisted = paper_sim::persist(&scenario, &dir.0).await.unwrap();
    let audit = paper_sim::audit_persisted(&scenario, &dir.0).await.unwrap();
    assert_eq!(audit.replay, persisted);
    assert!(!audit.live_orders_enabled);
    assert_eq!(audit.accounts.len(), persisted.results.len());
    for (account, result) in audit.accounts.iter().zip(&persisted.results) {
        assert_eq!(account.strategy_id, result.id);
        assert_eq!(account.checkpoint.head.sequence, result.journal_events as i64);
        assert_eq!(account.remaining_event_capacity, MAX_EVENTS - result.journal_events as i64);
        assert_eq!(account.checkpoint.head.hash.len(), 64);
    }
    assert_eq!(paper_sim::audit_persisted(&scenario, &dir.0).await.unwrap(), audit);
    let html = paper_sim::audit_html(&audit).unwrap();
    assert!(html.contains("Verified persisted accounts"));
    assert!(html.contains("No current-market health claim"));
    assert!(html.contains(&audit.accounts[0].checkpoint.head.hash));
}

#[tokio::test]
async fn missing_account_never_gets_created_by_audit() {
    let dir = TempDir::new();
    let scenario = scenario();
    assert!(paper_sim::audit_persisted(&scenario, &dir.0).await.is_err());
    assert_eq!(std::fs::read_dir(&dir.0).unwrap().count(), 0);
    paper_sim::persist(&scenario, &dir.0).await.unwrap();
    let path = dir.0.join(format!("paper-{}.sqlite", scenario.strategies.last().unwrap().id));
    std::fs::remove_file(&path).unwrap();
    assert!(paper_sim::audit_persisted(&scenario, &dir.0).await.is_err());
    assert!(!path.exists());
}

#[tokio::test]
async fn changed_scenario_and_modified_journal_fail_audit_without_repair() {
    let dir = TempDir::new();
    let scenario = scenario();
    paper_sim::persist(&scenario, &dir.0).await.unwrap();
    let mut changed = scenario.clone();
    changed.bars[0].volume = "19".into();
    assert!(paper_sim::audit_persisted(&changed, &dir.0).await.is_err());
    let path = dir.0.join(format!("paper-{}.sqlite", scenario.strategies[0].id));
    std::fs::remove_file(&path).unwrap();
    std::fs::write(&path, b"not a database").unwrap();
    assert!(paper_sim::audit_persisted(&scenario, &dir.0).await.is_err());
    assert_eq!(std::fs::read(&path).unwrap(), b"not a database");
}

#[test]
fn cli_audit_fails_for_missing_accounts_without_creating_them() {
    let dir = TempDir::new();
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("config/paper_replay_example.json");
    let run = |subcommand: &str| {
        std::process::Command::new(env!("CARGO_BIN_EXE_trading-server"))
            .env_clear().arg(subcommand).arg("--input").arg(&fixture)
            .arg("--output-dir").arg(&dir.0).output().unwrap()
    };
    let result = run("paper-audit");
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
    assert_eq!(std::fs::read_dir(&dir.0).unwrap().count(), 0);
    let result = run("paper-replay");
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let result = run("paper-audit");
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let output: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(output["mode"], "offline_paper_replay_audit");
    assert_eq!(output["live_orders_enabled"], false);
}
