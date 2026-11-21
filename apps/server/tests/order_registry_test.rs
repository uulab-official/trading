use std::collections::HashSet;
use std::sync::Arc;

use chrono::Duration;
use tokio::sync::Barrier;
use tokio::task::JoinSet;
use trading_server::core::database::Database;
use trading_server::core::models::{Order, OrderSide, OrderStatus, OrderType};
use trading_server::core::order_manager::OrderManager;
use trading_server::exchange::ExchangeEvent;
use uuid::Uuid;

async fn pending_order(manager: &OrderManager) -> Order {
    manager
        .create_order(
            "BTC/USDT".to_string(),
            OrderSide::Buy,
            OrderType::Limit,
            10.0,
            Some(100.0),
        )
        .await
        .expect("valid pending order")
}

fn assert_snapshot(actual: &Order, expected: &Order) {
    assert_eq!(
        serde_json::to_value(actual).unwrap(),
        serde_json::to_value(expected).unwrap()
    );
}

#[tokio::test]
async fn created_pending_order_is_immediately_visible_and_open() {
    let manager = OrderManager::new();
    let order = pending_order(&manager).await;
    assert_snapshot(
        &manager.get_order(&order.id).expect("created order"),
        &order,
    );
    assert_eq!(manager.get_all_orders().len(), 1);
    assert_eq!(manager.get_open_orders().len(), 1);
    assert_snapshot(&manager.get_open_orders()[0], &order);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_creation_preserves_256_unique_orders() {
    let manager = Arc::new(OrderManager::new());
    let barrier = Arc::new(Barrier::new(256));
    let mut tasks = JoinSet::new();
    for _ in 0..256 {
        let manager = Arc::clone(&manager);
        let barrier = Arc::clone(&barrier);
        tasks.spawn(async move {
            barrier.wait().await;
            pending_order(&manager).await
        });
    }
    let mut ids = HashSet::new();
    while let Some(result) = tasks.join_next().await {
        assert!(ids.insert(result.unwrap().id), "order ID collision");
    }
    assert_eq!(ids.len(), 256);
    assert_eq!(manager.get_all_orders().len(), 256);
    assert_eq!(manager.get_open_orders().len(), 256);
}

#[tokio::test]
async fn invalid_creation_never_inserts_an_order() {
    let manager = OrderManager::new();
    for quantity in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert!(manager
            .create_order(
                "BTC/USDT".into(),
                OrderSide::Buy,
                OrderType::Market,
                quantity,
                None
            )
            .await
            .is_err());
        assert!(manager.get_all_orders().is_empty());
    }
    for price in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert!(manager
            .create_order(
                "BTC/USDT".into(),
                OrderSide::Buy,
                OrderType::Limit,
                1.0,
                Some(price)
            )
            .await
            .is_err());
        assert!(manager.get_all_orders().is_empty());
    }
    for symbol in ["", "  ", "BTC\nUSDT", "BTC\u{0000}USDT", "BTC\u{007f}USDT"] {
        assert!(manager
            .create_order(symbol.into(), OrderSide::Buy, OrderType::Market, 1.0, None)
            .await
            .is_err());
        assert!(manager.get_all_orders().is_empty());
    }
    for order_type in [OrderType::Limit, OrderType::StopLoss, OrderType::TakeProfit] {
        assert!(manager
            .create_order("BTC/USDT".into(), OrderSide::Buy, order_type, 1.0, None)
            .await
            .is_err());
    }
    assert!(manager.get_all_orders().is_empty());
}

#[tokio::test]
async fn market_creation_can_omit_price() {
    let manager = OrderManager::new();
    let order = manager
        .create_order(
            "BTC/USDT".into(),
            OrderSide::Buy,
            OrderType::Market,
            1.0,
            None,
        )
        .await
        .unwrap();
    assert_snapshot(&manager.get_order(&order.id).expect("market order"), &order);
}

#[tokio::test]
async fn status_wrapper_updates_a_created_order() {
    let manager = OrderManager::new();
    let order = pending_order(&manager).await;
    manager.update_order_status(&order.id, OrderStatus::Open);
    let updated = manager.get_order(&order.id).expect("updated order");
    assert_eq!(updated.status, OrderStatus::Open);
    assert!(updated.updated_at >= order.updated_at);
    assert_eq!(manager.get_open_orders().len(), 1);
}

