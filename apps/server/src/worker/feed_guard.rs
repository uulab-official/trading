//! Observation-only source freshness and continuity checks. No account or
//! financial state is read or modified by this guard.

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Datelike, Months, Timelike, Utc};
use std::collections::BTreeMap;
use tokio::time::{Duration, Instant};

use crate::core::{
    config::Config,
    models::{normalize_market_symbol, Candle, Ticker},
};

const PRICE_MAX_AGE_MS: i64 = 90_000;
const FUTURE_SKEW_MS: i64 = 5_000;
const SILENCE_LIMIT: Duration = Duration::from_secs(90);
const CANDLE_GRACE_MS: i64 = 90_000;

#[derive(Debug, Clone)]
struct SeenTicker {
    source_ms: i64,
    price: f64,
    received: Instant,
}

pub(super) struct FeedGuard {
    started: Instant,
    started_ms: i64,
    tickers: BTreeMap<String, Option<SeenTicker>>,
    closed: BTreeMap<(String, String), Option<i64>>,
}

fn interval_ms(timeframe: &str) -> Result<i64> {
    Ok(match timeframe {
        "1s" => 1_000,
        "1m" => 60_000,
        "3m" => 180_000,
        "5m" => 300_000,
        "15m" => 900_000,
        "30m" => 1_800_000,
        "1h" => 3_600_000,
        "2h" => 7_200_000,
        "4h" => 14_400_000,
        "6h" => 21_600_000,
        "8h" => 28_800_000,
        "12h" => 43_200_000,
        "1d" => 86_400_000,
        "3d" => 259_200_000,
        "1w" => 604_800_000,
        // Only used as an upper bound for the first monthly bar deadline.
        "1M" => 31 * 86_400_000,
        _ => anyhow::bail!("Unsupported observation interval"),
    })
}

fn next_open(stamp: i64, timeframe: &str) -> Result<i64> {
    if timeframe == "1M" {
        let open = DateTime::<Utc>::from_timestamp_millis(stamp)
            .context("Invalid monthly observation time")?;
        ensure!(
            open.day() == 1 && open.num_seconds_from_midnight() == 0 && open.nanosecond() == 0,
            "Invalid monthly observation boundary"
        );
        return open.checked_add_months(Months::new(1))
            .map(|next| next.timestamp_millis())
            .context("Monthly observation time overflow");
    }
    stamp.checked_add(interval_ms(timeframe)?).context("Observation time overflow")
}

fn check_time(source_ms: i64, now_ms: i64, max_age_ms: i64) -> Result<()> {
    ensure!(source_ms >= 0 && now_ms > 0, "Invalid observation timestamp");
    let age = i128::from(now_ms) - i128::from(source_ms);
    ensure!(age >= -i128::from(FUTURE_SKEW_MS), "Future market observation");
    ensure!(age <= i128::from(max_age_ms), "Stale market observation");
    Ok(())
}

impl FeedGuard {
    pub(super) fn new(config: &Config, now_ms: i64, started: Instant) -> Result<Self> {
        ensure!(now_ms > 0, "Invalid observation clock");
        let mut tickers = BTreeMap::new();
        let mut closed = BTreeMap::new();
        for strategy in config.strategies.iter().filter(|s| s.enabled) {
            let symbol = normalize_market_symbol(&strategy.symbol)?;
            interval_ms(&strategy.timeframe)?;
            tickers.insert(symbol.clone(), None);
            closed.insert((symbol, strategy.timeframe.clone()), None);
        }
        ensure!(!tickers.is_empty(), "No observation subscriptions");
        Ok(Self { started, started_ms: now_ms, tickers, closed })
    }

    /// Unknown streams and identical retransmissions never refresh liveness.
    /// Equal source time with a different last price is a conflicting observation.
    pub(super) fn ticker(&mut self, ticker: &Ticker, now_ms: i64, received: Instant) -> Result<bool> {
        let Some(previous) = self.tickers.get_mut(&ticker.symbol) else {
            return Ok(false);
        };
        ensure!(ticker.last.is_finite() && ticker.last > 0.0, "Invalid ticker price");
        let source_ms = ticker.timestamp.timestamp_millis();
        check_time(source_ms, now_ms, PRICE_MAX_AGE_MS)?;
        if let Some(old) = previous {
            ensure!(source_ms >= old.source_ms, "Regressing ticker source time");
            if source_ms == old.source_ms {
                ensure!(ticker.last == old.price, "Conflicting ticker at equal source time");
                return Ok(false);
            }
        }
        *previous = Some(SeenTicker { source_ms, price: ticker.last, received });
        Ok(true)
    }

