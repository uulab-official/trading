use std::path::PathBuf;
use trading_server::core::{
    paper_ledger::{Action, JournalEvent, PaperLedger},
    paper_sim::{self, AttemptStatus, ForwardInput, ForwardMode, ReplayBar},
    paper_store::PaperStore,
};
use uuid::Uuid;

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("paper-forward-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn fixture() -> ForwardInput {
    serde_json::from_str(include_str!("../config/paper_forward_example.json")).unwrap()
}

fn prefix(input: &ForwardInput, bars: usize) -> ForwardInput {
    let mut result = input.clone();
    result.scenario.bars.truncate(bars);
    result
}

#[tokio::test]
async fn delayed_signal_survives_restart_and_extension_matches_full_reconstruction() {
    let dir = TempDir::new();
    let fresh = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let short = prefix(&input, 3);
    let initial = paper_sim::forward(&short, &dir.0, ForwardMode::Initialize).await.unwrap();
    assert_eq!(initial.replay.results[0].attempts[0].status, AttemptStatus::AwaitingNextBar);
    assert_eq!(initial.replay.results[0].fills, 0);
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Audit).await.is_err());

    input.archive_head_sha256 = "c".repeat(64);
    let advanced = paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.unwrap();
    assert_eq!(initial.session_sha256, advanced.session_sha256);
    assert!(!advanced.live_orders_enabled);
    assert_eq!(advanced.replay.results[0].attempts[0].filled_lots, 3);
    assert_eq!(advanced.replay.results[0].attempts[0].execution_ms, Some(input.scenario.bars[3].open_ms));
    assert_eq!(advanced.replay.results[0].attempts[0].fee_atoms, 4);
    assert!(advanced.accounts[0].checkpoint.head.sequence > initial.accounts[0].checkpoint.head.sequence);
    let full = paper_sim::forward(&input, &fresh.0, ForwardMode::Initialize).await.unwrap();
    assert_eq!(advanced, full);
    assert_eq!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.unwrap(), advanced);
    assert_eq!(paper_sim::forward(&input, &dir.0, ForwardMode::Audit).await.unwrap(), advanced);
    assert!(paper_sim::forward(&short, &dir.0, ForwardMode::Advance).await.is_err());
    let html = paper_sim::forward_html(&advanced).unwrap();
    assert!(html.contains("Verified forward reconstruction"));
    assert!(html.contains("not live fills"));
}

#[tokio::test]
async fn warmup_revisions_and_configuration_or_source_changes_are_rejected() {
    let dir = TempDir::new();
    let input = fixture();
    let short = prefix(&input, 4);
    let original = paper_sim::forward(&short, &dir.0, ForwardMode::Initialize).await.unwrap();
    let mut revision = input.clone();
    revision.scenario.bars[0].volume = "19".into();
    assert!(paper_sim::forward(&revision, &dir.0, ForwardMode::Advance).await.is_err());
    let mut source = input.clone();
    source.source_id = "d".repeat(64);
    assert!(paper_sim::forward(&source, &dir.0, ForwardMode::Advance).await.is_err());
    let mut rules = input.clone();
    rules.scenario.execution.fee_bps += 1;
    assert!(paper_sim::forward(&rules, &dir.0, ForwardMode::Advance).await.is_err());
    let mut units = input.clone();
    units.scenario.quote_scale = 1;
    assert!(paper_sim::forward(&units, &dir.0, ForwardMode::Advance).await.is_err());
    let mut start = input.clone();
    start.scenario.bars.remove(0);
    assert!(paper_sim::forward(&start, &dir.0, ForwardMode::Advance).await.is_err());
    assert_eq!(paper_sim::forward(&short, &dir.0, ForwardMode::Audit).await.unwrap(), original);
}

