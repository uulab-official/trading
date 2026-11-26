use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use std::{path::{Path, PathBuf}, time::Duration};
use trading_server::core::{
    paper_ledger::{Action, ApplyOutcome, JournalEvent, LedgerError, OrderTerms, PaperConfig, Side},
    paper_store::{PaperStore, MAX_BATCH_EVENTS},
};
use uuid::Uuid;

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("paper-batch-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn db(&self) -> PathBuf {
        self.0.join("paper.sqlite")
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn config() -> PaperConfig {
    PaperConfig {
        quote_asset: "USDT".into(), initial_cash: 1_000, max_order_cost: 1_000,
        max_asset_cost: 1_000, max_assets: 2, max_realized_loss: 100,
    }
}

fn buy(id: &str) -> JournalEvent {
    JournalEvent {
        event_id: id.into(),
        action: Action::Submit(OrderTerms {
            order_id: id.into(), asset: "BTC".into(), side: Side::Buy,
            lots: 3, limit_price: 100, max_fee: 10,
        }),
    }
}

fn fill(id: &str, order: &str, lots: u64) -> JournalEvent {
    JournalEvent {
        event_id: id.into(),
        action: Action::Fill { order_id: order.into(), lots, price: 90, fee: 3 },
    }
}

fn halt() -> JournalEvent {
    JournalEvent { event_id: "stop".into(), action: Action::HaltBuys }
}

async fn sql(path: &Path, statement: &str) {
    let pool = SqlitePoolOptions::new().max_connections(1)
        .connect_with(SqliteConnectOptions::new().filename(path).create_if_missing(false)
            .busy_timeout(Duration::from_secs(5)))
        .await.unwrap();
    sqlx::query(statement).execute(&pool).await.unwrap();
    pool.close().await;
}

#[tokio::test]
async fn batch_is_atomic_and_retry_survives_restart_and_later_events() {
    let dir = TempDir::new();
    let path = dir.db();
    let store = PaperStore::create(&path, "batch-account", config()).await.unwrap();
    let genesis = store.checkpoint().await.unwrap();
    let events = vec![buy("buy"), fill("fill", "buy", 1)];
    let receipt = store.apply_batch(&genesis, &events).await.unwrap();
    assert_eq!(receipt.outcome, ApplyOutcome::Applied);
    assert_eq!(receipt.batch_end, receipt.current_head);
    assert_eq!(receipt.batch_end.head.sequence, 2);
    let state = store.audit().await.unwrap();
    assert_eq!(state.account.cash, 907);
    assert_eq!(state.account.reserved_cash, 207);
    store.apply(halt()).await.unwrap();
    let latest = store.checkpoint().await.unwrap();
    store.close().await;
    let reopened = PaperStore::open_with_checkpoint(
        &path, "batch-account", config(), latest.clone(),
    ).await.unwrap();
    let duplicate = reopened.apply_batch(&genesis, &events).await.unwrap();
    assert_eq!(duplicate.outcome, ApplyOutcome::Duplicate);
    assert_eq!(duplicate.batch_end, receipt.batch_end);
    assert_eq!(duplicate.current_head, latest);
    assert!(reopened.audit().await.unwrap().account.buys_halted);
    reopened.close().await;
}

#[tokio::test]
async fn invalid_later_event_never_commits_valid_prefix() {
    let dir = TempDir::new();
    let store = PaperStore::create(&dir.db(), "batch-account", config()).await.unwrap();
    let head = store.checkpoint().await.unwrap();
    let before = store.audit().await.unwrap();
    let error = store.apply_batch(&head, &[buy("buy"), fill("overfill", "buy", 4)])
        .await.unwrap_err();
    assert_eq!(error.downcast_ref::<LedgerError>(), Some(&LedgerError::FillOutsideTerms));
    assert_eq!(store.audit().await.unwrap(), before);
    store.close().await;
}

#[tokio::test]
async fn insertion_or_head_failure_rolls_back_whole_batch() {
    let dir = TempDir::new();
    let path = dir.db();
    let store = PaperStore::create(&path, "batch-account", config()).await.unwrap();
    let head = store.checkpoint().await.unwrap();
    let before = store.audit().await.unwrap();
    let events = [buy("buy"), fill("fill", "buy", 1)];
    for (name, trigger) in [
        ("fail_insert", "CREATE TRIGGER fail_insert BEFORE INSERT ON paper_events WHEN NEW.seq = 2 BEGIN SELECT RAISE(ABORT, 'injected'); END"),
        ("fail_head", "CREATE TRIGGER fail_head BEFORE UPDATE OF head_seq ON paper_meta WHEN NEW.head_seq != OLD.head_seq BEGIN SELECT RAISE(ABORT, 'injected'); END"),
    ] {
        sql(&path, trigger).await;
        assert!(store.apply_batch(&head, &events).await.is_err());
        assert_eq!(store.audit().await.unwrap(), before);
        sql(&path, &format!("DROP TRIGGER {name}")).await;
    }
    assert_eq!(store.apply_batch(&head, &events).await.unwrap().outcome, ApplyOutcome::Applied);
    store.close().await;
}

#[tokio::test]
async fn stale_partial_reordered_conflicting_or_wrong_account_batches_are_refused() {
    let dir = TempDir::new();
    let store = PaperStore::create(&dir.db(), "batch-account", config()).await.unwrap();
    let head = store.checkpoint().await.unwrap();
    let events = [buy("buy"), fill("fill", "buy", 1)];
    store.apply(events[0].clone()).await.unwrap();
    let before = store.audit().await.unwrap();
    assert!(store.apply_batch(&head, &events).await.is_err());
    assert!(store.apply_batch(&head, &[buy("other")]).await.is_err());
    assert!(store.apply_batch(&head, &[events[1].clone(), events[0].clone()]).await.is_err());
    let mut wrong = head.clone();
    wrong.ledger_id = "other-account".into();
    assert!(store.apply_batch(&wrong, &events).await.is_err());
    let mut fork = head.clone();
    fork.head.hash = "0".repeat(64);
    assert!(store.apply_batch(&fork, &events).await.is_err());
    let current = store.checkpoint().await.unwrap();
    assert!(store.apply_batch(&current, &events).await.is_err());
    assert_eq!(store.audit().await.unwrap(), before);
    store.close().await;
}

#[tokio::test]
async fn empty_oversize_and_repeated_ids_are_refused() {
    let dir = TempDir::new();
    let store = PaperStore::create(&dir.db(), "batch-account", config()).await.unwrap();
    let head = store.checkpoint().await.unwrap();
    assert!(store.apply_batch(&head, &[]).await.is_err());
    assert!(store.apply_batch(&head, &vec![halt(); MAX_BATCH_EVENTS + 1]).await.is_err());
    assert!(store.apply_batch(&head, &[buy("buy"), buy("buy")]).await.is_err());
    assert_eq!(store.checkpoint().await.unwrap(), head);
    store.close().await;
}

#[tokio::test]
async fn competing_writers_cannot_append_different_batches_at_same_head() {
    let dir = TempDir::new();
    let path = dir.db();
    let left = PaperStore::create(&path, "batch-account", config()).await.unwrap();
    let right = PaperStore::open(&path, "batch-account", config()).await.unwrap();
    let head = left.checkpoint().await.unwrap();
    let a = [buy("a"), fill("af", "a", 1)];
    let b = [buy("b"), fill("bf", "b", 1)];
    let (one, two) = tokio::join!(left.apply_batch(&head, &a), right.apply_batch(&head, &b));
    assert_ne!(one.is_ok(), two.is_ok());
    let current = left.audit().await.unwrap();
    assert_eq!(current.head.sequence, 2);
    assert_eq!(current.account.cash, 907);
    assert_eq!(current.account.orders.len(), 1);
    assert_eq!(right.audit().await.unwrap(), current);
    left.close().await;
    right.close().await;
}

#[tokio::test]
async fn concurrent_exact_batch_retries_are_deduplicated() {
    let dir = TempDir::new();
    let path = dir.db();
    let left = PaperStore::create(&path, "batch-account", config()).await.unwrap();
    let right = PaperStore::open(&path, "batch-account", config()).await.unwrap();
    let head = left.checkpoint().await.unwrap();
    let events = [buy("a"), fill("af", "a", 1)];
    let (a, b) = tokio::join!(left.apply_batch(&head, &events), right.apply_batch(&head, &events));
    let (a, b) = (a.unwrap(), b.unwrap());
    assert_ne!(a.outcome, b.outcome);
    assert_eq!(a.batch_end, b.batch_end);
    assert_eq!(left.checkpoint().await.unwrap(), a.batch_end);
    left.close().await;
    right.close().await;
}

#[tokio::test]
async fn read_only_handle_refuses_single_and_batch_writes() {
    let dir = TempDir::new();
    let path = dir.db();
    let writer = PaperStore::create(&path, "batch-account", config()).await.unwrap();
    let head = writer.checkpoint().await.unwrap();
    writer.apply_batch(&head, &[buy("buy"), fill("fill", "buy", 1)]).await.unwrap();
    let before = writer.audit().await.unwrap();
    let reader = PaperStore::open_read_only(&path, "batch-account", config()).await.unwrap();
    assert_eq!(reader.audit().await.unwrap(), before);
    let head = reader.checkpoint().await.unwrap();
    assert!(reader.apply(halt()).await.is_err());
    assert!(reader.apply_batch(&head, &[halt()]).await.is_err());
    assert_eq!(writer.audit().await.unwrap(), before);
    reader.close().await;
    writer.close().await;
    assert!(PaperStore::open_read_only(&dir.0.join("absent.sqlite"), "batch-account", config()).await.is_err());
    assert!(!dir.0.join("absent.sqlite").exists());
}