    pub(super) fn ready(&self) -> bool {
        self.tickers.values().all(Option::is_some)
    }

    /// Validate the next bar before writing to SQLite or advancing a strategy.
    /// Exact same open time is allowed only for the database overlap check.
    /// No gap is silently repaired from unqualified legacy data.
    pub(super) fn check_candle(&self, candle: &Candle, now_ms: i64) -> Result<()> {
        candle.validate_closed()?;
        let key = (candle.symbol.clone(), candle.timeframe.clone());
        let last = self.closed.get(&key).context("Unsubscribed closed candle")?;
        let stamp = candle.timestamp.timestamp_millis();
        if let Some(previous) = last {
            if stamp == *previous {
                return Ok(());
            }
            ensure!(
                stamp == next_open(*previous, &candle.timeframe)?,
                "Closed candle gap or regression; verified backfill required"
            );
        }
        let end = next_open(stamp, &candle.timeframe)?.checked_sub(1)
            .context("Candle close overflow")?;
        check_time(end, now_ms, CANDLE_GRACE_MS)?;
        Ok(())
    }

    /// Commit the cursor only after immutable storage has succeeded.
    pub(super) fn stored(&mut self, candle: &Candle) -> Result<()> {
        let key = (candle.symbol.clone(), candle.timeframe.clone());
        *self.closed.get_mut(&key).context("Unsubscribed closed candle")? =
            Some(candle.timestamp.timestamp_millis());
        Ok(())
    }

