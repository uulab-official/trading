use std::collections::BTreeMap;
use trading_server::core::{
    paper_ledger::{Action, ApplyOutcome, JournalEvent, LedgerError, OrderTerms, PaperConfig, PaperLedger, Side, Status},
    paper_risk::{Breach, Mark, RiskError, RiskObservation, RiskPolicy},
    paper_store::PaperStore,
};
use uuid::Uuid;

const DAY: u64 = 86_400_000;

fn config() -> PaperConfig {
    PaperConfig { quote_asset: "USDT".into(), initial_cash: 10_000, max_order_cost: 10_000,
        max_asset_cost: 10_000, max_assets: 3, max_realized_loss: 10_000 }
}

fn policy() -> RiskPolicy {
    RiskPolicy { max_daily_loss_bps: 500, max_drawdown_bps: 1000,
        max_total_exposure_bps: 8000, max_asset_exposure_bps: 6000, max_mark_age_ms: 100 }
}

fn evt(id: &str, action: Action) -> JournalEvent {
    JournalEvent { event_id: id.into(), action }
}

fn obs(t: u64, prices: &[(&str, u64)]) -> RiskObservation {
    RiskObservation { at_ms: t, marks: prices.iter().map(|(asset, price)|
        (asset.to_string(), Mark { price: *price, source_ms: t })).collect::<BTreeMap<_, _>>() }
}

fn terms(id: &str, asset: &str, side: Side, lots: u64, price: u64, max_fee: u64) -> OrderTerms {
    OrderTerms { order_id: id.into(), asset: asset.into(), side, lots, limit_price: price, max_fee }
}

fn guarded_buy(id: &str, asset: &str, lots: u64, price: u64, frame: RiskObservation) -> JournalEvent {
    evt(id, Action::GuardedSubmit { terms: terms(id, asset, Side::Buy, lots, price, 0), observation: frame })
}

fn guarded_fill(id: &str, order: &str, lots: u64, price: u64, frame: RiskObservation) -> JournalEvent {
    evt(id, Action::GuardedFill { order_id: order.into(), lots, price, fee: 0, observation: frame })
}

fn ledger_with(p: RiskPolicy) -> PaperLedger {
    let mut ledger = PaperLedger::new(config()).unwrap();
    ledger.apply(evt("policy", Action::ConfigureRisk(p))).unwrap();
    ledger
}

fn reject(ledger: &mut PaperLedger, event: JournalEvent, error: LedgerError) {
    let before = ledger.snapshot().unwrap();
    let journal = ledger.journal().to_vec();
    assert_eq!(ledger.apply(event), Err(error));
    assert_eq!(ledger.snapshot().unwrap(), before);
    assert_eq!(ledger.journal(), journal.as_slice());
}

fn positioned(p: RiskPolicy) -> PaperLedger {
    let mut ledger = ledger_with(p);
    ledger.apply(guarded_buy("buy", "BTC", 10, 500, obs(1000, &[("BTC", 500)]))).unwrap();
    ledger.apply(guarded_fill("fill", "buy", 10, 500, obs(1001, &[("BTC", 500)]))).unwrap();
    ledger
}

#[test]
fn configure_once_only_and_legacy_account_is_unchanged() {
    let mut old = PaperLedger::new(config()).unwrap();
    old.apply(evt("legacy", Action::Submit(terms("legacy", "BTC", Side::Buy, 1, 100, 0)))).unwrap();
    assert!(old.snapshot().unwrap().risk.is_none());
    reject(&mut old, evt("policy", Action::ConfigureRisk(policy())), LedgerError::RiskAlreadyConfigured);
    let mut fresh = PaperLedger::new(config()).unwrap();
    let mut invalid = policy();
    invalid.max_daily_loss_bps = 0;
    reject(&mut fresh, evt("bad", Action::ConfigureRisk(invalid)), LedgerError::Risk(RiskError::InvalidPolicy));
    reject(&mut fresh, evt("observe", Action::ObserveRisk(obs(1, &[]))), LedgerError::RiskNotConfigured);
    fresh.apply(evt("policy", Action::ConfigureRisk(policy()))).unwrap();
    assert_eq!(fresh.apply(evt("policy", Action::ConfigureRisk(policy()))), Ok(ApplyOutcome::Duplicate));
    reject(&mut fresh, evt("again", Action::ConfigureRisk(policy())), LedgerError::RiskAlreadyConfigured);
    reject(&mut fresh, evt("plain", Action::Submit(terms("plain", "BTC", Side::Buy, 1, 100, 0))),
        LedgerError::RiskObservationRequired);
}