#[tokio::test]
async fn trade_update_preserves_cumulative_fill_metadata() {
    let manager = OrderManager::new();
    let mut order = pending_order(&manager).await;
    order.status = OrderStatus::PartiallyFilled;
    order.filled_quantity = 3.0;
    order.average_price = Some(100.0);
    order.exchange_order_id = Some("synthetic-exchange-1".into());
    order.commission = Some(1.0);
    order.commission_asset = Some("USDT".into());
    order.updated_at += Duration::seconds(1);
    manager.handle_exchange_event(&ExchangeEvent::TradeUpdate(order.clone()));
    assert_snapshot(&manager.get_order(&order.id).expect("partial fill"), &order);

    order.status = OrderStatus::Filled;
    order.filled_quantity = 10.0;
    order.average_price = Some(102.0);
    order.commission = Some(2.0);
    order.updated_at += Duration::seconds(1);
    manager.handle_exchange_event(&ExchangeEvent::TradeUpdate(order.clone()));
    assert_snapshot(&manager.get_order(&order.id).expect("final fill"), &order);
    assert!(manager.get_open_orders().is_empty());
}

fn with_partial_fill(mut order: Order) -> Order {
    order.status = OrderStatus::PartiallyFilled;
    order.filled_quantity = 3.0;
    order.average_price = Some(100.0);
    order.exchange_order_id = Some("synthetic-exchange-1".into());
    order.commission = Some(1.0);
    order.commission_asset = Some("USDT".into());
    order.updated_at += Duration::seconds(1);
    order
}

fn assert_rejected_without_changes(manager: &OrderManager, before: &Order, candidate: &Order) {
    assert!(manager.apply_order_update(candidate).is_err());
    assert_snapshot(&manager.get_order(&before.id).unwrap(), before);
    assert_eq!(manager.get_all_orders().len(), 1);
}

#[tokio::test]
async fn complete_snapshots_apply_cumulative_fills_and_final_metadata() {
    let manager = OrderManager::new();
    let partial = with_partial_fill(pending_order(&manager).await);
    manager.apply_order_update(&partial).unwrap();
    assert_snapshot(&manager.get_order(&partial.id).unwrap(), &partial);
    manager.apply_order_update(&partial).unwrap();
    assert_snapshot(&manager.get_order(&partial.id).unwrap(), &partial);

    let mut complete = partial.clone();
    complete.status = OrderStatus::Filled;
    complete.filled_quantity = 10.0;
    complete.average_price = Some(102.0);
    complete.commission = Some(2.0);
    complete.updated_at += Duration::seconds(1);
    manager.apply_order_update(&complete).unwrap();
    assert_snapshot(&manager.get_order(&complete.id).unwrap(), &complete);
    assert!(manager.get_open_orders().is_empty());
}

