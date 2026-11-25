use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use std::{path::{Path, PathBuf}, time::Duration};
use trading_server::core::{
    paper_ledger::{Action, ApplyOutcome, JournalEvent, OrderTerms, PaperConfig, Side},
    paper_store::{JournalCheckpoint, PaperStore, MAX_BACKUP_BYTES},
};
use uuid::Uuid;

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("paper-backup-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn config() -> PaperConfig {
    PaperConfig {
        quote_asset: "USDT".into(),
        initial_cash: 1000,
        max_order_cost: 500,
        max_asset_cost: 800,
        max_assets: 2,
        max_realized_loss: 100,
    }
}

fn buy(id: &str, price: u64) -> JournalEvent {
    JournalEvent {
        event_id: id.into(),
        action: Action::Submit(OrderTerms {
            order_id: id.into(), asset: "BTC".into(), side: Side::Buy,
            lots: 3, limit_price: price, max_fee: 10,
        }),
    }
}

fn fill() -> JournalEvent {
    JournalEvent {
        event_id: "fill".into(),
        action: Action::Fill { order_id: "buy".into(), lots: 1, price: 90, fee: 3 },
    }
}

fn halt() -> JournalEvent {
    JournalEvent { event_id: "halt".into(), action: Action::HaltBuys }
}

async fn rollback_to_genesis(path: &Path, genesis: &JournalCheckpoint) {
    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(SqliteConnectOptions::new().filename(path)
            .create_if_missing(false).busy_timeout(Duration::from_secs(5)))
        .await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("DELETE FROM paper_events").execute(&mut *tx).await.unwrap();
    sqlx::query("UPDATE paper_meta SET head_seq = 0, head_hash = ? WHERE id = 1")
        .bind(&genesis.head.hash).execute(&mut *tx).await.unwrap();
    tx.commit().await.unwrap();
    pool.close().await;
}

#[tokio::test]
async fn backup_restore_preserves_partial_fills_halt_and_idempotency() {
    let dir = TempDir::new();
    let source = dir.path("source.sqlite");
    let destination = dir.path("restored.sqlite");
    let store = PaperStore::create(&source, "account-a", config()).await.unwrap();
    store.apply(buy("buy", 100)).await.unwrap();
    store.apply(fill()).await.unwrap();
    let partial = store.export_backup().await.unwrap();
    let partial_state = store.audit().await.unwrap();
    assert_eq!(partial_state.account.cash, 907);
    assert_eq!(partial_state.account.reserved_cash, 207);
    let restored = PaperStore::restore_backup(
        &destination, "account-a", config(), &partial.bytes, partial.checkpoint.clone(),
    ).await.unwrap();
    assert_eq!(restored.audit().await.unwrap(), partial_state);
    assert_eq!(restored.apply(fill()).await.unwrap(), ApplyOutcome::Duplicate);
    restored.close().await;
    store.apply(halt()).await.unwrap();
    let stopped = store.export_backup().await.unwrap();
    let expected = store.audit().await.unwrap();
    let halted_path = dir.path("halted.sqlite");
    let restored = PaperStore::restore_backup(
        &halted_path, "account-a", config(), &stopped.bytes, stopped.checkpoint.clone(),
    ).await.unwrap();
    assert_eq!(restored.audit().await.unwrap(), expected);
    assert!(expected.account.buys_halted);
    assert_eq!(expected.account.reserved_cash, 0);
    assert_eq!(restored.export_backup().await.unwrap().bytes, stopped.bytes);
    assert!(restored.apply(buy("new", 100)).await.is_err());
    restored.close().await;
    let reopened = PaperStore::open_with_checkpoint(
        &halted_path, "account-a", config(), stopped.checkpoint,
    ).await.unwrap();
    assert_eq!(reopened.audit().await.unwrap(), expected);
    reopened.close().await;
    store.close().await;
}

#[tokio::test]
async fn newer_history_extending_trusted_prefix_is_accepted() {
    let dir = TempDir::new();
    let path = dir.path("source.sqlite");
    let source = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let genesis = source.checkpoint().await.unwrap();
    source.apply(buy("buy", 100)).await.unwrap();
    let prefix = source.checkpoint().await.unwrap();
    source.apply(fill()).await.unwrap();
    let latest = source.checkpoint().await.unwrap();
    source.close().await;
    for floor in [genesis, prefix] {
        let store = PaperStore::open_with_checkpoint(&path, "account-a", config(), floor)
            .await.unwrap();
        assert_eq!(store.checkpoint().await.unwrap(), latest);
        assert_eq!(store.apply(fill()).await.unwrap(), ApplyOutcome::Duplicate);
        store.close().await;
    }
}

#[tokio::test]
async fn independently_valid_fork_and_old_database_fail_checkpoint() {
    let dir = TempDir::new();
    let first = PaperStore::create(&dir.path("one.sqlite"), "account-a", config()).await.unwrap();
    let second_path = dir.path("fork.sqlite");
    let fork = PaperStore::create(&second_path, "account-a", config()).await.unwrap();
    let older_path = dir.path("old.sqlite");
    let older = PaperStore::create(&older_path, "account-a", config()).await.unwrap();
    first.apply(buy("buy", 100)).await.unwrap();
    fork.apply(buy("buy", 101)).await.unwrap();
    let floor = first.checkpoint().await.unwrap();
    fork.close().await;
    older.close().await;
    assert!(PaperStore::open_with_checkpoint(&second_path, "account-a", config(), floor.clone()).await.is_err());
    assert!(PaperStore::open_with_checkpoint(&older_path, "account-a", config(), floor).await.is_err());
    first.close().await;
}