#[test]
fn valuation_counts_cash_reservations_once_and_tracks_fees_and_pending_exposure() {
    let mut ledger = ledger_with(policy());
    ledger.apply(evt("buy", Action::GuardedSubmit {
        terms: terms("buy", "BTC", Side::Buy, 10, 500, 20),
        observation: obs(1000, &[("BTC", 490)]),
    })).unwrap();
    let s = ledger.snapshot().unwrap();
    let report = s.risk.unwrap().last_report.unwrap();
    assert_eq!((s.cash, s.reserved_cash, report.equity, report.total_exposure), (10_000, 5020, 10_000, 5000));
    reject(&mut ledger, evt("raw", Action::Fill { order_id: "buy".into(), lots: 1, price: 490, fee: 0 }),
        LedgerError::RiskObservationRequired);
    ledger.apply(evt("fill", Action::GuardedFill {
        order_id: "buy".into(), lots: 4, price: 490, fee: 10,
        observation: obs(1001, &[("BTC", 490)]),
    })).unwrap();
    let s = ledger.snapshot().unwrap();
    let report = s.risk.unwrap().last_report.unwrap();
    assert_eq!(s.cash, 8030);
    assert_eq!(s.reserved_cash, 3010);
    assert_eq!(report.holdings_value, 1960);
    assert_eq!(report.equity, 9990);
    assert_eq!(report.unrealized_pnl, -10);
    assert_eq!(report.daily_pnl, -10);
    assert_eq!(report.total_exposure, 4960);
    assert_eq!(report.drawdown_bps, 10);
}

#[test]
fn loss_at_exact_threshold_latches_halt_and_replays_without_reset() {
    let mut ledger = positioned(policy());
    ledger.apply(guarded_buy("pending", "ETH", 1, 100, obs(1002, &[("BTC", 500), ("ETH", 100)]))).unwrap();
    ledger.apply(evt("loss", Action::ObserveRisk(obs(1003, &[("BTC", 450), ("ETH", 100)])))).unwrap();
    let s = ledger.snapshot().unwrap();
    assert!(s.buys_halted);
    assert_eq!(s.orders["pending"].status, Status::Canceled);
    assert_eq!(s.reserved_cash, 0);
    let report = s.risk.unwrap().last_report.unwrap();
    assert_eq!(report.daily_pnl, -500);
    assert_eq!(report.unrealized_pnl, -500);
    assert!(report.breaches.contains(&Breach::DailyLoss));
    // An exit is permitted even without a fresh mark; accounting still enforces inventory.
    ledger.apply(evt("exit", Action::Submit(terms("exit", "BTC", Side::Sell, 10, 600, 0)))).unwrap();
    ledger.apply(evt("exitfill", Action::Fill { order_id: "exit".into(), lots: 10, price: 600, fee: 0 })).unwrap();
    ledger.apply(evt("new-day", Action::ObserveRisk(obs(DAY + 1000, &[])))).unwrap();
    let state = ledger.snapshot().unwrap();
    assert!(state.buys_halted);
    assert_eq!(state.realized_pnl, 1000);
    assert_eq!(PaperLedger::replay(config(), ledger.journal().to_vec()).unwrap().snapshot().unwrap(), state);
}

#[test]
fn daily_baseline_rolls_to_last_sample_and_session_peak_remains() {
    let mut p = policy();
    p.max_daily_loss_bps = 1000;
    p.max_drawdown_bps = 500;
    p.max_asset_exposure_bps = 8000;
    let mut ledger = positioned(p);
    ledger.apply(evt("high", Action::ObserveRisk(obs(DAY - 1, &[("BTC", 600)])))).unwrap();
    ledger.apply(evt("next", Action::ObserveRisk(obs(DAY + 1, &[("BTC", 550)])))).unwrap();
    let report = ledger.snapshot().unwrap().risk.unwrap().last_report.unwrap();
    assert_eq!((report.daily_baseline, report.daily_pnl, report.peak_equity), (11_000, -500, 11_000));
    assert_eq!(report.drawdown_bps, 454);
    assert!(!ledger.snapshot().unwrap().buys_halted);
    ledger.apply(evt("low", Action::ObserveRisk(obs(DAY + 2, &[("BTC", 545)])))).unwrap();
    let s = ledger.snapshot().unwrap();
    assert!(s.buys_halted);
    let report = s.risk.unwrap().last_report.unwrap();
    assert_eq!(report.drawdown_bps, 500);
    assert_eq!(report.breaches, vec![Breach::Drawdown]);
}

#[test]
fn exposure_checks_include_pending_orders_and_reject_prospective_buys_atomically() {
    let mut ledger = positioned(policy());
    reject(&mut ledger, guarded_buy("oversized", "BTC", 3, 500, obs(1002, &[("BTC", 500)])), LedgerError::RiskLimit);
    ledger.apply(guarded_buy("eth", "ETH", 30, 100, obs(1002, &[("BTC", 500), ("ETH", 100)]))).unwrap();
    reject(&mut ledger, guarded_buy("sol", "SOL", 1, 100, obs(1003, &[("BTC", 500), ("ETH", 100), ("SOL", 100)])), LedgerError::RiskLimit);
    // Existing exposure worsens with a new mark; explicit observation halts and cancels pending buys.
    ledger.apply(evt("breach", Action::ObserveRisk(obs(1004, &[("BTC", 500), ("ETH", 101)])))).unwrap();
    let s = ledger.snapshot().unwrap();
    assert!(s.buys_halted);
    assert_eq!(s.orders["eth"].status, Status::Canceled);
    assert_eq!(s.risk.unwrap().last_report.unwrap().breaches, vec![Breach::TotalExposure]);
}

