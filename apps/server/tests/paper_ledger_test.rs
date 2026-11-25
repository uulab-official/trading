use trading_server::core::paper_ledger::*;

fn config() -> PaperConfig {
    PaperConfig {
        quote_asset: "USDT".into(),
        initial_cash: 10_000,
        max_order_cost: 3_000,
        max_asset_cost: 4_000,
        max_assets: 2,
        max_realized_loss: 500,
    }
}

fn event(id: &str, action: Action) -> JournalEvent {
    JournalEvent {
        event_id: id.into(),
        action,
    }
}

fn submit(id: &str, asset: &str, side: Side, lots: u64, price: u64, fee: u64) -> JournalEvent {
    event(
        id,
        Action::Submit(OrderTerms {
            order_id: id.into(),
            asset: asset.into(),
            side,
            lots,
            limit_price: price,
            max_fee: fee,
        }),
    )
}

fn fill(id: &str, order: &str, lots: u64, price: u64, fee: u64) -> JournalEvent {
    event(
        id,
        Action::Fill {
            order_id: order.into(),
            lots,
            price,
            fee,
        },
    )
}

fn cancel(id: &str, order: &str) -> JournalEvent {
    event(
        id,
        Action::Cancel {
            order_id: order.into(),
        },
    )
}

fn accepted(ledger: &mut PaperLedger, event: JournalEvent) {
    assert_eq!(ledger.apply(event), Ok(ApplyOutcome::Applied));
}

fn rejected(ledger: &mut PaperLedger, event: JournalEvent, error: LedgerError) {
    let before = ledger.snapshot().unwrap();
    let journal = ledger.journal().to_vec();
    assert_eq!(ledger.apply(event), Err(error));
    assert_eq!(ledger.snapshot().unwrap(), before);
    assert_eq!(ledger.journal(), journal.as_slice());
}

#[test]
fn reservations_partial_fills_fees_and_exact_cost_basis() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("buy", "BTC", Side::Buy, 10, 100, 20));
    let s = ledger.snapshot().unwrap();
    assert_eq!((s.cash, s.reserved_cash, s.available_cash), (10_000, 1_020, 8_980));
    accepted(&mut ledger, fill("f1", "buy", 4, 90, 5));
    let s = ledger.snapshot().unwrap();
    assert_eq!((s.cash, s.reserved_cash, s.available_cash), (9_635, 615, 9_020));
    assert_eq!(s.orders["buy"].status, Status::PartiallyFilled);
    accepted(&mut ledger, fill("f2", "buy", 6, 95, 10));
    let s = ledger.snapshot().unwrap();
    assert_eq!((s.cash, s.reserved_cash), (9_055, 0));
    assert_eq!(s.holdings["BTC"], Holding { lots: 10, cost_basis: 945 });

    accepted(&mut ledger, submit("sell1", "BTC", Side::Sell, 3, 120, 3));
    assert_eq!(ledger.available_lots("BTC"), Ok(7));
    accepted(&mut ledger, fill("s1", "sell1", 3, 120, 3));
    let s = ledger.snapshot().unwrap();
    assert_eq!(s.realized_pnl, 74);
    assert_eq!(s.holdings["BTC"], Holding { lots: 7, cost_basis: 662 });

    accepted(&mut ledger, submit("sell2", "BTC", Side::Sell, 7, 110, 7));
    accepted(&mut ledger, fill("s2", "sell2", 7, 110, 7));
    let s = ledger.snapshot().unwrap();
    assert!(s.holdings.is_empty());
    assert_eq!(s.realized_pnl, 175);
    assert_eq!(s.cash, 10_175);
}

#[test]
fn partial_cancel_releases_only_unspent_cash_and_unsold_lots() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 10, 100, 20));
    accepted(&mut ledger, fill("f", "b", 4, 90, 5));
    accepted(&mut ledger, cancel("c", "b"));
    let s = ledger.snapshot().unwrap();
    assert_eq!((s.cash, s.reserved_cash, s.available_cash), (9_635, 0, 9_635));
    assert_eq!(s.holdings["BTC"].lots, 4);
    assert_eq!(s.orders["b"].filled_lots, 4);
    assert_eq!(s.orders["b"].paid_fee, 5);
    accepted(&mut ledger, submit("s", "BTC", Side::Sell, 4, 100, 5));
    accepted(&mut ledger, fill("sf", "s", 1, 100, 1));
    assert_eq!(ledger.available_lots("BTC"), Ok(0));
    accepted(&mut ledger, cancel("sc", "s"));
    assert_eq!(ledger.available_lots("BTC"), Ok(3));
    rejected(&mut ledger, fill("late", "s", 1, 100, 0), LedgerError::ClosedOrder);
}