#[tokio::test]
async fn trusted_floor_is_rechecked_before_audit_export_and_append() {
    let dir = TempDir::new();
    let path = dir.path("source.sqlite");
    let initial = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let genesis = initial.checkpoint().await.unwrap();
    initial.apply(buy("buy", 100)).await.unwrap();
    let checkpoint = initial.checkpoint().await.unwrap();
    initial.close().await;
    let store = PaperStore::open_with_checkpoint(&path, "account-a", config(), checkpoint).await.unwrap();
    rollback_to_genesis(&path, &genesis).await;
    assert!(store.audit().await.is_err());
    assert!(store.export_backup().await.is_err());
    assert!(store.apply(buy("new", 100)).await.is_err());
    store.close().await;
    let unpinned = PaperStore::open(&path, "account-a", config()).await.unwrap();
    assert_eq!(unpinned.checkpoint().await.unwrap(), genesis);
    unpinned.close().await;
}

#[tokio::test]
async fn invalid_backup_is_rejected_before_creating_destination() {
    let dir = TempDir::new();
    let source = PaperStore::create(&dir.path("source.sqlite"), "account-a", config()).await.unwrap();
    let empty = source.export_backup().await.unwrap();
    source.apply(buy("buy", 100)).await.unwrap();
    let backup = source.export_backup().await.unwrap();
    let path = dir.path("destination.sqlite");

    let original = std::str::from_utf8(&backup.bytes).unwrap();
    let mut malformed = vec![
        original.replace("\"version\":1", "\"version\":2").into_bytes(),
        original.replace("\"limit_price\":100", "\"limit_price\":101").into_bytes(),
        original.replace("\"event_id\":\"buy\"", "\"event_id\":\"other\"").into_bytes(),
        original.replace("\"events\":[", "\"unexpected\":0,\"events\":[").into_bytes(),
        vec![b' '; MAX_BACKUP_BYTES + 1],
    ];
    let mut trailing = backup.bytes.clone();
    trailing.push(b' ');
    malformed.push(trailing);
    for bytes in malformed {
        assert!(PaperStore::restore_backup(&path, "account-a", config(), &bytes, backup.checkpoint.clone()).await.is_err());
        assert!(!path.exists());
    }
    assert!(PaperStore::restore_backup(&path, "account-a", config(), &empty.bytes, backup.checkpoint.clone()).await.is_err());
    assert!(PaperStore::restore_backup(&path, "account-a", config(), &backup.bytes, empty.checkpoint).await.is_err());
    assert!(PaperStore::restore_backup(&path, "wrong", config(), &backup.bytes, backup.checkpoint.clone()).await.is_err());
    let mut changed = config();
    changed.initial_cash += 1;
    assert!(PaperStore::restore_backup(&path, "account-a", changed, &backup.bytes, backup.checkpoint).await.is_err());
    assert!(!path.exists());
    source.close().await;
}

#[tokio::test]
async fn restore_never_replaces_existing_file_and_empty_backup_round_trips() {
    let dir = TempDir::new();
    let path = dir.path("source.sqlite");
    let source = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let backup = source.export_backup().await.unwrap();
    let occupied = dir.path("existing.sqlite");
    std::fs::write(&occupied, b"do not overwrite").unwrap();
    assert!(PaperStore::restore_backup(&occupied, "account-a", config(), &backup.bytes, backup.checkpoint.clone()).await.is_err());
    assert_eq!(std::fs::read(&occupied).unwrap(), b"do not overwrite");
    let empty_path = dir.path("empty.sqlite");
    let restored = PaperStore::restore_backup(&empty_path, "account-a", config(), &backup.bytes, backup.checkpoint).await.unwrap();
    assert_eq!(restored.audit().await.unwrap(), source.audit().await.unwrap());
    restored.close().await;
    source.close().await;
}

#[tokio::test]
async fn concurrent_export_captures_one_committed_head() {
    let dir = TempDir::new();
    let path = dir.path("source.sqlite");
    let source = PaperStore::create(&path, "account-a", config()).await.unwrap();
    source.apply(buy("buy", 100)).await.unwrap();
    let before = source.audit().await.unwrap();
    let writer = PaperStore::open(&path, "account-a", config()).await.unwrap();
    let (export, applied) = tokio::join!(source.export_backup(), writer.apply(fill()));
    let export = export.unwrap();
    assert_eq!(applied.unwrap(), ApplyOutcome::Applied);
    let after = source.audit().await.unwrap();
    let target = dir.path("restore.sqlite");
    let restored = PaperStore::restore_backup(&target, "account-a", config(), &export.bytes, export.checkpoint).await.unwrap();
    let snapshot = restored.audit().await.unwrap();
    assert!(snapshot == before || snapshot == after);
    restored.close().await;
    writer.close().await;
    source.close().await;
}

#[tokio::test]
async fn malformed_or_wrong_account_checkpoint_is_rejected() {
    let dir = TempDir::new();
    let path = dir.path("source.sqlite");
    let source = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let good = source.checkpoint().await.unwrap();
    source.close().await;
    let mut bad = good.clone();
    bad.ledger_id = "other".into();
    assert!(PaperStore::open_with_checkpoint(&path, "account-a", config(), bad).await.is_err());
    let mut bad = good.clone();
    bad.head.hash = "x".repeat(64);
    assert!(PaperStore::open_with_checkpoint(&path, "account-a", config(), bad).await.is_err());
    let mut bad = good;
    bad.head.sequence = -1;
    assert!(PaperStore::open_with_checkpoint(&path, "account-a", config(), bad).await.is_err());
}