#[tokio::test]
async fn regressive_conflicting_and_invalid_updates_preserve_every_field() {
    let manager = OrderManager::new();
    let partial = with_partial_fill(pending_order(&manager).await);
    manager.apply_order_update(&partial).unwrap();
    let cases: Vec<(&str, fn(&mut Order))> = vec![
        ("changed symbol", |o| o.symbol = "ETH/USDT".into()),
        ("changed side", |o| o.side = OrderSide::Sell),
        ("changed order type", |o| o.order_type = OrderType::Market),
        ("changed requested quantity", |o| o.quantity = 11.0),
        ("changed requested price", |o| o.price = Some(101.0)),
        ("changed creation time", |o| {
            o.created_at += Duration::milliseconds(1)
        }),
        ("stale timestamp", |o| o.updated_at -= Duration::seconds(2)),
        ("changed exchange ID", |o| {
            o.exchange_order_id = Some("other".into())
        }),
        ("removed exchange ID", |o| o.exchange_order_id = None),
        ("blank exchange ID", |o| {
            o.exchange_order_id = Some(" ".into())
        }),
        ("invalid exchange ID", |o| {
            o.exchange_order_id = Some("id\n".into())
        }),
        ("decreased fill", |o| o.filled_quantity = 2.0),
        ("overfill", |o| o.filled_quantity = 11.0),
        ("zero partial fill", |o| o.filled_quantity = 0.0),
        ("NaN fill", |o| o.filled_quantity = f64::NAN),
        ("infinite fill", |o| o.filled_quantity = f64::INFINITY),
        ("negative fill", |o| o.filled_quantity = -1.0),
        ("rewritten average", |o| o.average_price = Some(101.0)),
        ("missing average", |o| o.average_price = None),
        ("NaN average", |o| o.average_price = Some(f64::NAN)),
        ("infinite average", |o| {
            o.average_price = Some(f64::INFINITY)
        }),
        ("zero average", |o| o.average_price = Some(0.0)),
        ("negative average", |o| o.average_price = Some(-100.0)),
        ("decreasing gross", |o| {
            o.filled_quantity = 4.0;
            o.average_price = Some(50.0);
        }),
        ("unchanged gross", |o| {
            o.filled_quantity = 6.0;
            o.average_price = Some(50.0);
        }),
        ("overflowing gross", |o| o.average_price = Some(f64::MAX)),
        ("negative commission", |o| o.commission = Some(-1.0)),
        ("NaN commission", |o| o.commission = Some(f64::NAN)),
        ("infinite commission", |o| {
            o.commission = Some(f64::INFINITY)
        }),
        ("decreased commission", |o| o.commission = Some(0.5)),
        ("removed commission", |o| {
            o.commission = None;
            o.commission_asset = None;
        }),
        ("missing commission amount", |o| o.commission = None),
        ("missing commission asset", |o| o.commission_asset = None),
        ("changed commission asset", |o| {
            o.commission_asset = Some("BTC".into())
        }),
        ("blank commission asset", |o| {
            o.commission_asset = Some(" ".into())
        }),
        ("invalid commission asset", |o| {
            o.commission_asset = Some("USDT\n".into())
        }),
        ("pending regression", |o| o.status = OrderStatus::Pending),
        ("open regression", |o| o.status = OrderStatus::Open),
        ("failed with fills", |o| o.status = OrderStatus::Failed),
        ("incomplete filled state", |o| {
            o.status = OrderStatus::Filled
        }),
        ("complete partial state", |o| o.filled_quantity = 10.0),
        ("complete canceled state", |o| {
            o.status = OrderStatus::Canceled;
            o.filled_quantity = 10.0;
        }),
    ];
    for (name, mutate) in cases {
        let mut candidate = partial.clone();
        candidate.updated_at += Duration::seconds(1);
        mutate(&mut candidate);
        assert!(
            manager.apply_order_update(&candidate).is_err(),
            "accepted {name}"
        );
        assert_snapshot(&manager.get_order(&partial.id).unwrap(), &partial);
        assert_eq!(
            manager.get_all_orders().len(),
            1,
            "mutated count for {name}"
        );
    }
}

#[tokio::test]
async fn unknown_updates_and_compatibility_refusals_do_not_mutate_registry() {
    let manager = OrderManager::new();
    let pending = pending_order(&manager).await;
    let mut unknown = pending.clone();
    unknown.id = "unknown-local-order".into();
    assert_rejected_without_changes(&manager, &pending, &unknown);
    assert!(manager
        .try_update_order_status(&unknown.id, OrderStatus::Open)
        .is_err());
    manager.update_order_status(&unknown.id, OrderStatus::Open);
    manager.handle_exchange_event(&ExchangeEvent::TradeUpdate(unknown));
    manager.handle_exchange_event(&ExchangeEvent::Connected);
    assert!(manager
        .try_update_order_status(&pending.id, OrderStatus::Filled)
        .is_err());
    manager.update_order_status(&pending.id, OrderStatus::Filled);
    assert_snapshot(&manager.get_order(&pending.id).unwrap(), &pending);
    assert_eq!(manager.get_all_orders().len(), 1);
}