#[test]
fn missing_stale_future_zero_conflicting_and_regressing_marks_fail_closed() {
    let mut ledger = positioned(policy());
    let mut stale = obs(1200, &[("BTC", 500)]);
    stale.marks.get_mut("BTC").unwrap().source_ms = 1001;
    let mut future = obs(1200, &[("BTC", 500)]);
    future.marks.get_mut("BTC").unwrap().source_ms = 1201;
    let mut old = obs(1050, &[("BTC", 500)]);
    old.marks.get_mut("BTC").unwrap().source_ms = 1000;
    let mut conflict = obs(1050, &[("BTC", 499)]);
    conflict.marks.get_mut("BTC").unwrap().source_ms = 1001;
    for (frame, error) in [
        (obs(1002, &[]), RiskError::MissingMark),
        (stale, RiskError::StaleMark),
        (future, RiskError::InvalidObservation),
        (old, RiskError::RegressingTime),
        (conflict, RiskError::ConflictingMark),
        (obs(1002, &[("BTC", 0)]), RiskError::InvalidObservation),
        (obs(1000, &[("BTC", 500)]), RiskError::RegressingTime),
        (obs(1001, &[("BTC", 499)]), RiskError::ConflictingMark),
    ] {
        reject(&mut ledger, evt("invalid", Action::ObserveRisk(frame)), LedgerError::Risk(error));
    }
    reject(&mut ledger, guarded_buy("no-mark", "ETH", 1, 100, obs(1002, &[("BTC", 500)])),
        LedgerError::Risk(RiskError::MissingMark));
    assert!(!ledger.snapshot().unwrap().buys_halted);
}

#[test]
fn prospective_fill_loss_is_refused_and_observed_loss_can_be_persisted_separately() {
    let mut p = policy();
    p.max_asset_exposure_bps = 8000;
    let mut ledger = ledger_with(p);
    ledger.apply(guarded_buy("buy", "BTC", 10, 500, obs(1000, &[("BTC", 500)]))).unwrap();
    // A costly synthetic fill at a market mark of 400 would cause a 1000 loss.
    reject(&mut ledger, guarded_fill("fill", "buy", 10, 500, obs(1001, &[("BTC", 400)])), LedgerError::RiskLimit);
    let s = ledger.snapshot().unwrap();
    assert_eq!(s.cash, 10_000);
    assert_eq!(s.reserved_cash, 5000);
    assert!(s.holdings.is_empty());
    assert!(!s.buys_halted);
}

#[test]
fn overflow_fails_without_mutation_and_exact_exposure_boundary_is_allowed() {
    let mut ledger = ledger_with(policy());
    ledger.apply(guarded_buy("buy", "BTC", 12, 500, obs(1000, &[("BTC", 500)]))).unwrap();
    reject(&mut ledger, evt("overflow", Action::ObserveRisk(obs(1001, &[("BTC", u64::MAX)]))),
        LedgerError::Risk(RiskError::ArithmeticOverflow));
    assert_eq!(ledger.snapshot().unwrap().reserved_cash, 6000);
}

#[tokio::test]
async fn sqlite_replay_backup_and_restore_retain_risk_policy_peak_and_halt() {
    let dir = std::env::temp_dir().join(format!("paper-risk-{}", Uuid::new_v4()));
    std::fs::create_dir(&dir).unwrap();
    let path = dir.join("ledger.sqlite");
    let restored_path = dir.join("restored.sqlite");
    let store = PaperStore::create(&path, "risk-account", config()).await.unwrap();
    for event in [
        evt("policy", Action::ConfigureRisk(policy())),
        guarded_buy("buy", "BTC", 10, 500, obs(1000, &[("BTC", 500)])),
        guarded_fill("fill", "buy", 10, 500, obs(1001, &[("BTC", 500)])),
        evt("loss", Action::ObserveRisk(obs(1002, &[("BTC", 450)]))),
    ] {
        store.apply(event).await.unwrap();
    }
    let before = store.audit().await.unwrap();
    assert!(before.account.buys_halted);
    let backup = store.export_backup().await.unwrap();
    store.close().await;
    let reopened = PaperStore::open_with_checkpoint(&path, "risk-account", config(), backup.checkpoint.clone()).await.unwrap();
    assert_eq!(reopened.audit().await.unwrap(), before);
    assert_eq!(reopened.apply(evt("loss", Action::ObserveRisk(obs(1002, &[("BTC", 450)])))).await.unwrap(), ApplyOutcome::Duplicate);
    reopened.close().await;
    let restored = PaperStore::restore_backup(&restored_path, "risk-account", config(), &backup.bytes, backup.checkpoint).await.unwrap();
    assert_eq!(restored.audit().await.unwrap(), before);
    assert!(restored.apply(guarded_buy("new", "BTC", 1, 450, obs(1003, &[("BTC", 450)]))).await.is_err());
    restored.close().await;
    std::fs::remove_dir_all(dir).unwrap();
}
