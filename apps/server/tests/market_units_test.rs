use trading_server::core::{
    market_units::{FillRequest, LimitRequest, MarketCatalog, MarketRules, UnitError},
    paper_ledger::{Action, ApplyOutcome, JournalEvent, LedgerError, PaperConfig, PaperLedger, Side},
};

fn config(quote: &str) -> PaperConfig {
    PaperConfig {
        quote_asset: quote.into(),
        initial_cash: 1_000_000_000,
        max_order_cost: 1_000_000_000,
        max_asset_cost: 1_000_000_000,
        max_assets: 2,
        max_realized_loss: 100_000_000,
    }
}

fn crypto_rules() -> MarketRules {
    // Synthetic test fixture, not current exchange metadata.
    MarketRules {
        base_asset: "BTC".into(),
        lot_size: "0.00001".into(),
        price_tick: "0.01".into(),
        min_quantity: "0.00002".into(),
        max_quantity: Some("0.01".into()),
        min_price: Some("100".into()),
        max_price: Some("100000".into()),
        min_notional: "1".into(),
        max_notional: Some("100".into()),
    }
}

fn crypto() -> MarketCatalog {
    let mut catalog = MarketCatalog::new("USDT", 8).unwrap();
    catalog.register(crypto_rules()).unwrap();
    catalog
}

fn request() -> LimitRequest<'static> {
    LimitRequest {
        order_id: "buy",
        asset: "BTC",
        side: Side::Buy,
        quantity: "0.00003",
        price: "40000.01",
        max_fee: "0.00000011",
    }
}

fn fill_request() -> FillRequest<'static> {
    FillRequest {
        event_id: "fill1",
        quantity: "0.00001",
        price: "40000",
        fee: "0.00000003",
    }
}

#[test]
fn crypto_tick_and_lot_convert_exactly_and_apply_to_ledger() {
    let catalog = crypto();
    let cfg = config("USDT");
    let terms = catalog.order_terms(&cfg, request()).unwrap();
    assert_eq!(terms.lots, 3);
    assert_eq!(terms.limit_price, 40_000_010);
    assert_eq!(terms.max_fee, 11);
    let mut ledger = PaperLedger::new(cfg.clone()).unwrap();
    assert_eq!(
        ledger.apply(JournalEvent { event_id: "submit".into(), action: Action::Submit(terms.clone()) }),
        Ok(ApplyOutcome::Applied)
    );
    assert_eq!(ledger.snapshot().unwrap().reserved_cash, 120_000_041);
    let fill = catalog.fill_event(&cfg, &terms, fill_request()).unwrap();
    assert_eq!(fill.action, Action::Fill {
        order_id: "buy".into(), lots: 1, price: 40_000_000, fee: 3,
    });
    assert_eq!(ledger.apply(fill.clone()), Ok(ApplyOutcome::Applied));
    let state = ledger.snapshot().unwrap();
    assert_eq!(state.cash, 959_999_997);
    assert_eq!(state.reserved_cash, 80_000_028);
    assert_eq!(state.holdings["BTC"].cost_basis, 40_000_003);
    assert_eq!(ledger.apply(fill), Ok(ApplyOutcome::Duplicate));
}

#[test]
fn integer_currency_stock_fixture_supports_buy_and_sell() {
    let mut catalog = MarketCatalog::new("KRW", 0).unwrap();
    catalog.register(MarketRules {
        base_asset: "005930".into(),
        lot_size: "1".into(), price_tick: "50".into(), min_quantity: "1".into(),
        max_quantity: Some("100".into()), min_price: None, max_price: None,
        min_notional: "0".into(), max_notional: None,
    }).unwrap();
    let cfg = config("KRW");
    let terms = catalog.order_terms(&cfg, LimitRequest {
        order_id: "stock", asset: "005930", side: Side::Buy,
        quantity: "3", price: "10000", max_fee: "100",
    }).unwrap();
    assert_eq!((terms.lots, terms.limit_price, terms.max_fee), (3, 10_000, 100));
    let event = catalog.fill_event(&cfg, &terms, FillRequest {
        event_id: "buy-fill", quantity: "3", price: "9950", fee: "30",
    }).unwrap();
    let mut ledger = PaperLedger::new(cfg.clone()).unwrap();
    ledger.apply(JournalEvent { event_id: "stock-submit".into(), action: Action::Submit(terms) }).unwrap();
    ledger.apply(event).unwrap();
    let sell = catalog.order_terms(&cfg, LimitRequest {
        order_id: "stock-sell", asset: "005930", side: Side::Sell,
        quantity: "3", price: "10100", max_fee: "40",
    }).unwrap();
    let event = catalog.fill_event(&cfg, &sell, FillRequest {
        event_id: "sell-fill", quantity: "3", price: "10150", fee: "40",
    }).unwrap();
    ledger.apply(JournalEvent { event_id: "sell-submit".into(), action: Action::Submit(sell) }).unwrap();
    ledger.apply(event).unwrap();
    let snapshot = ledger.snapshot().unwrap();
    assert!(snapshot.holdings.is_empty());
    assert_eq!(snapshot.realized_pnl, 530);
}