#[tokio::test]
async fn status_updates_preserve_metadata_and_do_not_invent_fills() {
    let manager = OrderManager::new();
    let pending = pending_order(&manager).await;
    manager
        .try_update_order_status(&pending.id, OrderStatus::Open)
        .unwrap();
    let open = manager.get_order(&pending.id).unwrap();
    assert_eq!(open.status, OrderStatus::Open);
    assert_eq!(open.filled_quantity, 0.0);
    assert_eq!(open.average_price, None);
    assert_eq!(open.quantity, pending.quantity);
    assert_eq!(open.price, pending.price);
    assert_eq!(open.created_at, pending.created_at);
    assert!(open.updated_at >= pending.updated_at);

    let partial = with_partial_fill(open);
    manager.apply_order_update(&partial).unwrap();
    manager
        .try_update_order_status(&partial.id, OrderStatus::Canceled)
        .unwrap();
    let canceled = manager.get_order(&partial.id).unwrap();
    assert_eq!(canceled.status, OrderStatus::Canceled);
    assert_eq!(canceled.filled_quantity, 3.0);
    assert_eq!(canceled.average_price, Some(100.0));
    assert_eq!(canceled.commission, Some(1.0));
    assert_eq!(canceled.commission_asset.as_deref(), Some("USDT"));
    assert_eq!(canceled.exchange_order_id, partial.exchange_order_id);
    assert!(canceled.updated_at >= partial.updated_at);
    assert!(manager.get_open_orders().is_empty());
}

fn snapshot_in_state(base: &Order, status: OrderStatus) -> Order {
    let mut snapshot = base.clone();
    snapshot.status = status.clone();
    snapshot.exchange_order_id = Some("synthetic-exchange-1".into());
    snapshot.updated_at += Duration::seconds(1);
    if matches!(
        status,
        OrderStatus::PartiallyFilled | OrderStatus::Canceled | OrderStatus::Filled
    ) {
        snapshot.filled_quantity = if status == OrderStatus::Filled {
            10.0
        } else {
            3.0
        };
        snapshot.average_price = Some(100.0);
        snapshot.commission = Some(1.0);
        snapshot.commission_asset = Some("USDT".into());
    }
    snapshot
}

#[tokio::test]
async fn transition_matrix_enforces_terminal_and_partial_fill_boundaries() {
    let base = pending_order(&OrderManager::new()).await;
    let states = [
        OrderStatus::Pending,
        OrderStatus::Open,
        OrderStatus::PartiallyFilled,
        OrderStatus::Filled,
        OrderStatus::Canceled,
        OrderStatus::Failed,
    ];
    let permitted = [
        [true, true, true, true, true, true],
        [false, true, true, true, true, true],
        [false, false, true, true, true, false],
        [false, false, false, true, false, false],
        [false, false, false, false, true, false],
        [false, false, false, false, false, true],
    ];
    for (source_index, source) in states.iter().enumerate() {
        for (target_index, target) in states.iter().enumerate() {
            let manager = OrderManager::new();
            let original = snapshot_in_state(&base, source.clone());
            manager.restore_order(original.clone()).unwrap();
            let mut candidate = snapshot_in_state(&base, target.clone());
            candidate.updated_at = original.updated_at + Duration::seconds(1);
            let allowed = permitted[source_index][target_index];
            assert_eq!(
                manager.apply_order_update(&candidate).is_ok(),
                allowed,
                "{source:?} -> {target:?}"
            );
            let expected = if !allowed || source_index >= 3 {
                &original
            } else {
                &candidate
            };
            assert_snapshot(&manager.get_order(&base.id).unwrap(), expected);
        }
    }
}

#[tokio::test]
async fn terminal_repetitions_are_no_ops_and_cannot_rewrite_late_metadata() {
    let base = pending_order(&OrderManager::new()).await;
    for status in [
        OrderStatus::Filled,
        OrderStatus::Canceled,
        OrderStatus::Failed,
    ] {
        let manager = OrderManager::new();
        let terminal = snapshot_in_state(&base, status);
        manager.restore_order(terminal.clone()).unwrap();
        manager.apply_order_update(&terminal).unwrap();
        let mut later = terminal.clone();
        later.updated_at += Duration::seconds(10);
        manager.apply_order_update(&later).unwrap();
        assert_snapshot(&manager.get_order(&terminal.id).unwrap(), &terminal);

        let mut changed_fee = later.clone();
        changed_fee.commission = Some(terminal.commission.unwrap_or(0.0) + 1.0);
        changed_fee.commission_asset = Some("USDT".into());
        assert_rejected_without_changes(&manager, &terminal, &changed_fee);
        let mut stale = terminal.clone();
        stale.updated_at -= Duration::milliseconds(1);
        assert_rejected_without_changes(&manager, &terminal, &stale);
    }
}