#[test]
fn cumulative_fee_limit_and_price_terms_fail_atomically() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 2, 100, 5));
    accepted(&mut ledger, fill("f", "b", 1, 100, 4));
    rejected(&mut ledger, fill("overfee", "b", 1, 100, 2), LedgerError::FillOutsideTerms);
    rejected(&mut ledger, fill("badprice", "b", 1, 101, 0), LedgerError::FillOutsideTerms);
    rejected(&mut ledger, fill("overfill", "b", 2, 100, 0), LedgerError::FillOutsideTerms);
    accepted(&mut ledger, fill("ok", "b", 1, 100, 1));
    accepted(&mut ledger, submit("s", "BTC", Side::Sell, 1, 90, 200));
    rejected(&mut ledger, fill("low", "s", 1, 89, 0), LedgerError::FillOutsideTerms);
    rejected(&mut ledger, fill("fee", "s", 1, 90, 91), LedgerError::FillOutsideTerms);
}

#[test]
fn pending_orders_consume_cash_asset_limits_and_inventory() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("b1", "BTC", Side::Buy, 20, 100, 0));
    rejected(&mut ledger, submit("too-big", "BTC", Side::Buy, 31, 100, 0), LedgerError::RiskLimit);
    rejected(&mut ledger, submit("exposure", "BTC", Side::Buy, 21, 100, 0), LedgerError::RiskLimit);
    accepted(&mut ledger, submit("b2", "ETH", Side::Buy, 20, 100, 0));
    rejected(&mut ledger, submit("b3", "SOL", Side::Buy, 1, 100, 0), LedgerError::RiskLimit);
    rejected(&mut ledger, submit("short", "BTC", Side::Sell, 1, 100, 0), LedgerError::InsufficientLots);
    accepted(&mut ledger, fill("f", "b1", 20, 100, 0));
    accepted(&mut ledger, submit("s1", "BTC", Side::Sell, 15, 100, 0));
    rejected(&mut ledger, submit("s2", "BTC", Side::Sell, 6, 100, 0), LedgerError::InsufficientLots);
    accepted(&mut ledger, cancel("c2", "b2"));
    accepted(&mut ledger, submit("b3-ok", "SOL", Side::Buy, 1, 100, 0));

    let mut small = config();
    small.initial_cash = 150;
    let mut ledger = PaperLedger::new(small).unwrap();
    accepted(&mut ledger, submit("first", "BTC", Side::Buy, 1, 100, 10));
    rejected(&mut ledger, submit("second", "ETH", Side::Buy, 1, 40, 1), LedgerError::InsufficientCash);
}

#[test]
fn realized_loss_latches_halt_cancels_buys_and_allows_reducing_sales() {
    let mut cfg = config();
    cfg.max_realized_loss = 100;
    let mut ledger = PaperLedger::new(cfg.clone()).unwrap();
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 10, 100, 0));
    accepted(&mut ledger, fill("bfill", "b", 10, 100, 0));
    accepted(&mut ledger, submit("pending", "ETH", Side::Buy, 1, 100, 0));
    accepted(&mut ledger, submit("s", "BTC", Side::Sell, 5, 80, 0));
    accepted(&mut ledger, fill("loss", "s", 5, 80, 0));
    let s = ledger.snapshot().unwrap();
    assert_eq!(s.realized_pnl, -100);
    assert!(s.buys_halted);
    assert_eq!(s.orders["pending"].status, Status::Canceled);
    assert_eq!(s.reserved_cash, 0);
    rejected(&mut ledger, submit("newbuy", "ETH", Side::Buy, 1, 100, 0), LedgerError::BuysHalted);
    rejected(&mut ledger, fill("late", "pending", 1, 100, 0), LedgerError::ClosedOrder);
    accepted(&mut ledger, submit("exit", "BTC", Side::Sell, 5, 200, 0));
    accepted(&mut ledger, fill("gain", "exit", 5, 200, 0));
    assert_eq!(ledger.snapshot().unwrap().realized_pnl, 400);
    assert!(ledger.snapshot().unwrap().buys_halted);
    let restored = PaperLedger::replay(cfg, ledger.journal().to_vec()).unwrap();
    assert_eq!(restored.snapshot().unwrap(), ledger.snapshot().unwrap());
}