    pub(super) fn deadlines(&self, now_ms: i64, instant: Instant) -> Result<()> {
        ensure!(
            i128::from(now_ms) >= i128::from(self.started_ms) - i128::from(FUTURE_SKEW_MS),
            "Observation wall clock regressed"
        );
        for seen in self.tickers.values() {
            let since = match seen {
                Some(ticker) => {
                    check_time(ticker.source_ms, now_ms, PRICE_MAX_AGE_MS)?;
                    ticker.received
                }
                None => self.started,
            };
            ensure!(
                instant.saturating_duration_since(since) <= SILENCE_LIMIT,
                "Subscribed ticker receive deadline exceeded"
            );
        }
        for ((_, timeframe), last) in &self.closed {
            let deadline = match last {
                Some(open) => next_open(next_open(*open, timeframe)?, timeframe)?
                    .checked_sub(1).context("Candle deadline overflow")?,
                None => self.started_ms.checked_add(interval_ms(timeframe)?)
                    .context("Initial candle deadline overflow")?,
            };
            ensure!(
                i128::from(now_ms) <= i128::from(deadline) + i128::from(CANDLE_GRACE_MS),
                "Subscribed closed candle deadline exceeded"
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const START: i64 = 1_700_000_040_000;

    fn fixture() -> Config {
        let mut cfg: Config = toml::from_str(include_str!("../../config/bot.toml")).unwrap();
        cfg.strategies[0].timeframe = "1m".into();
        cfg.strategies[1].timeframe = "1m".into();
        cfg
    }

    fn ticker(symbol: &str, stamp: i64, last: f64) -> Ticker {
        Ticker {
            symbol: symbol.into(), bid: last, ask: last, last, volume: 1.0,
            timestamp: DateTime::from_timestamp_millis(stamp).unwrap(),
        }
    }

    fn candle(symbol: &str, stamp: i64) -> Candle {
        Candle {
            symbol: symbol.into(), timeframe: "1m".into(),
            timestamp: DateTime::from_timestamp_millis(stamp).unwrap(),
            open: 100.0, high: 100.0, low: 100.0, close: 100.0, volume: 1.0,
        }
    }

    #[test]
    fn readiness_requires_each_subscribed_symbol_and_no_unrelated_ticker() {
        let now = Instant::now();
        let mut guard = FeedGuard::new(&fixture(), START, now).unwrap();
        assert!(!guard.ticker(&ticker("SOLUSDT", START, 10.0), START, now).unwrap());
        assert!(guard.ticker(&ticker("BTCUSDT", START, 100.0), START, now).unwrap());
        assert!(!guard.ready());
        assert!(guard.ticker(&ticker("ETHUSDT", START, 50.0), START, now).unwrap());
        assert!(guard.ready());
    }

    #[test]
    fn old_future_regressing_conflicting_and_duplicate_tickers_do_not_refresh() {
        let now = Instant::now();
        let mut guard = FeedGuard::new(&fixture(), START, now).unwrap();
        for stamp in [START - PRICE_MAX_AGE_MS - 1, START + FUTURE_SKEW_MS + 1] {
            assert!(guard.ticker(&ticker("BTCUSDT", stamp, 100.0), START, now).is_err());
        }
        assert!(guard.ticker(&ticker("BTCUSDT", START, 100.0), START, now).unwrap());
        assert!(!guard.ticker(&ticker("BTCUSDT", START, 100.0), START + 1000, now).unwrap());
        assert!(guard.ticker(&ticker("BTCUSDT", START, 101.0), START, now).is_err());
        assert!(guard.ticker(&ticker("BTCUSDT", START - 1, 100.0), START, now).is_err());
    }

    #[test]
    fn one_active_symbol_cannot_hide_a_silent_subscribed_symbol() {
        let now = Instant::now();
        let mut guard = FeedGuard::new(&fixture(), START, now).unwrap();
        guard.ticker(&ticker("BTCUSDT", START, 100.0), START, now).unwrap();
        let later = now + Duration::from_secs(91);
        guard.ticker(&ticker("BTCUSDT", START + 91_000, 101.0), START + 91_000, later).unwrap();
        assert!(guard.deadlines(START + 91_000, later).unwrap_err().to_string().contains("ticker receive"));
    }

    #[test]
    fn gap_is_refused_before_cursor_advances_and_next_bar_can_be_checked() {
        let now = Instant::now();
        let mut guard = FeedGuard::new(&fixture(), START, now).unwrap();
        let first = candle("BTCUSDT", START);
        guard.check_candle(&first, START + 60_000).unwrap();
        guard.stored(&first).unwrap();
        assert!(guard.check_candle(&first, START + 500_000).is_ok());
        let gap = candle("BTCUSDT", START + 120_000);
        assert!(guard.check_candle(&gap, START + 180_000).is_err());
        let next = candle("BTCUSDT", START + 60_000);
        guard.check_candle(&next, START + 120_000).unwrap();
        guard.stored(&next).unwrap();
        assert!(guard.check_candle(&first, START + 120_000).is_err());
    }

    #[test]
    fn stale_and_forming_closed_candles_are_refused() {
        let now = Instant::now();
        let guard = FeedGuard::new(&fixture(), START, now).unwrap();
        let first = candle("BTCUSDT", START);
        assert!(guard.check_candle(&first, START + 10_000).is_err());
        assert!(guard.check_candle(&first, START + 160_000).is_err());
    }

    #[test]
    fn active_tickers_do_not_hide_a_missing_closed_bar() {
        let now = Instant::now();
        let mut guard = FeedGuard::new(&fixture(), START, now).unwrap();
        let first = candle("BTCUSDT", START);
        guard.check_candle(&first, START + 60_000).unwrap();
        guard.stored(&first).unwrap();
        let stamp = START + 220_000;
        let later = now + Duration::from_secs(220);
        for symbol in ["BTCUSDT", "ETHUSDT"] {
            guard.ticker(&ticker(symbol, stamp, 100.0), stamp, later).unwrap();
        }
        assert!(guard.deadlines(stamp, later).unwrap_err().to_string().contains("candle deadline"));
    }

    #[test]
    fn month_boundaries_are_calendar_based() {
        let feb = DateTime::parse_from_rfc3339("2024-02-01T00:00:00Z").unwrap().timestamp_millis();
        let mar = DateTime::parse_from_rfc3339("2024-03-01T00:00:00Z").unwrap().timestamp_millis();
        assert_eq!(next_open(feb, "1M").unwrap(), mar);
        assert!(next_open(feb + 1000, "1M").is_err());
    }
}