#[tokio::test]
async fn additional_fills_require_increasing_finite_gross_value() {
    let manager = OrderManager::new();
    let pending = pending_order(&manager).await;
    let mut underflow = with_partial_fill(pending.clone());
    underflow.filled_quantity = f64::MIN_POSITIVE;
    underflow.average_price = Some(f64::MIN_POSITIVE);
    assert_rejected_without_changes(&manager, &pending, &underflow);
    let mut overflowing = with_partial_fill(pending.clone());
    overflowing.average_price = Some(f64::MAX);
    assert_rejected_without_changes(&manager, &pending, &overflowing);

    let partial = with_partial_fill(pending);
    manager.apply_order_update(&partial).unwrap();
    let mut next = partial.clone();
    next.filled_quantity = 4.0;
    next.average_price = Some(90.0);
    next.updated_at += Duration::seconds(1);
    manager.apply_order_update(&next).unwrap();
    assert_snapshot(&manager.get_order(&next.id).unwrap(), &next);
}

#[tokio::test]
async fn zero_and_later_reported_commission_follow_cumulative_metadata_rules() {
    let manager = OrderManager::new();
    let pending = pending_order(&manager).await;
    let mut zero = pending.clone();
    zero.commission = Some(0.0);
    zero.commission_asset = Some("USDT".into());
    manager.apply_order_update(&zero).unwrap();
    assert_snapshot(&manager.get_order(&zero.id).unwrap(), &zero);

    let mut positive_without_fill = zero.clone();
    positive_without_fill.commission = Some(1.0);
    assert_rejected_without_changes(&manager, &zero, &positive_without_fill);
    let mut asset_change = with_partial_fill(zero.clone());
    asset_change.commission_asset = Some("BTC".into());
    assert_rejected_without_changes(&manager, &zero, &asset_change);
    let partial = with_partial_fill(zero);
    manager.apply_order_update(&partial).unwrap();
    let mut late_fee = partial.clone();
    late_fee.commission = Some(1.5);
    late_fee.updated_at += Duration::seconds(1);
    manager.apply_order_update(&late_fee).unwrap();
    assert_snapshot(&manager.get_order(&late_fee.id).unwrap(), &late_fee);
}

