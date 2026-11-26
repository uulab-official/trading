//! Replayable risk evaluation for simulated spot accounts only.
//! Prices are quote atoms per ledger lot; timestamps are supplied by the caller.
//! No wall clock, external quotes, broker communication or live order authority.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use super::paper_ledger::{AccountSnapshot, Side, Status};

const BPS: u128 = 10_000;
const UTC_DAY_MS: u64 = 86_400_000;
pub const MAX_MARKS: usize = 24;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RiskError {
    InvalidPolicy,
    InvalidObservation,
    MissingMark,
    StaleMark,
    RegressingTime,
    ConflictingMark,
    ArithmeticOverflow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RiskPolicy {
    /// Reaching the loss or drawdown limit halts new simulated buys.
    pub max_daily_loss_bps: u16,
    pub max_drawdown_bps: u16,
    /// Exposure includes holdings and pending buys; exact equality is allowed.
    pub max_total_exposure_bps: u16,
    pub max_asset_exposure_bps: u16,
    pub max_mark_age_ms: u64,
}

impl RiskPolicy {
    pub fn validate(self) -> Result<(), RiskError> {
        if [self.max_daily_loss_bps, self.max_drawdown_bps,
            self.max_total_exposure_bps, self.max_asset_exposure_bps]
            .iter().any(|n| !(1..=10_000).contains(n))
            || self.max_mark_age_ms == 0
            || self.max_mark_age_ms > UTC_DAY_MS
            || self.max_asset_exposure_bps > self.max_total_exposure_bps
        {
            return Err(RiskError::InvalidPolicy);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Mark {
    pub price: u64,
    pub source_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RiskObservation {
    pub at_ms: u64,
    /// Canonical base asset -> source timestamp and quote atoms per lot.
    pub marks: BTreeMap<String, Mark>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Breach {
    DailyLoss,
    Drawdown,
    TotalExposure,
    AssetExposure(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RiskReport {
    pub as_of_ms: u64,
    pub utc_day: u64,
    pub daily_baseline: u64,
    pub equity: u64,
    pub holdings_value: u64,
    pub realized_pnl: i128,
    pub unrealized_pnl: i128,
    pub daily_pnl: i128,
    pub peak_equity: u64,
    pub drawdown_bps: u16,
    pub total_exposure: u64,
    pub asset_exposure: BTreeMap<String, u64>,
    pub breaches: Vec<Breach>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RiskSnapshot {
    pub policy: RiskPolicy,
    /// Historical observation, not a statement of current quote freshness.
    pub last_report: Option<RiskReport>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RiskTracker {
    policy: RiskPolicy,
    initial_cash: u64,
    last: Option<RiskReport>,
    last_frame: Option<RiskObservation>,
    mark_history: BTreeMap<String, Mark>,
}

fn add(left: u64, right: u64) -> Result<u64, RiskError> {
    left.checked_add(right).ok_or(RiskError::ArithmeticOverflow)
}

fn mul(left: u64, right: u64) -> Result<u64, RiskError> {
    left.checked_mul(right).ok_or(RiskError::ArithmeticOverflow)
}

fn valid_asset(asset: &str) -> bool {
    !asset.is_empty() && asset.len() <= 32
        && asset.bytes().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
}

fn exceeds(exposure: u64, equity: u64, limit: u16) -> bool {
    u128::from(exposure) * BPS > u128::from(equity) * u128::from(limit)
}

fn reaches_loss(equity: u64, baseline: u64, limit: u16) -> bool {
    equity < baseline
        && u128::from(baseline - equity) * BPS >= u128::from(baseline) * u128::from(limit)
}

impl RiskTracker {
    pub(crate) fn new(policy: RiskPolicy, initial_cash: u64) -> Result<Self, RiskError> {
        policy.validate()?;
        Ok(Self { policy, initial_cash, last: None, last_frame: None, mark_history: BTreeMap::new() })
    }

    pub(crate) fn snapshot(&self) -> RiskSnapshot {
        RiskSnapshot { policy: self.policy, last_report: self.last.clone() }
    }

    /// Compute without mutating the previous tracker. A pre/post assessment at
    /// the same timestamp is legal only if the exact mark frame is unchanged.
    pub(crate) fn evaluate(
        &self,
        account: &AccountSnapshot,
        observation: &RiskObservation,
    ) -> Result<Self, RiskError> {
        if observation.at_ms == 0 || observation.marks.len() > MAX_MARKS {
            return Err(RiskError::InvalidObservation);
        }
        if let Some(last) = &self.last_frame {
            if observation.at_ms < last.at_ms {
                return Err(RiskError::RegressingTime);
            }
            if observation.at_ms == last.at_ms && observation != last {
                return Err(RiskError::ConflictingMark);
            }
        }
        for (asset, mark) in &observation.marks {
            if !valid_asset(asset) || mark.price == 0 || mark.source_ms == 0
                || mark.source_ms > observation.at_ms
            {
                return Err(RiskError::InvalidObservation);
            }
            if observation.at_ms - mark.source_ms > self.policy.max_mark_age_ms {
                return Err(RiskError::StaleMark);
            }
            if let Some(previous) = self.mark_history.get(asset) {
                if mark.source_ms < previous.source_ms {
                    return Err(RiskError::RegressingTime);
                }
                if mark.source_ms == previous.source_ms && mark.price != previous.price {
                    return Err(RiskError::ConflictingMark);
                }
            }
        }

        let price = |asset: &str| {
            observation.marks.get(asset).map(|m| m.price).ok_or(RiskError::MissingMark)
        };
        let mut holdings_value = 0u64;
        let mut unrealized_pnl = 0i128;
        let mut asset_exposure = BTreeMap::<String, u64>::new();
        for (asset, holding) in &account.holdings {
            let value = mul(holding.lots, price(asset)?)?;
            holdings_value = add(holdings_value, value)?;
            unrealized_pnl = unrealized_pnl.checked_add(i128::from(value) - i128::from(holding.cost_basis))
                .ok_or(RiskError::ArithmeticOverflow)?;
            asset_exposure.insert(asset.clone(), value);
        }
        for order in account.orders.values() {
            if order.terms.side != Side::Buy
                || !matches!(order.status, Status::Open | Status::PartiallyFilled)
            {
                continue;
            }
            let remaining = order.terms.lots.checked_sub(order.filled_lots)
                .ok_or(RiskError::InvalidObservation)?;
            let value = mul(remaining, price(&order.terms.asset)?.max(order.terms.limit_price))?;
            let exposure = asset_exposure.entry(order.terms.asset.clone()).or_default();
            *exposure = add(*exposure, value)?;
        }
        let total_exposure = asset_exposure.values().try_fold(0u64, |sum, value| add(sum, *value))?;
        // Cash already includes reserved funds; subtracting reservations here
        // would count the same prospective purchase twice.
        let equity = add(account.cash, holdings_value)?;
        let utc_day = observation.at_ms / UTC_DAY_MS;
        let daily_baseline = match &self.last {
            Some(last) if last.utc_day == utc_day => last.daily_baseline,
            Some(last) => last.equity,
            None => self.initial_cash,
        };
        let peak_equity = self.last.as_ref().map_or(self.initial_cash, |last| last.peak_equity).max(equity);
        let drawdown_bps = ((u128::from(peak_equity - equity) * BPS) / u128::from(peak_equity)) as u16;
        let mut breaches = Vec::new();
        if reaches_loss(equity, daily_baseline, self.policy.max_daily_loss_bps) {
            breaches.push(Breach::DailyLoss);
        }
        if reaches_loss(equity, peak_equity, self.policy.max_drawdown_bps) {
            breaches.push(Breach::Drawdown);
        }
        if exceeds(total_exposure, equity, self.policy.max_total_exposure_bps) {
            breaches.push(Breach::TotalExposure);
        }
        for (asset, exposure) in &asset_exposure {
            if exceeds(*exposure, equity, self.policy.max_asset_exposure_bps) {
                breaches.push(Breach::AssetExposure(asset.clone()));
            }
        }
        let report = RiskReport {
            as_of_ms: observation.at_ms, utc_day, daily_baseline, equity, holdings_value,
            realized_pnl: account.realized_pnl, unrealized_pnl,
            daily_pnl: i128::from(equity) - i128::from(daily_baseline),
            peak_equity, drawdown_bps, total_exposure, asset_exposure, breaches,
        };
        let mut next = self.clone();
        next.last = Some(report);
        next.last_frame = Some(observation.clone());
        next.mark_history.extend(observation.marks.iter().map(|(asset, mark)| (asset.clone(), *mark)));
        Ok(next)
    }

    pub(crate) fn breached(&self) -> bool {
        self.last.as_ref().is_some_and(|report| !report.breaches.is_empty())
    }
}
