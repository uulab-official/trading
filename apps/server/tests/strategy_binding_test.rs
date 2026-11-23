use chrono::{TimeZone, Utc};
use trading_server::core::models::{normalize_market_symbol, Candle};
use trading_server::strategy::ma_cross::MaCrossStrategy;
use trading_server::strategy::{Signal, StrategyBinding};

fn candle(symbol: &str, timeframe: &str, minute: i64, price: f64) -> Candle {
    Candle {
        symbol: symbol.into(),
        timeframe: timeframe.into(),
        timestamp: Utc.timestamp_opt(1_700_000_000 + minute * 60, 0).unwrap(),
        open: price,
        high: price,
        low: price,
        close: price,
        volume: 0.0,
    }
}

fn binding(symbol: &str, timeframe: &str) -> StrategyBinding {
    StrategyBinding::new(
        symbol,
        timeframe,
        Box::new(MaCrossStrategy::new("fixture".into(), 1, 2)),
    )
    .unwrap()
}

#[test]
fn interleaved_symbols_match_independent_strategy_results() {
    let mut btc = binding("BTC/USDT", "1m");
    let mut eth = binding("ETHUSDT", "1m");
    let mut only_btc = binding("btcusdt", "1m");
    let mut only_eth = binding("eth/usdt", "1m");
    for (minute, (b, e)) in [
        (100.0, 3000.0),
        (101.0, 2000.0),
        (99.0, 2100.0),
        (102.0, 1800.0),
    ]
    .into_iter()
    .enumerate()
    {
        let b = candle("BTCUSDT", "1m", minute as i64, b);
        let e = candle("ETHUSDT", "1m", minute as i64, e);
        assert_eq!(btc.on_candle(&b).unwrap(), only_btc.on_candle(&b).unwrap());
        assert_eq!(eth.on_candle(&b).unwrap(), None);
        assert_eq!(btc.on_candle(&e).unwrap(), None);
        assert_eq!(eth.on_candle(&e).unwrap(), only_eth.on_candle(&e).unwrap());
    }
}

#[test]
fn same_symbol_different_intervals_have_independent_buffers_and_cursors() {
    let mut minute = binding("BTCUSDT", "1m");
    let mut five_minute = binding("BTCUSDT", "5m");
    for index in 0..2 {
        let small = candle("BTCUSDT", "1m", index, 100.0 + index as f64);
        let large = candle("BTCUSDT", "5m", index * 5, 1000.0 - index as f64);
        assert!(minute.on_candle(&small).unwrap().is_some());
        assert_eq!(five_minute.on_candle(&small).unwrap(), None);
        assert_eq!(minute.on_candle(&large).unwrap(), None);
        let result = five_minute.on_candle(&large).unwrap();
        assert_eq!(
            result,
            Some(if index == 0 {
                Signal::Hold
            } else {
                Signal::Sell
            })
        );
    }
}

#[test]
fn duplicate_and_old_candles_do_not_advance_the_strategy() {
    let mut tested = binding("BTCUSDT", "1m");
    let mut control = binding("BTCUSDT", "1m");
    for (minute, price) in [(1, 100.0), (2, 101.0), (3, 99.0)] {
        let next = candle("BTCUSDT", "1m", minute, price);
        assert_eq!(
            tested.on_candle(&next).unwrap(),
            control.on_candle(&next).unwrap()
        );
        assert_eq!(tested.on_candle(&next).unwrap(), None);
        assert_eq!(
            tested
                .on_candle(&candle("BTCUSDT", "1m", minute - 1, 100000.0))
                .unwrap(),
            None
        );
    }
}

#[test]
fn invalid_candle_cannot_poison_the_cursor_or_strategy() {
    let mut tested = binding("BTCUSDT", "1m");
    let valid = candle("BTCUSDT", "1m", 1, 100.0);
    let mut invalid = valid.clone();
    invalid.close = f64::NAN;
    assert!(tested.on_candle(&invalid).is_err());
    assert_eq!(tested.on_candle(&valid).unwrap(), Some(Signal::Hold));
    assert_eq!(
        tested
            .on_candle(&candle("BTCUSDT", "1m", 2, 101.0))
            .unwrap(),
        Some(Signal::Buy)
    );
}

#[test]
fn legacy_candle_is_not_assigned_a_guessed_timeframe() {
    let mut value = serde_json::to_value(candle("BTCUSDT", "1m", 1, 100.0)).unwrap();
    value.as_object_mut().unwrap().remove("timeframe");
    let legacy: Candle = serde_json::from_value(value).unwrap();
    assert_eq!(legacy.timeframe, "");
    assert!(legacy.validate_closed().is_err());
    assert_eq!(binding("BTCUSDT", "1m").on_candle(&legacy).unwrap(), None);
}

#[test]
fn ambiguous_symbol_forms_are_not_normalized_silently() {
    for symbol in [
        "",
        "BTC//USDT",
        "BTC/",
        "/USDT",
        "BTC USDT",
        " BTCUSDT",
        "BTC?x=y",
        "비트코인",
    ] {
        assert!(normalize_market_symbol(symbol).is_err());
    }
    assert_eq!(normalize_market_symbol("btc/usdt").unwrap(), "BTCUSDT");
}