#[tokio::test]
async fn missing_strategy_is_not_silently_recreated_and_preflight_protects_other_accounts() {
    let dir = TempDir::new();
    let input = fixture();
    let short = prefix(&input, 4);
    let initial = paper_sim::forward(&short, &dir.0, ForwardMode::Initialize).await.unwrap();
    let missing = dir.0.join("forward-rsi.sqlite");
    std::fs::remove_file(&missing).unwrap();
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.is_err());
    assert!(!missing.exists());
    let first = &initial.accounts[0].checkpoint;
    let store = PaperStore::open_read_only(&dir.0.join("forward-ma.sqlite"),
        &first.ledger_id, input.scenario.account.clone()).await.unwrap();
    assert_eq!(&store.checkpoint().await.unwrap(), first);
    store.close().await;
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Initialize).await.is_err());
}

#[tokio::test]
async fn incomplete_bar_history_is_refused_even_if_it_is_an_exact_event_prefix() {
    let dir = TempDir::new();
    let damaged = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let initial = paper_sim::forward(&input, &dir.0, ForwardMode::Initialize).await.unwrap();
    let id = &initial.accounts[0].checkpoint.ledger_id;
    let store = PaperStore::open_read_only(&dir.0.join("forward-ma.sqlite"),
        id, input.scenario.account.clone()).await.unwrap();
    let backup = store.export_backup().await.unwrap();
    store.close().await;
    let json: serde_json::Value = serde_json::from_slice(&backup.bytes).unwrap();
    let mut events: Vec<JournalEvent> = serde_json::from_value(json["events"].clone()).unwrap();
    assert!(matches!(events.last().map(|e| &e.action), Some(Action::SealReplayBar { .. })));
    events.pop();
    let ledger = PaperLedger::replay(input.scenario.account.clone(), events).unwrap();
    let path = damaged.0.join("forward-ma.sqlite");
    let store = PaperStore::persist_replay(&path, id, ledger).await.unwrap();
    let before = store.checkpoint().await.unwrap();
    store.close().await;
    assert!(paper_sim::forward(&input, &damaged.0, ForwardMode::Advance).await.is_err());
    let store = PaperStore::open_read_only(&path, id, input.scenario.account.clone()).await.unwrap();
    assert_eq!(store.checkpoint().await.unwrap(), before);
    store.close().await;
}

#[tokio::test]
async fn multi_batch_extension_has_same_head_as_fresh_full_session() {
    let dir = TempDir::new();
    let fresh = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let initial = paper_sim::forward(&prefix(&input, 3), &dir.0, ForwardMode::Initialize).await.unwrap();
    for index in 9..90 {
        let price = if index % 2 == 0 { "110" } else { "100" };
        let open_ms = input.scenario.bars.last().unwrap().open_ms + 60_000;
        input.scenario.bars.push(ReplayBar {
            open_ms, open: price.into(), high: price.into(),
            low: price.into(), close: price.into(), volume: "20".into(),
        });
    }
    let extended = paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.unwrap();
    assert!(extended.accounts[0].checkpoint.head.sequence - initial.accounts[0].checkpoint.head.sequence > 128);
    assert_eq!(extended, paper_sim::forward(&input, &fresh.0, ForwardMode::Initialize).await.unwrap());
}

#[tokio::test]
async fn invalid_input_and_missing_account_have_no_creation_side_effects() {
    let dir = TempDir::new();
    let input = fixture();
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.is_err());
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Audit).await.is_err());
    let mut invalid = input.clone();
    invalid.archive_head_sha256 = "bad".into();
    assert!(paper_sim::forward(&invalid, &dir.0, ForwardMode::Initialize).await.is_err());
    let mut gap = input;
    gap.scenario.bars[3].open_ms += 60_000;
    assert!(paper_sim::forward(&gap, &dir.0, ForwardMode::Initialize).await.is_err());
    assert_eq!(std::fs::read_dir(&dir.0).unwrap().count(), 0);
}

#[test]
fn cli_initializes_and_audits_only_explicit_local_session() {
    let dir = TempDir::new();
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("config/paper_forward_example.json");
    let run = |flags: &[&str]| {
        std::process::Command::new(env!("CARGO_BIN_EXE_trading-server"))
            .env_clear().arg("paper-forward").arg("--input").arg(&fixture)
            .arg("--output-dir").arg(&dir.0).args(flags).output().unwrap()
    };
    assert!(!run(&[]).status.success());
    assert!(!run(&["--initialize", "--audit-only"]).status.success());
    let initialized = run(&["--initialize"]);
    assert!(initialized.status.success(), "{}", String::from_utf8_lossy(&initialized.stderr));
    let audited = run(&["--audit-only"]);
    assert!(audited.status.success(), "{}", String::from_utf8_lossy(&audited.stderr));
    assert_eq!(initialized.stdout, audited.stdout);
    assert!(String::from_utf8(run(&["--audit-only", "--html"]).stdout).unwrap().contains("Verified forward reconstruction"));
}