#[test]
fn invalid_decimals_and_nonintegral_quote_atoms_are_rejected() {
    let catalog = crypto();
    for raw in ["", ".", ".1", "1.", "1..0", "-1", "+1", "1e-8", "NaN", " 1", "1 ", "１", "1_000",
                "0.0000000000000000001"] {
        assert_eq!(catalog.quote_atoms(raw), Err(UnitError::InvalidDecimal), "{raw}");
    }
    assert_eq!(catalog.quote_atoms("0.000000001"), Err(UnitError::OffGrid));
    assert_eq!(catalog.quote_atoms("0001.230000000000000000"), Ok(123_000_000));
    assert_eq!(catalog.quote_atoms("0.00000000"), Ok(0));
}

#[test]
fn order_quantity_price_and_fee_must_be_exactly_on_grid() {
    let catalog = crypto();
    let cfg = config("USDT");
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { quantity: "0.000031", ..request() }), Err(UnitError::OffGrid));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { price: "40000.001", ..request() }), Err(UnitError::OffGrid));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { max_fee: "0.000000001", ..request() }), Err(UnitError::OffGrid));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { quantity: "0", ..request() }), Err(UnitError::QuantityBounds));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { price: "0", ..request() }), Err(UnitError::PriceBounds));
}

#[test]
fn order_minimum_and_maximum_constraints_are_enforced() {
    let catalog = crypto();
    let cfg = config("USDT");
    for quantity in ["0.00001", "0.01001"] {
        assert_eq!(catalog.order_terms(&cfg, LimitRequest { quantity, ..request() }), Err(UnitError::QuantityBounds));
    }
    for price in ["99.99", "100000.01"] {
        assert_eq!(catalog.order_terms(&cfg, LimitRequest { price, ..request() }), Err(UnitError::PriceBounds));
    }
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { price: "100", ..request() }), Err(UnitError::NotionalBounds));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { quantity: "0.01", ..request() }), Err(UnitError::NotionalBounds));
    assert!(catalog.order_terms(&cfg, LimitRequest { quantity: "0.00002", price: "50000", ..request() }).is_ok());
    assert!(catalog.order_terms(&cfg, LimitRequest { quantity: "0.001", price: "100000", ..request() }).is_ok());
}

#[test]
fn registration_rejects_inexact_grid_bad_bounds_and_duplicate_identity() {
    let mut catalog = MarketCatalog::new("USDT", 8).unwrap();
    let mut rules = crypto_rules();
    rules.lot_size = "0.0000001".into();
    assert_eq!(catalog.register(rules), Err(UnitError::NonIntegralTickLot));
    let mut rules = crypto_rules();
    rules.min_quantity = "0.000021".into();
    assert_eq!(catalog.register(rules), Err(UnitError::OffGrid));
    let mut rules = crypto_rules();
    rules.max_quantity = Some("0.00001".into());
    assert_eq!(catalog.register(rules), Err(UnitError::InvalidRules));
    let mut rules = crypto_rules();
    rules.max_price = Some("99".into());
    assert_eq!(catalog.register(rules), Err(UnitError::InvalidRules));
    let mut rules = crypto_rules();
    rules.max_notional = Some("0.5".into());
    assert_eq!(catalog.register(rules), Err(UnitError::InvalidRules));
    catalog.register(crypto_rules()).unwrap();
    let mut changed = crypto_rules();
    changed.price_tick = "1".into();
    assert_eq!(catalog.register(changed), Err(UnitError::DuplicateAsset));
    assert_eq!(catalog.order_terms(&config("USDT"), request()).unwrap().limit_price, 40_000_010);
}

#[test]
fn wrong_quote_asset_and_unregistered_assets_never_convert() {
    let catalog = crypto();
    assert_eq!(catalog.quote_asset(), "USDT");
    assert_eq!(catalog.quote_scale(), 8);
    assert_eq!(catalog.order_terms(&config("USD"), request()), Err(UnitError::QuoteMismatch));
    assert_eq!(catalog.order_terms(&config("USDT"), LimitRequest { asset: "ETH", ..request() }), Err(UnitError::UnknownAsset));
    assert_eq!(catalog.order_terms(&config("USDT"), LimitRequest { order_id: "bad id", ..request() }), Err(UnitError::InvalidId));
    assert!(MarketCatalog::new("usdt", 8).is_err());
    assert!(MarketCatalog::new("USDT", 19).is_err());
}