#[test]
fn duplicate_retries_and_conflicting_ids_do_not_change_state() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    let order = submit("b", "BTC", Side::Buy, 2, 100, 0);
    accepted(&mut ledger, order.clone());
    let trade = fill("f", "b", 1, 100, 0);
    accepted(&mut ledger, trade.clone());
    let before = ledger.snapshot().unwrap();
    assert_eq!(ledger.apply(order.clone()), Ok(ApplyOutcome::Duplicate));
    assert_eq!(ledger.apply(trade.clone()), Ok(ApplyOutcome::Duplicate));
    assert_eq!(ledger.snapshot().unwrap(), before);
    assert_eq!(ledger.journal().len(), 2);
    rejected(&mut ledger, fill("f", "b", 1, 99, 0), LedgerError::EventIdConflict);
    rejected(&mut ledger, event("different", order.action.clone()), LedgerError::OrderIdConflict);
    let restored = PaperLedger::replay(config(), ledger.journal().to_vec()).unwrap();
    assert_eq!(restored.snapshot().unwrap(), before);
    assert_eq!(restored.journal(), ledger.journal());
    assert_eq!(
        PaperLedger::replay(config(), [order, trade.clone(), trade]).unwrap_err(),
        LedgerError::DuplicateReplayEvent
    );
    assert_eq!(
        PaperLedger::replay(config(), [fill("f", "missing", 1, 100, 0)]).unwrap_err(),
        LedgerError::UnknownOrder
    );
}

#[test]
fn overflow_invalid_units_and_unknown_orders_fail_without_mutation() {
    let mut cfg = config();
    cfg.initial_cash = u64::MAX;
    cfg.max_order_cost = u64::MAX;
    cfg.max_asset_cost = u64::MAX;
    let mut ledger = PaperLedger::new(cfg).unwrap();
    rejected(&mut ledger, submit("bad", "BTC", Side::Buy, u64::MAX, 2, 0), LedgerError::ArithmeticOverflow);
    rejected(&mut ledger, submit("fee-overflow", "BTC", Side::Buy, 1, u64::MAX, 1), LedgerError::ArithmeticOverflow);
    rejected(&mut ledger, submit("zero", "BTC", Side::Buy, 0, 1, 0), LedgerError::InvalidInput);
    rejected(&mut ledger, submit("bad-asset", "btc", Side::Buy, 1, 1, 0), LedgerError::InvalidInput);
    rejected(&mut ledger, submit("same", "USDT", Side::Buy, 1, 1, 0), LedgerError::InvalidInput);
    rejected(&mut ledger, cancel("unknown", "no-order"), LedgerError::UnknownOrder);
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 1, 1, 0));
    accepted(&mut ledger, fill("bf", "b", 1, 1, 0));
    accepted(&mut ledger, submit("s", "BTC", Side::Sell, 1, 2, 0));
    rejected(&mut ledger, fill("overflow", "s", 1, 2, 0), LedgerError::ArithmeticOverflow);
    assert_eq!(ledger.snapshot().unwrap().holdings["BTC"].lots, 1);
}

#[test]
fn manual_halt_replays_and_keeps_partial_inventory() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 3, 100, 3));
    accepted(&mut ledger, fill("f", "b", 1, 100, 1));
    accepted(&mut ledger, event("stop", Action::HaltBuys));
    let snapshot = ledger.snapshot().unwrap();
    assert!(snapshot.buys_halted);
    assert_eq!(snapshot.reserved_cash, 0);
    assert_eq!(snapshot.holdings["BTC"].lots, 1);
    assert_eq!(snapshot.orders["b"].status, Status::Canceled);
    assert_eq!(
        PaperLedger::replay(config(), ledger.journal().to_vec()).unwrap().snapshot().unwrap(),
        snapshot
    );
}

#[test]
fn multiple_buys_aggregate_basis_and_full_exit_assigns_rounding_remainder() {
    let mut ledger = PaperLedger::new(config()).unwrap();
    accepted(&mut ledger, submit("a", "BTC", Side::Buy, 2, 10, 1));
    accepted(&mut ledger, fill("af", "a", 2, 10, 1));
    accepted(&mut ledger, submit("b", "BTC", Side::Buy, 1, 20, 0));
    accepted(&mut ledger, fill("bf", "b", 1, 20, 0));
    assert_eq!(ledger.snapshot().unwrap().holdings["BTC"].cost_basis, 41);
    accepted(&mut ledger, submit("s", "BTC", Side::Sell, 3, 15, 0));
    for id in ["s1", "s2", "s3"] {
        accepted(&mut ledger, fill(id, "s", 1, 15, 0));
    }
    let snapshot = ledger.snapshot().unwrap();
    assert!(snapshot.holdings.is_empty());
    assert_eq!(snapshot.realized_pnl, 4);
    assert_eq!(snapshot.cash, 10_004);
}