#[tokio::test]
async fn trusted_checkpoint_rejects_rolled_back_complete_prefix() {
    let dir = TempDir::new();
    let newer = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let short = prefix(&input, 3);
    let initial = paper_sim::forward(&short, &dir.0, ForwardMode::Initialize).await.unwrap();
    let full = paper_sim::forward(&input, &newer.0, ForwardMode::Initialize).await.unwrap();
    input.trusted_checkpoints = vec![full.accounts[0].checkpoint.clone()];
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.is_err());
    assert_eq!(paper_sim::forward(&short, &dir.0, ForwardMode::Audit).await.unwrap(), initial);
    input.trusted_checkpoints = vec![initial.accounts[0].checkpoint.clone()];
    assert_eq!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.unwrap(), full);
    input.trusted_checkpoints[0].head.hash = "0".repeat(64);
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Audit).await.is_err());
}

#[tokio::test]
async fn extended_session_exceeds_old_bar_and_event_limits_without_resetting_history() {
    let dir = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let initial = paper_sim::forward(&prefix(&input, 3), &dir.0, ForwardMode::Initialize).await.unwrap();
    input.trusted_checkpoints = vec![initial.accounts[0].checkpoint.clone()];
    while input.scenario.bars.len() < 700 {
        let open_ms = input.scenario.bars.last().unwrap().open_ms + 60_000;
        input.scenario.bars.push(ReplayBar {
            open_ms, open: "100".into(), high: "100".into(), low: "100".into(),
            close: "100".into(), volume: "20".into(),
        });
    }
    assert!(paper_sim::simulate(&input.scenario).is_err()); // legacy research cap is unchanged
    let extended = paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.unwrap();
    assert_eq!(extended.input_bar_count, 700);
    assert_eq!(extended.max_bar_count, paper_sim::MAX_FORWARD_BARS);
    assert_eq!(extended.max_event_count, trading_server::core::paper_store::MAX_EVENTS);
    assert!(extended.accounts[0].checkpoint.head.sequence > 2048);
    assert_eq!(paper_sim::forward(&input, &dir.0, ForwardMode::Audit).await.unwrap(), extended);
}

#[tokio::test]
async fn error_in_later_sql_chunk_rolls_back_entire_account_extension() {
    use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
    let dir = TempDir::new();
    let mut input = fixture();
    input.scenario.strategies.truncate(1);
    let short = prefix(&input, 3);
    let initial = paper_sim::forward(&short, &dir.0, ForwardMode::Initialize).await.unwrap();
    for _ in 9..90 {
        let open_ms = input.scenario.bars.last().unwrap().open_ms + 60_000;
        input.scenario.bars.push(ReplayBar {
            open_ms, open: "100".into(), high: "100".into(), low: "100".into(),
            close: "100".into(), volume: "20".into(),
        });
    }
    let path = dir.0.join("forward-ma.sqlite");
    let pool = SqlitePoolOptions::new().max_connections(1)
        .connect_with(SqliteConnectOptions::new().filename(&path).create_if_missing(false))
        .await.unwrap();
    let failure_seq = initial.accounts[0].checkpoint.head.sequence + 129;
    sqlx::query(&format!(
        "CREATE TRIGGER fail_extension BEFORE INSERT ON paper_events WHEN NEW.seq = {failure_seq} BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )).execute(&pool).await.unwrap();
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.is_err());
    assert_eq!(paper_sim::forward(&short, &dir.0, ForwardMode::Audit).await.unwrap(), initial);
    sqlx::query("DROP TRIGGER fail_extension").execute(&pool).await.unwrap();
    pool.close().await;
    assert!(paper_sim::forward(&input, &dir.0, ForwardMode::Advance).await.is_ok());
}