#[tokio::test]
async fn restoration_rejects_invalid_full_snapshots_before_insertion() {
    let base = pending_order(&OrderManager::new()).await;
    let cases: Vec<(&str, fn(&mut Order))> = vec![
        ("empty ID", |o| o.id.clear()),
        ("blank ID", |o| o.id = " ".into()),
        ("control ID", |o| o.id = "id\n".into()),
        ("empty symbol", |o| o.symbol.clear()),
        ("blank symbol", |o| o.symbol = "  ".into()),
        ("control symbol", |o| o.symbol = "BTC\nUSDT".into()),
        ("zero quantity", |o| o.quantity = 0.0),
        ("negative quantity", |o| o.quantity = -1.0),
        ("NaN quantity", |o| o.quantity = f64::NAN),
        ("infinite quantity", |o| o.quantity = f64::INFINITY),
        ("zero price", |o| o.price = Some(0.0)),
        ("negative price", |o| o.price = Some(-1.0)),
        ("NaN price", |o| o.price = Some(f64::NAN)),
        ("infinite price", |o| o.price = Some(f64::INFINITY)),
        ("missing limit price", |o| o.price = None),
        ("timestamp before creation", |o| {
            o.updated_at = o.created_at - Duration::seconds(1)
        }),
        ("blank exchange ID", |o| {
            o.exchange_order_id = Some(" ".into())
        }),
        ("control exchange ID", |o| {
            o.exchange_order_id = Some("id\n".into())
        }),
        ("pending with fills", |o| {
            o.filled_quantity = 1.0;
            o.average_price = Some(100.0);
        }),
        ("zero fill average", |o| o.average_price = Some(100.0)),
        ("negative filled quantity", |o| o.filled_quantity = -1.0),
        ("NaN filled quantity", |o| o.filled_quantity = f64::NAN),
        ("infinite filled quantity", |o| {
            o.filled_quantity = f64::INFINITY
        }),
        ("overfilled", |o| {
            o.status = OrderStatus::Filled;
            o.filled_quantity = 11.0;
            o.average_price = Some(100.0);
        }),
        ("partial zero fills", |o| {
            o.status = OrderStatus::PartiallyFilled
        }),
        ("filled zero fills", |o| o.status = OrderStatus::Filled),
        ("positive commission without fill", |o| {
            o.commission = Some(1.0);
            o.commission_asset = Some("USDT".into());
        }),
        ("missing commission asset", |o| o.commission = Some(0.0)),
        ("missing commission amount", |o| {
            o.commission_asset = Some("USDT".into())
        }),
        ("blank commission asset", |o| {
            o.commission = Some(0.0);
            o.commission_asset = Some(" ".into());
        }),
        ("control commission asset", |o| {
            o.commission = Some(0.0);
            o.commission_asset = Some("USDT\n".into());
        }),
        ("negative commission", |o| {
            o.commission = Some(-1.0);
            o.commission_asset = Some("USDT".into());
        }),
        ("NaN commission", |o| {
            o.commission = Some(f64::NAN);
            o.commission_asset = Some("USDT".into());
        }),
        ("infinite commission", |o| {
            o.commission = Some(f64::INFINITY);
            o.commission_asset = Some("USDT".into());
        }),
    ];
    for (name, mutate) in cases {
        let manager = OrderManager::new();
        let mut candidate = base.clone();
        mutate(&mut candidate);
        assert!(manager.restore_order(candidate).is_err(), "accepted {name}");
        assert!(manager.get_all_orders().is_empty());
    }
    let valid_partial = with_partial_fill(base);
    for average in [
        None,
        Some(0.0),
        Some(-1.0),
        Some(f64::NAN),
        Some(f64::INFINITY),
        Some(f64::MAX),
    ] {
        let manager = OrderManager::new();
        let mut candidate = valid_partial.clone();
        candidate.average_price = average;
        assert!(manager.restore_order(candidate).is_err());
        assert!(manager.get_all_orders().is_empty());
    }
}

