use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use std::{path::{Path, PathBuf}, time::Duration};
use trading_server::core::{
    paper_ledger::{Action, ApplyOutcome, JournalEvent, LedgerError, OrderTerms, PaperConfig, Side, Status},
    paper_store::PaperStore,
};
use uuid::Uuid;

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("paper-store-{}", Uuid::new_v4()));
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
        quote_asset: "USDT".into(),
        initial_cash: 1_000,
        max_order_cost: 1_000,
        max_asset_cost: 1_000,
        max_assets: 2,
        max_realized_loss: 100,
    }
}

fn event(id: &str, action: Action) -> JournalEvent {
    JournalEvent { event_id: id.into(), action }
}

fn buy(id: &str, asset: &str, lots: u64) -> JournalEvent {
    event(id, Action::Submit(OrderTerms {
        order_id: id.into(),
        asset: asset.into(),
        side: Side::Buy,
        lots,
        limit_price: 100,
        max_fee: 10,
    }))
}

fn fill(id: &str, order_id: &str, lots: u64, price: u64, fee: u64) -> JournalEvent {
    event(id, Action::Fill { order_id: order_id.into(), lots, price, fee })
}

async fn sql(path: &Path, statement: &str) {
    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(
            SqliteConnectOptions::new().filename(path).create_if_missing(false)
                .busy_timeout(Duration::from_secs(5)),
        )
        .await
        .unwrap();
    sqlx::query(statement).execute(&pool).await.unwrap();
    pool.close().await;
}

#[tokio::test]
async fn restart_restores_fills_reservations_halt_and_event_idempotency() {
    let dir = TempDir::new();
    let path = dir.db();
    let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let submit = buy("buy1", "BTC", 3);
    let execution = fill("fill1", "buy1", 1, 90, 3);
    assert_eq!(store.apply(submit.clone()).await.unwrap(), ApplyOutcome::Applied);
    assert_eq!(store.apply(execution.clone()).await.unwrap(), ApplyOutcome::Applied);
    let partial = store.audit().await.unwrap();
    assert_eq!(partial.account.cash, 907);
    assert_eq!(partial.account.reserved_cash, 207);
    assert_eq!(partial.account.holdings["BTC"].cost_basis, 93);
    store.close().await;

    let store = PaperStore::open(&path, "account-a", config()).await.unwrap();
    assert_eq!(store.audit().await.unwrap(), partial);
    assert_eq!(store.apply(submit).await.unwrap(), ApplyOutcome::Duplicate);
    assert_eq!(store.apply(execution).await.unwrap(), ApplyOutcome::Duplicate);
    assert_eq!(store.audit().await.unwrap(), partial);
    store.apply(event("stop", Action::HaltBuys)).await.unwrap();
    let halted = store.audit().await.unwrap();
    assert_eq!(halted.head.sequence, 3);
    assert!(halted.account.buys_halted);
    assert_eq!(halted.account.reserved_cash, 0);
    assert_eq!(halted.account.orders["buy1"].status, Status::Canceled);
    store.close().await;

    let store = PaperStore::open(&path, "account-a", config()).await.unwrap();
    assert_eq!(store.audit().await.unwrap(), halted);
    let error = store.apply(buy("later", "ETH", 1)).await.unwrap_err();
    assert_eq!(error.downcast_ref::<LedgerError>(), Some(&LedgerError::BuysHalted));
    assert_eq!(store.audit().await.unwrap(), halted);
    store.close().await;
}

#[tokio::test]
async fn create_and_open_never_silently_reset_an_account() {
    let dir = TempDir::new();
    let path = dir.db();
    assert!(PaperStore::open(&path, "account-a", config()).await.is_err());
    assert!(!path.exists());
    let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
    store.apply(buy("buy1", "BTC", 1)).await.unwrap();
    let before = store.audit().await.unwrap();
    store.close().await;
    assert!(PaperStore::create(&path, "account-a", config()).await.is_err());
    assert!(PaperStore::open(&path, "other", config()).await.is_err());
    let mut changed = config();
    changed.initial_cash += 1;
    assert!(PaperStore::open(&path, "account-a", changed).await.is_err());
    let store = PaperStore::open(&path, "account-a", config()).await.unwrap();
    assert_eq!(store.audit().await.unwrap(), before);
    store.close().await;
}

#[tokio::test]
async fn corrupt_payload_cannot_be_audited_or_extended() {
    let dir = TempDir::new();
    let path = dir.db();
    let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
    store.apply(buy("buy1", "BTC", 1)).await.unwrap();
    sql(&path, "UPDATE paper_events SET payload = replace(payload, '100', '101') WHERE seq = 1").await;
    assert!(store.audit().await.is_err());
    assert!(store.apply(buy("buy2", "ETH", 1)).await.is_err());
    store.close().await;
    assert!(PaperStore::open(&path, "account-a", config()).await.is_err());
}