#[test]
fn partial_fill_uses_original_order_terms_without_order_minimum() {
    let catalog = crypto();
    let cfg = config("USDT");
    let terms = catalog.order_terms(&cfg, request()).unwrap();
    assert!(catalog.fill_event(&cfg, &terms, fill_request()).is_ok());
    assert_eq!(catalog.fill_event(&cfg, &terms, FillRequest { quantity: "0.00004", ..fill_request() }), Err(UnitError::FillOutsideTerms));
    assert_eq!(catalog.fill_event(&cfg, &terms, FillRequest { price: "40000.02", ..fill_request() }), Err(UnitError::FillOutsideTerms));
    assert_eq!(catalog.fill_event(&cfg, &terms, FillRequest { fee: "0.00000012", ..fill_request() }), Err(UnitError::FillOutsideTerms));
    assert_eq!(catalog.fill_event(&cfg, &terms, FillRequest { price: "40000.001", ..fill_request() }), Err(UnitError::OffGrid));
    let sell = catalog.order_terms(&cfg, LimitRequest { side: Side::Sell, ..request() }).unwrap();
    assert_eq!(catalog.fill_event(&cfg, &sell, fill_request()), Err(UnitError::FillOutsideTerms));
    let mut foreign = terms;
    foreign.limit_price += 1;
    assert_eq!(catalog.fill_event(&cfg, &foreign, fill_request()), Err(UnitError::OffGrid));
}

#[test]
fn ledger_retains_cumulative_fee_and_remaining_quantity_authority() {
    let catalog = crypto();
    let cfg = config("USDT");
    let terms = catalog.order_terms(&cfg, request()).unwrap();
    let mut ledger = PaperLedger::new(cfg.clone()).unwrap();
    ledger.apply(JournalEvent { event_id: "submit".into(), action: Action::Submit(terms.clone()) }).unwrap();
    ledger.apply(catalog.fill_event(&cfg, &terms, FillRequest { fee: "0.00000009", ..fill_request() }).unwrap()).unwrap();
    let state = ledger.snapshot().unwrap();
    let second = catalog.fill_event(&cfg, &terms, FillRequest { event_id: "fill2", ..fill_request() }).unwrap();
    assert_eq!(ledger.apply(second), Err(LedgerError::FillOutsideTerms));
    assert_eq!(ledger.snapshot().unwrap(), state);
}

#[test]
fn arithmetic_boundaries_fail_closed_without_float_or_wrapping() {
    let mut catalog = MarketCatalog::new("KRW", 0).unwrap();
    catalog.register(MarketRules {
        base_asset: "STOCK".into(), lot_size: "1".into(), price_tick: "1".into(),
        min_quantity: "1".into(), max_quantity: None, min_price: None, max_price: None,
        min_notional: "0".into(), max_notional: None,
    }).unwrap();
    let cfg = config("KRW");
    let limit = LimitRequest {
        order_id: "order", asset: "STOCK", side: Side::Buy,
        quantity: "1", price: "18446744073709551615", max_fee: "0",
    };
    assert_eq!(catalog.quote_atoms("18446744073709551615"), Ok(u64::MAX));
    assert_eq!(catalog.quote_atoms("18446744073709551616"), Err(UnitError::ArithmeticOverflow));
    assert_eq!(catalog.order_terms(&cfg, limit).unwrap().limit_price, u64::MAX);
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { quantity: "2", ..limit }), Err(UnitError::ArithmeticOverflow));
    assert_eq!(catalog.order_terms(&cfg, LimitRequest { max_fee: "1", ..limit }), Err(UnitError::ArithmeticOverflow));
}

#[test]
fn rational_reduction_handles_non_power_of_ten_steps() {
    let mut catalog = MarketCatalog::new("USD", 4).unwrap();
    catalog.register(MarketRules {
        base_asset: "FUND".into(), lot_size: "0.025".into(), price_tick: "0.04".into(),
        min_quantity: "0.025".into(), max_quantity: None, min_price: None, max_price: None,
        min_notional: "0".into(), max_notional: None,
    }).unwrap();
    let request = LimitRequest {
        order_id: "fund", asset: "FUND", side: Side::Buy,
        quantity: "0.075", price: "1.24", max_fee: "0.0001",
    };
    let terms = catalog.order_terms(&config("USD"), request).unwrap();
    assert_eq!((terms.lots, terms.limit_price, terms.max_fee), (3, 310, 1));
    assert_eq!(catalog.order_terms(&config("USD"), LimitRequest { quantity: "0.07", ..request }), Err(UnitError::OffGrid));
}