#[tokio::test]
async fn identical_restore_is_idempotent_but_conflicts_never_overwrite() {
    let source = OrderManager::new();
    let partial = with_partial_fill(pending_order(&source).await);
    let manager = OrderManager::new();
    manager.restore_order(partial.clone()).unwrap();
    manager.restore_order(partial.clone()).unwrap();
    assert_eq!(manager.get_all_orders().len(), 1);
    assert_snapshot(&manager.get_order(&partial.id).unwrap(), &partial);
    let mut different = partial.clone();
    different.quantity = 11.0;
    assert!(manager.restore_order(different).is_err());
    let mut later = partial.clone();
    later.updated_at += Duration::seconds(1);
    assert!(manager.restore_order(later).is_err());
    assert_snapshot(&manager.get_order(&partial.id).unwrap(), &partial);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_cumulative_updates_never_overwrite_a_later_fill() {
    let manager = Arc::new(OrderManager::new());
    let pending = manager
        .create_order(
            "BTC/USDT".into(),
            OrderSide::Buy,
            OrderType::Limit,
            32.0,
            Some(100.0),
        )
        .await
        .unwrap();
    let barrier = Arc::new(Barrier::new(32));
    let mut tasks = JoinSet::new();
    for units in 1..=32 {
        let manager = Arc::clone(&manager);
        let barrier = Arc::clone(&barrier);
        let mut candidate = pending.clone();
        candidate.status = if units == 32 {
            OrderStatus::Filled
        } else {
            OrderStatus::PartiallyFilled
        };
        candidate.filled_quantity = f64::from(units);
        candidate.average_price = Some(100.0);
        candidate.exchange_order_id = Some("synthetic-concurrent-exchange".into());
        candidate.commission = Some(f64::from(units) / 10.0);
        candidate.commission_asset = Some("USDT".into());
        candidate.updated_at += Duration::seconds(i64::from(units));
        tasks.spawn(async move {
            barrier.wait().await;
            (units, manager.apply_order_update(&candidate).is_ok())
        });
    }
    let mut final_accepted = false;
    while let Some(task) = tasks.join_next().await {
        let (units, accepted) = task.unwrap();
        if units == 32 {
            final_accepted = accepted;
        }
    }
    assert!(final_accepted);
    let final_order = manager.get_order(&pending.id).unwrap();
    assert_eq!(final_order.status, OrderStatus::Filled);
    assert_eq!(final_order.filled_quantity, 32.0);
    assert_eq!(final_order.average_price, Some(100.0));
    assert_eq!(final_order.commission, Some(3.2));
    assert_eq!(
        final_order.updated_at,
        pending.updated_at + Duration::seconds(32)
    );
    assert_eq!(manager.get_all_orders().len(), 1);
    assert!(manager.get_open_orders().is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn concurrent_conflicting_restores_keep_exactly_one_snapshot() {
    let manager = Arc::new(OrderManager::new());
    let pending = pending_order(&OrderManager::new()).await;
    let barrier = Arc::new(Barrier::new(32));
    let mut tasks = JoinSet::new();
    for quantity in 1..=32 {
        let manager = Arc::clone(&manager);
        let barrier = Arc::clone(&barrier);
        let mut candidate = pending.clone();
        candidate.quantity = f64::from(quantity);
        tasks.spawn(async move {
            barrier.wait().await;
            let accepted = manager.restore_order(candidate.clone()).is_ok();
            (accepted, candidate)
        });
    }
    let mut winners = Vec::new();
    while let Some(task) = tasks.join_next().await {
        let (accepted, candidate) = task.unwrap();
        if accepted {
            winners.push(candidate);
        }
    }
    assert_eq!(winners.len(), 1);
    assert_eq!(manager.get_all_orders().len(), 1);
    assert_snapshot(&manager.get_order(&pending.id).unwrap(), &winners[0]);
}

struct PrivateDirectory(std::path::PathBuf);

impl PrivateDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("trading-order-registry-{}", Uuid::new_v4()));
        let mut builder = std::fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(&path).unwrap();
        Self(path)
    }
}

impl Drop for PrivateDirectory {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).expect("remove owned database test directory");
    }
}

#[tokio::test]
async fn sqlite_roundtrip_restores_pending_and_partial_snapshots_without_loss() {
    let directory = PrivateDirectory::new();
    let path = directory.0.join("orders.sqlite");
    let database = Database::new(&format!("sqlite://{}", path.display()))
        .await
        .unwrap();
    let source = OrderManager::new();
    let pending = pending_order(&source).await;
    let partial = with_partial_fill(pending_order(&source).await);
    source.apply_order_update(&partial).unwrap();
    database.save_order(&pending).await.unwrap();
    database.save_order(&partial).await.unwrap();
    assert!(path.is_file());
    assert_snapshot(
        &database.get_order(&pending.id).await.unwrap().unwrap(),
        &pending,
    );
    assert_snapshot(
        &database.get_order(&partial.id).await.unwrap().unwrap(),
        &partial,
    );
    let persisted = database.get_open_orders().await.unwrap();
    assert_eq!(persisted.len(), 2);
    let restored = OrderManager::new();
    for snapshot in persisted {
        restored.restore_order(snapshot.clone()).unwrap();
        restored.restore_order(snapshot).unwrap();
    }
    assert_eq!(restored.get_open_orders().len(), 2);
    assert_snapshot(&restored.get_order(&pending.id).unwrap(), &pending);
    assert_snapshot(&restored.get_order(&partial.id).unwrap(), &partial);
    let mut conflicting = partial.clone();
    conflicting.quantity = 20.0;
    assert!(restored.restore_order(conflicting).is_err());
    assert_snapshot(&restored.get_order(&partial.id).unwrap(), &partial);
}