#[tokio::test]
async fn missing_tail_and_sequence_gap_are_rejected() {
    for statement in [
        "DELETE FROM paper_events WHERE seq = 2",
        "UPDATE paper_events SET seq = 3 WHERE seq = 2",
    ] {
        let dir = TempDir::new();
        let path = dir.db();
        let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
        store.apply(buy("buy1", "BTC", 1)).await.unwrap();
        store.apply(fill("fill1", "buy1", 1, 90, 0)).await.unwrap();
        store.close().await;
        sql(&path, statement).await;
        assert!(PaperStore::open(&path, "account-a", config()).await.is_err());
    }
}

#[tokio::test]
async fn unsupported_version_and_wrong_head_digest_are_rejected() {
    for statement in [
        "UPDATE paper_meta SET schema_version = 2",
        "UPDATE paper_meta SET head_hash = lower(hex(zeroblob(32)))",
    ] {
        let dir = TempDir::new();
        let path = dir.db();
        let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
        store.close().await;
        sql(&path, statement).await;
        assert!(PaperStore::open(&path, "account-a", config()).await.is_err());
    }
}

#[tokio::test]
async fn failed_head_write_rolls_back_event_and_can_be_retried() {
    let dir = TempDir::new();
    let path = dir.db();
    let store = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let before = store.audit().await.unwrap();
    sql(&path, r#"CREATE TRIGGER fail_head BEFORE UPDATE OF head_seq ON paper_meta
        WHEN NEW.head_seq != OLD.head_seq BEGIN SELECT RAISE(ABORT, 'injected failure'); END"#).await;
    let submit = buy("buy1", "BTC", 1);
    assert!(store.apply(submit.clone()).await.is_err());
    assert_eq!(store.audit().await.unwrap(), before);
    sql(&path, "DROP TRIGGER fail_head").await;
    assert_eq!(store.apply(submit).await.unwrap(), ApplyOutcome::Applied);
    assert_eq!(store.audit().await.unwrap().head.sequence, 1);
    store.close().await;
}

#[tokio::test]
async fn independent_writers_cannot_overspend_shared_cash() {
    let dir = TempDir::new();
    let path = dir.db();
    let mut cfg = config();
    cfg.initial_cash = 150;
    let left = PaperStore::create(&path, "account-a", cfg.clone()).await.unwrap();
    let right = PaperStore::open(&path, "account-a", cfg).await.unwrap();
    let (a, b) = tokio::join!(
        left.apply(buy("a", "BTC", 1)),
        right.apply(buy("b", "ETH", 1))
    );
    match (a, b) {
        (Ok(ApplyOutcome::Applied), Err(error)) | (Err(error), Ok(ApplyOutcome::Applied)) => {
            assert_eq!(error.downcast_ref::<LedgerError>(), Some(&LedgerError::InsufficientCash));
        }
        other => panic!("Expected one accepted reservation: {other:?}"),
    }
    let state = left.audit().await.unwrap();
    assert_eq!(state.head.sequence, 1);
    assert_eq!(state.account.reserved_cash, 110);
    assert_eq!(state.account.available_cash, 40);
    assert_eq!(right.audit().await.unwrap(), state);
    left.close().await;
    right.close().await;
}

#[tokio::test]
async fn independent_writers_deduplicate_retried_event() {
    let dir = TempDir::new();
    let path = dir.db();
    let left = PaperStore::create(&path, "account-a", config()).await.unwrap();
    let right = PaperStore::open(&path, "account-a", config()).await.unwrap();
    let submit = buy("same", "BTC", 1);
    let (a, b) = tokio::join!(left.apply(submit.clone()), right.apply(submit));
    assert!(matches!(
        (a.unwrap(), b.unwrap()),
        (ApplyOutcome::Applied, ApplyOutcome::Duplicate) | (ApplyOutcome::Duplicate, ApplyOutcome::Applied)
    ));
    let before = left.audit().await.unwrap();
    assert_eq!(before.head.sequence, 1);
    assert_eq!(before.account.reserved_cash, 110);
    let error = right.apply(buy("same", "ETH", 1)).await.unwrap_err();
    assert_eq!(error.downcast_ref::<LedgerError>(), Some(&LedgerError::EventIdConflict));
    assert_eq!(left.audit().await.unwrap(), before);
    left.close().await;
    right.close().await;
}
