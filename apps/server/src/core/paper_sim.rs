//! Bounded, deterministic historical paper experiment. Never starts a worker,
//! sends an order, loads credentials, or connects to a network.

use anyhow::{anyhow, ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, fmt::Write as _, fs, io::Read, path::Path};

use super::{
    market_units::{MarketCatalog, MarketRules, UnitError},
    models::{Candle, normalize_market_symbol},
    paper_ledger::{Action, JournalEvent, LedgerError, OrderTerms, PaperConfig, PaperLedger, Side},
    paper_risk::{Mark, RiskObservation, RiskPolicy},
    paper_store::{JournalCheckpoint, PaperStore, MAX_EVENTS},
};
use crate::strategy::{ma_cross::MaCrossStrategy, rsi::RsiStrategy, Signal, Strategy, StrategyBinding};

const MAX_BARS: usize = 240;
/// Forward sessions have a larger but still bounded fixed-start history.
pub const MAX_FORWARD_BARS: usize = 2048;
const MAX_STRATEGIES: usize = 8;
const MAX_INPUT_BYTES: u64 = 2 * 1024 * 1024;
const MAX_FORWARD_INPUT_BYTES: u64 = 4 * 1024 * 1024;
const MAX_EXACT_F64: u64 = (1u64 << 53) - 1;
const BPS: u128 = 10_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayInput {
    pub version: u32,
    pub account: PaperConfig,
    pub risk: RiskPolicy,
    pub quote_scale: u32,
    pub market: MarketRules,
    pub timeframe: String,
    /// Bars before this index warm indicators only; no orders or valuation.
    pub warmup_bars: usize,
    pub execution: Execution,
    pub strategies: Vec<StrategySpec>,
    pub bars: Vec<ReplayBar>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Execution {
    pub buy_lots: u64,
    /// One or more complete bar indices after the signal; never same-bar fill.
    pub delay_bars: usize,
    pub fee_bps: u16,
    pub slippage_bps: u16,
    /// Percentage of PREVIOUS closed bar volume, not future current-bar volume.
    pub participation_bps: u16,
    pub style: OrderStyle,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum OrderStyle {
    Market,
    /// IOC at the delayed opening mark, limit fixed at the signal close.
    Limit { offset_bps: u16 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StrategySpec {
    pub id: String,
    pub strategy: StrategyKind,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum StrategyKind {
    MaCross { fast: usize, slow: usize },
    Rsi { period: usize, oversold: u8, overbought: u8 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayBar {
    pub open_ms: u64,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: String,
}

#[derive(Debug, Clone)]
struct Bar {
    open_ms: u64,
    close_ms: u64,
    open: u64,
    high: u64,
    low: u64,
    close: u64,
    volume: u64,
}

#[derive(Debug, Clone)]
struct Intent {
    side: Side,
    lots: u64,
    signal_ms: u64,
    signal_price: u64,
    due: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AttemptStatus {
    Filled,
    PartialThenCanceled,
    LimitNotMet,
    NoLiquidity,
    InvalidTerms,
    RiskOrBalanceRejected,
    Expired,
    AwaitingNextBar,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Attempt {
    pub side: Side,
    pub signal_ms: u64,
    pub execution_ms: Option<u64>,
    pub requested_lots: u64,
    pub filled_lots: u64,
    pub fill_price_atoms_per_lot: Option<u64>,
    pub fee_atoms: u64,
    pub status: AttemptStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EquityPoint {
    pub at_ms: u64,
    pub equity_atoms: u64,
    pub drawdown_bps: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StrategyReport {
    pub id: String,
    pub initial_cash_atoms: u64,
    pub final_cash_atoms: u64,
    pub final_equity_atoms: u64,
    pub realized_pnl_atoms: i128,
    pub unrealized_pnl_atoms: i128,
    /// Integer basis points, truncated toward zero; not annualized.
    pub return_bps: i128,
    pub max_sampled_drawdown_bps: u16,
    pub fees_atoms: u64,
    pub fills: usize,
    pub buy_signals: usize,
    pub sell_signals: usize,
    pub buys_halted: bool,
    pub remaining_lots: u64,
    pub journal_events: usize,
    pub attempts: Vec<Attempt>,
    pub equity_curve: Vec<EquityPoint>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReplayReport {
    pub mode: &'static str,
    pub scenario_sha256: String,
    pub base_asset: String,
    pub quote_asset: String,
    pub quote_scale: u32,
    pub evaluation_start_ms: u64,
    pub evaluation_end_ms: u64,
    pub warmup_bars: usize,
    pub results: Vec<StrategyReport>,
}

struct Prepared {
    catalog: MarketCatalog,
    bars: Vec<Bar>,
    symbol: String,
    tick: u64,
    fingerprint: String,
}

struct Run {
    report: StrategyReport,
    ledger: PaperLedger,
}

pub fn read_input(path: &Path) -> Result<ReplayInput> {
    ensure!(fs::metadata(path)?.is_file(), "Replay input must be a regular file");
    let mut bytes = Vec::new();
    fs::File::open(path)?.take(MAX_INPUT_BYTES + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 <= MAX_INPUT_BYTES, "Replay input too large");
    Ok(serde_json::from_slice(&bytes).context("Invalid replay scenario")?)
}

fn interval(timeframe: &str) -> Result<u64> {
    Ok(match timeframe {
        "1m" => 60_000, "3m" => 180_000, "5m" => 300_000,
        "15m" => 900_000, "30m" => 1_800_000, "1h" => 3_600_000,
        "2h" => 7_200_000, "4h" => 14_400_000, "6h" => 21_600_000,
        "8h" => 28_800_000, "12h" => 43_200_000, "1d" => 86_400_000,
        _ => anyhow::bail!("Replay requires a supported fixed UTC interval"),
    })
}

fn valid_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 32
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
}

fn prepare(input: &ReplayInput) -> Result<Prepared> {
    prepare_bounded(input, MAX_BARS)
}

fn prepare_bounded(input: &ReplayInput, max_bars: usize) -> Result<Prepared> {
    ensure!(input.version == 1, "Unsupported replay version");
    PaperLedger::new(input.account.clone())?;
    input.risk.validate().map_err(|e| anyhow!("Invalid risk policy: {e:?}"))?;
    ensure!(input.bars.len() >= 2 && input.bars.len() <= max_bars, "Replay bar count outside bounds");
    ensure!(input.warmup_bars < input.bars.len(), "Warmup consumes evaluation window");
    ensure!(!input.strategies.is_empty() && input.strategies.len() <= MAX_STRATEGIES, "Invalid strategy count");
    ensure!(input.execution.buy_lots > 0 && input.execution.delay_bars > 0
        && input.execution.delay_bars <= MAX_BARS && input.execution.fee_bps <= 10_000
        && input.execution.slippage_bps < 10_000
        && (1..=10_000).contains(&input.execution.participation_bps), "Invalid simulation execution parameters");
    if let OrderStyle::Limit { offset_bps } = input.execution.style {
        ensure!(offset_bps < 10_000, "Invalid limit offset");
    }
    let mut names = BTreeSet::new();
    for spec in &input.strategies {
        ensure!(valid_name(&spec.id) && names.insert(spec.id.to_ascii_lowercase()), "Invalid or duplicate strategy ID");
        match spec.strategy {
            StrategyKind::MaCross { fast, slow } => {
                ensure!(fast > 0 && slow > fast && slow <= MAX_BARS, "Invalid moving-average periods");
                ensure!(input.warmup_bars >= slow - 1, "Insufficient MA warmup");
            }
            StrategyKind::Rsi { period, oversold, overbought } => {
                ensure!(period > 0 && period < MAX_BARS && oversold < overbought && overbought <= 100, "Invalid RSI parameters");
                ensure!(input.warmup_bars >= period, "Insufficient RSI warmup");
            }
        }
    }
    let step = interval(&input.timeframe)?;
    let symbol = normalize_market_symbol(&format!("{}{}", input.market.base_asset, input.account.quote_asset))?;
    let mut catalog = MarketCatalog::new(&input.account.quote_asset, input.quote_scale)?;
    catalog.register(input.market.clone())?;
    let asset = input.market.base_asset.as_str();
    let tick = catalog.tick_atoms(&input.account, asset)?;
    let mut bars = Vec::with_capacity(input.bars.len());
    let mut previous: Option<u64> = None;
    for raw in &input.bars {
        ensure!(raw.open_ms > 0 && raw.open_ms % step == 0, "Unaligned or zero candle timestamp");
        if let Some(last) = previous {
            ensure!(last.checked_add(step) == Some(raw.open_ms), "Missing, duplicated or out-of-order candle");
        }
        let close_ms = raw.open_ms.checked_add(step - 1).context("Candle time overflow")?;
        for t in [raw.open_ms, close_ms] {
            ensure!(DateTime::<Utc>::from_timestamp_millis(i64::try_from(t)?).is_some(), "Unsupported candle timestamp");
        }
        let convert = |value: &str| catalog.mark_price_atoms(&input.account, asset, value);
        let (open, high, low, close) = (convert(&raw.open)?, convert(&raw.high)?, convert(&raw.low)?, convert(&raw.close)?);
        let volume = catalog.volume_lots(&input.account, asset, &raw.volume)?;
        ensure!(low <= open.min(close) && high >= open.max(close), "Invalid OHLC range");
        ensure!([open, high, low, close, volume].iter().all(|v| *v <= MAX_EXACT_F64), "Indicator input exceeds exact integer f64 range");
        bars.push(Bar { open_ms: raw.open_ms, close_ms, open, high, low, close, volume });
        previous = Some(raw.open_ms);
    }
    // The complete immutable scenario, including unit rules, execution policy,
    // strategy parameters and historical input, is bound to each output account.
    let fingerprint = hex::encode(Sha256::digest(serde_json::to_vec(input)?));
    Ok(Prepared { catalog, bars, symbol, tick, fingerprint })
}

fn event(id: String, action: Action) -> JournalEvent {
    JournalEvent { event_id: id, action }
}

fn frame(asset: &str, at: u64, price: u64) -> RiskObservation {
    RiskObservation {
        at_ms: at,
        marks: BTreeMap::from([(asset.to_owned(), Mark { price, source_ms: at })]),
    }
}

fn observe(ledger: &mut PaperLedger, id: String, observation: RiskObservation, max_dd: &mut u16) -> Result<EquityPoint> {
    let time = observation.at_ms;
    ledger.apply(event(id, Action::ObserveRisk(observation)))?;
    let report = ledger.snapshot()?.risk.context("Missing replay risk policy")?
        .last_report.context("Missing replay observation")?;
    *max_dd = (*max_dd).max(report.drawdown_bps);
    Ok(EquityPoint { at_ms: time, equity_atoms: report.equity, drawdown_bps: report.drawdown_bps })
}

/// Price rounding is adverse for execution (buy up, sell down) and never
/// violates the specified limit when constructing a limit (buy down, sell up).
fn adjusted(price: u64, tick: u64, side: Side, bps: u16, execution: bool) -> Result<u64> {
    let factor = match side {
        Side::Buy => BPS + u128::from(bps),
        Side::Sell => BPS - u128::from(bps),
    };
    let numerator = u128::from(price) * factor;
    let denominator = u128::from(tick) * BPS;
    let round_up = matches!((side, execution), (Side::Buy, true) | (Side::Sell, false));
    let ticks = numerator / denominator + u128::from(round_up && numerator % denominator != 0);
    Ok(u64::try_from(ticks.checked_mul(u128::from(tick)).context("Price overflow")?)?)
}

fn fee(lots: u64, price: u64, bps: u16) -> Result<u64> {
    let gross = lots.checked_mul(price).context("Notional overflow")?;
    let numerator = u128::from(gross) * u128::from(bps);
    Ok(u64::try_from(numerator / BPS + u128::from(numerator % BPS != 0))?)
}

fn expected_rejection(error: LedgerError) -> bool {
    matches!(error, LedgerError::RiskLimit | LedgerError::BuysHalted
        | LedgerError::InsufficientCash | LedgerError::InsufficientLots)
}

fn invalid_terms(error: UnitError) -> bool {
    matches!(error, UnitError::OffGrid | UnitError::QuantityBounds | UnitError::PriceBounds | UnitError::NotionalBounds)
}

fn execute(
    input: &ReplayInput, prepared: &Prepared, ledger: &mut PaperLedger,
    index: usize, intent: Intent, observation: &RiskObservation,
) -> Result<Attempt> {
    let bar = &prepared.bars[index];
    let e = &input.execution;
    let mut attempt = Attempt {
        side: intent.side, signal_ms: intent.signal_ms, execution_ms: Some(bar.open_ms),
        requested_lots: intent.lots, filled_lots: 0, fill_price_atoms_per_lot: None,
        fee_atoms: 0, status: AttemptStatus::InvalidTerms,
    };
    let asset = &input.market.base_asset;
    let fill_price = adjusted(bar.open, prepared.tick, intent.side, e.slippage_bps, true)?;
    let limit_price = match e.style {
        OrderStyle::Market => fill_price,
        OrderStyle::Limit { offset_bps } => adjusted(intent.signal_price, prepared.tick, intent.side, offset_bps, false)?,
    };
    if let Err(error) = prepared.catalog.validate_integer_price(&input.account, asset, fill_price) {
        if invalid_terms(error) { return Ok(attempt); }
        return Err(error.into());
    }
    let order_id = format!("bar-{index}-order");
    let terms = OrderTerms {
        order_id: order_id.clone(), asset: asset.clone(), side: intent.side,
        lots: intent.lots, limit_price, max_fee: fee(intent.lots, limit_price.max(fill_price), e.fee_bps)?,
    };
    if let Err(error) = prepared.catalog.validate_integer_terms(&input.account, &terms) {
        if invalid_terms(error) { return Ok(attempt); }
        return Err(error.into());
    }
    let submit = if intent.side == Side::Buy {
        Action::GuardedSubmit { terms, observation: observation.clone() }
    } else {
        Action::Submit(terms)
    };
    if let Err(error) = ledger.apply(event(format!("bar-{index}-submit"), submit)) {
        if expected_rejection(error) {
            attempt.status = AttemptStatus::RiskOrBalanceRejected;
            return Ok(attempt);
        }
        return Err(error.into());
    }

    let crosses = match intent.side {
        Side::Buy => fill_price <= limit_price,
        Side::Sell => fill_price >= limit_price,
    };
    // The signal is available only at the previous close. At the delayed open,
    // we can use the previously completed volume as a conservative proxy.
    let available = u64::try_from(u128::from(prepared.bars[index - 1].volume)
        * u128::from(e.participation_bps) / BPS)?;
    let lots = intent.lots.min(available);
    if !crosses {
        attempt.status = AttemptStatus::LimitNotMet;
    } else if lots == 0 {
        attempt.status = AttemptStatus::NoLiquidity;
    } else {
        let paid = fee(lots, fill_price, e.fee_bps)?;
        let fill = if intent.side == Side::Buy {
            Action::GuardedFill {
                order_id: order_id.clone(), lots, price: fill_price, fee: paid,
                observation: observation.clone(),
            }
        } else {
            Action::Fill { order_id: order_id.clone(), lots, price: fill_price, fee: paid }
        };
        match ledger.apply(event(format!("bar-{index}-fill"), fill)) {
            Ok(_) => {
                attempt.filled_lots = lots;
                attempt.fee_atoms = paid;
                attempt.fill_price_atoms_per_lot = Some(fill_price);
                attempt.status = if lots == intent.lots { AttemptStatus::Filled } else { AttemptStatus::PartialThenCanceled };
            }
            Err(error) if expected_rejection(error) => attempt.status = AttemptStatus::RiskOrBalanceRejected,
            Err(error) => return Err(error.into()),
        }
    }
    // IOC simulation: a partial or unfilled order never remains outstanding.
    if attempt.filled_lots < intent.lots {
        ledger.apply(event(format!("bar-{index}-cancel"), Action::Cancel { order_id }))?;
    }
    Ok(attempt)
}

fn strategy(spec: &StrategySpec, input: &ReplayInput, prepared: &Prepared) -> Result<StrategyBinding> {
    let implementation: Box<dyn Strategy> = match spec.strategy {
        StrategyKind::MaCross { fast, slow } => Box::new(MaCrossStrategy::new(spec.id.clone(), fast, slow)),
        StrategyKind::Rsi { period, oversold, overbought } => Box::new(RsiStrategy::new(
            spec.id.clone(), period, f64::from(oversold), f64::from(overbought))),
    };
    StrategyBinding::new(&prepared.symbol, &input.timeframe, implementation)
}

fn run_one(input: &ReplayInput, prepared: &Prepared, spec: &StrategySpec, seal_bars: bool) -> Result<Run> {
    let mut ledger = PaperLedger::new(input.account.clone())?;
    ledger.apply(event("risk-policy".into(), Action::ConfigureRisk(input.risk)))?;
    let mut strategy = strategy(spec, input, prepared)?;
    let asset = input.market.base_asset.as_str();
    let mut pending: Option<Intent> = None;
    let mut attempts = Vec::new();
    let mut equity_curve = Vec::new();
    let (mut buy_signals, mut sell_signals, mut max_dd) = (0usize, 0usize, 0u16);
    for (index, bar) in prepared.bars.iter().enumerate() {
        if index >= input.warmup_bars {
            let opening = frame(asset, bar.open_ms, bar.open);
            observe(&mut ledger, format!("bar-{index}-open"), opening.clone(), &mut max_dd)?;
            if pending.as_ref().is_some_and(|intent| intent.due == index) {
                let intent = pending.take().context("Missing due simulation intent")?;
                attempts.push(execute(input, prepared, &mut ledger, index, intent, &opening)?);
                observe(&mut ledger, format!("bar-{index}-after"), opening, &mut max_dd)?;
            }
            equity_curve.push(observe(&mut ledger, format!("bar-{index}-close"),
                frame(asset, bar.close_ms, bar.close), &mut max_dd)?);
        }
        let candle = Candle {
            symbol: prepared.symbol.clone(), timeframe: input.timeframe.clone(),
            timestamp: DateTime::<Utc>::from_timestamp_millis(i64::try_from(bar.open_ms)?).context("Invalid candle time")?,
            open: bar.open as f64, high: bar.high as f64, low: bar.low as f64,
            close: bar.close as f64, volume: bar.volume as f64,
        };
        let signal = strategy.on_candle(&candle)?.context("Replay strategy skipped validated candle")?;
        // Seal the original exact decimal input, even if this bar had no fill
        // or was used only for indicator warmup. No financial state changes.
        if seal_bars {
            let sha256 = hex::encode(Sha256::digest(serde_json::to_vec(&input.bars[index])?));
            ledger.apply(event(format!("bar-{index}-seal"), Action::SealReplayBar {
                open_ms: bar.open_ms, sha256,
            }))?;
        }
        if index < input.warmup_bars { continue; }
        match signal {
            Signal::Buy => buy_signals += 1,
            Signal::Sell => sell_signals += 1,
            Signal::Hold => {}
        }
        let account = ledger.snapshot()?;
        let held = account.holdings.get(asset).map_or(0, |holding| holding.lots);
        if pending.is_none() {
            let side_lots = match signal {
                Signal::Buy if held == 0 && !account.buys_halted => Some((Side::Buy, input.execution.buy_lots)),
                Signal::Sell if held > 0 => Some((Side::Sell, held)),
                _ => None,
            };
            if let Some((side, lots)) = side_lots {
                pending = Some(Intent {
                    side, lots, signal_ms: bar.close_ms, signal_price: bar.close,
                    due: index.checked_add(input.execution.delay_bars).context("Intent delay overflow")?,
                });
            }
        }
    }
    if let Some(intent) = pending {
        attempts.push(Attempt { side: intent.side, signal_ms: intent.signal_ms,
            execution_ms: None, requested_lots: intent.lots, filled_lots: 0,
            fill_price_atoms_per_lot: None, fee_atoms: 0, status: if seal_bars { AttemptStatus::AwaitingNextBar } else { AttemptStatus::Expired } });
    }
    ensure!(ledger.journal().len() <= MAX_EVENTS as usize, "Replay journal capacity exceeded");
    let account = ledger.snapshot()?;
    let last = account.risk.as_ref().and_then(|risk| risk.last_report.as_ref()).context("No evaluation sample")?;
    let fees_atoms = attempts.iter().try_fold(0u64, |total, a| total.checked_add(a.fee_atoms).context("Report fee overflow"))?;
    let report = StrategyReport {
        id: spec.id.clone(), initial_cash_atoms: input.account.initial_cash,
        final_cash_atoms: account.cash, final_equity_atoms: last.equity,
        realized_pnl_atoms: account.realized_pnl, unrealized_pnl_atoms: last.unrealized_pnl,
        return_bps: (i128::from(last.equity) - i128::from(input.account.initial_cash)) * 10_000 / i128::from(input.account.initial_cash),
        max_sampled_drawdown_bps: max_dd, fees_atoms,
        fills: attempts.iter().filter(|a| a.filled_lots > 0).count(),
        buy_signals, sell_signals, buys_halted: account.buys_halted,
        remaining_lots: account.holdings.get(asset).map_or(0, |h| h.lots),
        journal_events: ledger.journal().len(), attempts, equity_curve,
    };
    Ok(Run { report, ledger })
}

fn experiment(input: &ReplayInput) -> Result<(ReplayReport, Vec<Run>)> {
    experiment_mode(input, false)
}

fn experiment_mode(input: &ReplayInput, seal_bars: bool) -> Result<(ReplayReport, Vec<Run>)> {
    let prepared = prepare_bounded(input, if seal_bars { MAX_FORWARD_BARS } else { MAX_BARS })?;
    let runs = input.strategies.iter().map(|spec| run_one(input, &prepared, spec, seal_bars)).collect::<Result<Vec<_>>>()?;
    let report = ReplayReport {
        mode: if seal_bars { "offline_paper_forward_reconstruction" } else { "offline_paper_replay" }, scenario_sha256: prepared.fingerprint,
        base_asset: input.market.base_asset.clone(), quote_asset: input.account.quote_asset.clone(),
        quote_scale: input.quote_scale, evaluation_start_ms: prepared.bars[input.warmup_bars].open_ms,
        evaluation_end_ms: prepared.bars.last().context("Empty replay")?.close_ms,
        warmup_bars: input.warmup_bars, results: runs.iter().map(|run| run.report.clone()).collect(),
    };
    Ok((report, runs))
}

pub fn simulate(input: &ReplayInput) -> Result<ReplayReport> {
    Ok(experiment(input)?.0)
}


#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WalkForwardConfig {
    pub train_bars: usize,
    pub test_bars: usize,
    pub min_train_fills: usize,
    pub max_train_drawdown_bps: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TrainingCandidate {
    pub id: String,
    pub final_equity_atoms: u64,
    pub return_bps: i128,
    pub max_sampled_drawdown_bps: u16,
    pub fills: usize,
    pub buys_halted: bool,
    pub eligible: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WalkForwardFold {
    pub fold: usize,
    pub train_start_ms: u64,
    pub train_end_ms: u64,
    pub test_start_ms: u64,
    pub test_end_ms: u64,
    pub training_candidates: Vec<TrainingCandidate>,
    pub selected_strategy: Option<String>,
    pub test_result: Option<StrategyReport>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WalkForwardReport {
    pub mode: &'static str,
    pub scenario_sha256: String,
    pub experiment_sha256: String,
    pub base_asset: String,
    pub quote_asset: String,
    pub quote_scale: u32,
    pub config: WalkForwardConfig,
    pub warmup_bars: usize,
    pub unused_trailing_bars: usize,
    pub evaluated_folds: usize,
    pub skipped_folds: usize,
    /// Independent fold mean, not compounded or annualized.
    pub mean_test_return_bps: Option<i128>,
    pub worst_test_drawdown_bps: Option<u16>,
    pub positive_test_folds: usize,
    pub folds: Vec<WalkForwardFold>,
}

/// Rolling train-only candidate selection. The selected test uses only
/// prior warmup bars and fresh cash, never the training account's positions.
/// Skipped folds have no fabricated return. No database writes occur.
pub fn walk_forward(input: &ReplayInput, config: WalkForwardConfig) -> Result<WalkForwardReport> {
    let prepared = prepare(input)?;
    ensure!(
        config.train_bars >= 2 && config.test_bars >= 2
            && config.min_train_fills > 0 && config.min_train_fills <= config.train_bars
            && config.max_train_drawdown_bps <= 10_000,
        "Invalid walk-forward parameters"
    );
    let first_test = input.warmup_bars.checked_add(config.train_bars)
        .context("Walk-forward window overflow")?;
    let remaining = input.bars.len().checked_sub(first_test)
        .context("Insufficient bars for walk-forward training")?;
    let count = remaining / config.test_bars;
    ensure!((1..=32).contains(&count), "Walk-forward requires 1 to 32 complete folds");
    let mut folds = Vec::with_capacity(count);
    let (mut evaluated, mut positive, mut total_return) = (0usize, 0usize, 0i128);
    let mut worst_drawdown: Option<u16> = None;
    for i in 0..count {
        let train_start = input.warmup_bars + i * config.test_bars;
        let test_start = train_start + config.train_bars;
        let test_end = test_start + config.test_bars;
        let mut training = input.clone();
        training.bars = input.bars[train_start - input.warmup_bars..test_start].to_vec();
        let train_report = simulate(&training)?;
        let candidates: Vec<TrainingCandidate> = train_report.results.iter().map(|r| TrainingCandidate {
            id: r.id.clone(), final_equity_atoms: r.final_equity_atoms, return_bps: r.return_bps,
            max_sampled_drawdown_bps: r.max_sampled_drawdown_bps,
            fills: r.fills, buys_halted: r.buys_halted,
            eligible: !r.buys_halted && r.fills >= config.min_train_fills
                && r.max_sampled_drawdown_bps <= config.max_train_drawdown_bps,
        }).collect();
        // Exact marked equity ranks equal-starting-cash candidates.
        // Ties prefer lower drawdown, then lexicographically smaller ID.
        let chosen = candidates.iter().filter(|c| c.eligible).max_by(|a,b| {
            a.final_equity_atoms.cmp(&b.final_equity_atoms)
                .then_with(|| b.max_sampled_drawdown_bps.cmp(&a.max_sampled_drawdown_bps))
                .then_with(|| b.id.cmp(&a.id))
        }).map(|c| c.id.clone());
        let test_result = if let Some(id) = &chosen {
            let mut testing = input.clone();
            testing.bars = input.bars[test_start - input.warmup_bars..test_end].to_vec();
            testing.strategies = vec![input.strategies.iter().find(|s| &s.id == id)
                .context("Selected strategy not found")?.clone()];
            let result = simulate(&testing)?.results.into_iter().next()
                .context("Missing selected test result")?;
            evaluated += 1;
            if result.final_equity_atoms > result.initial_cash_atoms { positive += 1; }
            total_return = total_return.checked_add(result.return_bps).context("Fold return overflow")?;
            worst_drawdown = Some(worst_drawdown.map_or(result.max_sampled_drawdown_bps,
                |previous| previous.max(result.max_sampled_drawdown_bps)));
            Some(result)
        } else { None };
        folds.push(WalkForwardFold {
            fold: i + 1,
            train_start_ms: prepared.bars[train_start].open_ms,
            train_end_ms: prepared.bars[test_start - 1].close_ms,
            test_start_ms: prepared.bars[test_start].open_ms,
            test_end_ms: prepared.bars[test_end - 1].close_ms,
            training_candidates: candidates, selected_strategy: chosen, test_result,
        });
    }
    let experiment_sha256 = hex::encode(Sha256::digest(
        serde_json::to_vec(&(&prepared.fingerprint, config))?));
    Ok(WalkForwardReport {
        mode: "offline_paper_walk_forward", scenario_sha256: prepared.fingerprint,
        experiment_sha256, base_asset: input.market.base_asset.clone(),
        quote_asset: input.account.quote_asset.clone(), quote_scale: input.quote_scale,
        config, warmup_bars: input.warmup_bars, unused_trailing_bars: remaining % config.test_bars,
        evaluated_folds: evaluated, skipped_folds: count - evaluated,
        mean_test_return_bps: if evaluated == 0 { None } else { Some(total_return / evaluated as i128) },
        worst_test_drawdown_bps: worst_drawdown, positive_test_folds: positive, folds,
    })
}

/// Compute every strategy before touching any destination. Independent accounts
/// are not cross-file atomic; reruns verify exact histories in existing output
/// files and create only missing accounts. A mismatched or partial DB is never
/// silently reset. Use a trusted output directory, with exclusive experiment ownership.
pub async fn persist(input: &ReplayInput, directory: &Path) -> Result<ReplayReport> {
    let (report, runs) = experiment(input)?;
    ensure!(fs::symlink_metadata(directory)?.file_type().is_dir(), "Replay output directory must already exist");
    // Validate all existing outputs before creating missing accounts.
    for run in &runs {
        let path = directory.join(format!("paper-{}.sqlite", run.report.id));
        match fs::symlink_metadata(&path) {
            Ok(_) => {
                let id = format!("replay-{}-{}", report.scenario_sha256, run.report.id);
                let store = PaperStore::open(&path, &id, input.account.clone()).await?;
                store.verify_replay(&run.ledger).await?;
                store.close().await;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    for run in runs {
        let path = directory.join(format!("paper-{}.sqlite", run.report.id));
        match fs::symlink_metadata(&path) {
            Ok(_) => {
                let id = format!("replay-{}-{}", report.scenario_sha256, run.report.id);
                let store = PaperStore::open(&path, &id, input.account.clone()).await?;
                store.verify_replay(&run.ledger).await?;
                store.close().await;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let id = format!("replay-{}-{}", report.scenario_sha256, run.report.id);
                let expected = run.ledger.clone();
                let store = PaperStore::persist_replay(&path, &id, run.ledger).await?;
                store.verify_replay(&expected).await?;
                store.close().await;
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(report)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AuditedAccount {
    pub strategy_id: String,
    pub checkpoint: JournalCheckpoint,
    pub remaining_event_capacity: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AuditReport {
    pub mode: &'static str,
    pub live_orders_enabled: bool,
    pub verification: &'static str,
    pub replay: ReplayReport,
    pub accounts: Vec<AuditedAccount>,
}

/// Recompute the experiment and verify every expected SQLite account read-only.
/// Missing accounts or mismatched histories prevent a successful report.
/// Independent account snapshots are not a cross-database transaction.
pub async fn audit_persisted(input: &ReplayInput, directory: &Path) -> Result<AuditReport> {
    let (report, runs) = experiment(input)?;
    ensure!(fs::symlink_metadata(directory)?.file_type().is_dir(), "Replay directory must already exist");
    let mut accounts = Vec::with_capacity(runs.len());
    for run in &runs {
        let path = directory.join(format!("paper-{}.sqlite", run.report.id));
        let id = format!("replay-{}-{}", report.scenario_sha256, run.report.id);
        let store = PaperStore::open_read_only(&path, &id, input.account.clone()).await?;
        let verified = store.verify_replay(&run.ledger).await?;
        store.close().await;
        accounts.push(AuditedAccount {
            strategy_id: run.report.id.clone(),
            remaining_event_capacity: MAX_EVENTS - verified.head.sequence,
            checkpoint: JournalCheckpoint { ledger_id: id, head: verified.head },
        });
    }
    Ok(AuditReport {
        mode: "offline_paper_replay_audit",
        live_orders_enabled: false,
        verification: "exact_replay_and_persisted_account_history",
        replay: report,
        accounts,
    })
}

pub fn audit_html(report: &AuditReport) -> Result<String> {
    let mut out = html(&report.replay)?;
    let suffix = "</body></html>";
    ensure!(out.ends_with(suffix), "Unexpected replay HTML boundary");
    out.truncate(out.len() - suffix.len());
    out.push_str("<article class=\"scroll\"><h2>Verified persisted accounts</h2><p>Read-only historical audit. No live orders. No current-market health claim.</p><table><thead><tr><th>Strategy</th><th>Verified event count</th><th>Remaining capacity</th><th>Checkpoint SHA-256</th></tr></thead><tbody>");
    for account in &report.accounts {
        write!(out, "<tr><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td></tr>",
            escape(&account.strategy_id), account.checkpoint.head.sequence,
            account.remaining_event_capacity, escape(&account.checkpoint.head.hash))?;
    }
    out.push_str("</tbody></table></article></body></html>");
    Ok(out)
}


#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ForwardInput {
    pub version: u32,
    /// Caller-supplied provenance. The local collector verifies its own archive.
    /// The Rust engine does not authenticate an exchange or an archive digest.
    pub source: String,
    pub source_id: String,
    pub archive_head_sha256: String,
    pub scenario: ReplayInput,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForwardMode {
    Initialize,
    Advance,
    Audit,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ForwardReport {
    pub mode: &'static str,
    pub max_event_count: i64,
    pub live_orders_enabled: bool,
    pub source: String,
    pub source_id: String,
    pub archive_head_sha256: String,
    pub session_sha256: String,
    pub input_bar_count: usize,
    pub max_bar_count: usize,
    pub replay: ReplayReport,
    pub accounts: Vec<AuditedAccount>,
}

pub fn read_forward_input(path: &Path) -> Result<ForwardInput> {
    ensure!(fs::metadata(path)?.is_file(), "Forward input must be a regular file");
    let mut bytes = Vec::new();
    fs::File::open(path)?.take(MAX_FORWARD_INPUT_BYTES + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 <= MAX_FORWARD_INPUT_BYTES, "Forward input too large");
    Ok(serde_json::from_slice(&bytes).context("Invalid forward scenario")?)
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn forward_session(input: &ForwardInput) -> Result<String> {
    ensure!(input.version == 1, "Unsupported forward input version");
    ensure!(
        matches!(input.source.as_str(), "binance-spot-public-utc-v1" | "synthetic-fixture-v1"),
        "Unsupported forward input source"
    );
    ensure!(valid_digest(&input.source_id) && valid_digest(&input.archive_head_sha256), "Invalid forward provenance digest");
    let first = input.scenario.bars.first().context("Missing forward start bar")?.open_ms;
    let mut fixed = input.scenario.clone();
    fixed.bars.clear();
    // All parameters, candidate identities, units, source identity and fixed
    // start are immutable across extensions. Only the bar suffix may grow.
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(&(
        "paper-forward-session-v1", &input.source, &input.source_id, first, fixed,
    ))?)))
}

fn forward_path(directory: &Path, id: &str) -> std::path::PathBuf {
    directory.join(format!("forward-{id}.sqlite"))
}

fn sealed_at(ledger: &PaperLedger, count: usize) -> bool {
    count.checked_sub(1).and_then(|index| ledger.journal().get(index))
        .is_some_and(|event| matches!(&event.action, Action::SealReplayBar { .. }))
}

async fn preflight_forward(
    input: &ForwardInput, directory: &Path, session: &str, runs: &[Run], mode: ForwardMode,
) -> Result<Vec<JournalCheckpoint>> {
    let mut checkpoints = Vec::with_capacity(runs.len());
    if mode == ForwardMode::Initialize {
        ensure!(fs::read_dir(directory)?.next().is_none(), "Forward initialization requires an empty dedicated directory");
        return Ok(checkpoints);
    }
    for run in runs {
        let id = format!("forward-{session}-{}", run.report.id);
        let path = forward_path(directory, &run.report.id);
        let store = PaperStore::open_read_only(&path, &id, input.scenario.account.clone()).await?;
        let verified = store.verify_replay_prefix(&run.ledger).await?;
        store.close().await;
        let count = usize::try_from(verified.head.sequence)?;
        ensure!(sealed_at(&run.ledger, count), "Forward history does not end at a verified bar boundary");
        if mode == ForwardMode::Audit {
            ensure!(count == run.ledger.journal().len(), "Forward account is behind input");
        }
        checkpoints.push(JournalCheckpoint { ledger_id: id, head: verified.head });
    }
    Ok(checkpoints)
}

/// Extend an existing bounded offline experiment from a fixed start to a
/// later verified bar prefix. Recompute strategy state from original history
/// (including unresolved delayed signals); append ONLY complete bar groups.
/// All input and existing accounts are validated before writing. Each account
/// has its own transaction history, so use exclusive directory ownership.
/// This is not a cross-account atomic commit or a real-time broker service.
pub async fn forward(
    input: &ForwardInput, directory: &Path, mode: ForwardMode,
) -> Result<ForwardReport> {
    let session = forward_session(input)?;
    let (replay, runs) = experiment_mode(&input.scenario, true)?;
    ensure!(fs::symlink_metadata(directory)?.file_type().is_dir(), "Forward directory must already exist");
    let checkpoints = preflight_forward(input, directory, &session, &runs, mode).await?;
    for (index, run) in runs.iter().enumerate() {
        let id = format!("forward-{session}-{}", run.report.id);
        let path = forward_path(directory, &run.report.id);
        match mode {
            ForwardMode::Initialize => {
                let store = PaperStore::persist_replay(&path, &id, run.ledger.clone()).await?;
                store.close().await;
            }
            ForwardMode::Advance => {
                let checkpoint = checkpoints[index].clone();
                let store = PaperStore::open_with_checkpoint(&path, &id,
                    input.scenario.account.clone(), checkpoint.clone()).await?;
                store.extend_verified_replay(&checkpoint, &run.ledger).await?;
                store.close().await;
            }
            ForwardMode::Audit => {}
        }
    }
    let mut accounts = Vec::with_capacity(runs.len());
    for run in &runs {
        let id = format!("forward-{session}-{}", run.report.id);
        let path = forward_path(directory, &run.report.id);
        let store = PaperStore::open_read_only(&path, &id, input.scenario.account.clone()).await?;
        let verified = store.verify_replay(&run.ledger).await?;
        store.close().await;
        accounts.push(AuditedAccount {
            strategy_id: run.report.id.clone(),
            remaining_event_capacity: MAX_EVENTS - verified.head.sequence,
            checkpoint: JournalCheckpoint { ledger_id: id, head: verified.head },
        });
    }
    Ok(ForwardReport {
        mode: "offline_paper_forward", max_event_count: MAX_EVENTS, live_orders_enabled: false,
        source: input.source.clone(), source_id: input.source_id.clone(),
        archive_head_sha256: input.archive_head_sha256.clone(), session_sha256: session,
        input_bar_count: input.scenario.bars.len(), max_bar_count: MAX_FORWARD_BARS,
        replay, accounts,
    })
}

pub fn forward_html(report: &ForwardReport) -> Result<String> {
    let mut out = audit_html(&AuditReport {
        mode: "offline_paper_forward_audit", live_orders_enabled: false,
        verification: "exact_replay_and_persisted_account_history",
        replay: report.replay.clone(), accounts: report.accounts.clone(),
    })?;
    let suffix = "</body></html>";
    ensure!(out.ends_with(suffix), "Unexpected report HTML boundary");
    out.truncate(out.len() - suffix.len());
    write!(out, "<article><h2>Verified forward reconstruction</h2><p>Historical closed-bar simulation; not live fills or a real-time health claim.</p><p>Source: {} · Bars: {} / {}</p><small>Session SHA-256: <code>{}</code></small><p><small>Supplied archive head: <code>{}</code></small></p></article></body></html>",
        escape(&report.source), report.input_bar_count, report.max_bar_count,
        escape(&report.session_sha256), escape(&report.archive_head_sha256))?;
    Ok(out)
}

fn escape(value: &str) -> String {
    value.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
        .replace('"', "&quot;").replace('\'', "&#39;")
}

/// Self-contained static report, no JavaScript, remote fonts or external assets.
pub fn html(report: &ReplayReport) -> Result<String> {
    let mut out = String::from("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Paper replay report</title><style>body{font:15px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#182235;background:#f7f8fa}article{background:white;border:1px solid #dbe1e8;border-radius:12px;padding:20px;margin:16px 0}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e5e7eb;font-variant-numeric:tabular-nums}small{color:#536175}svg{width:100%;height:100px}code{overflow-wrap:anywhere}.scroll{overflow:auto}</style><body><h1>Offline paper replay</h1><p>Historical simulation only. No broker executions or live return claims.</p>");
    write!(out, "<p>{}/{} · quote scale {} · evaluation {}–{} ms UTC · warmup {} bars</p><small>Scenario SHA-256: <code>{}</code></small>",
        escape(&report.base_asset), escape(&report.quote_asset), report.quote_scale,
        report.evaluation_start_ms, report.evaluation_end_ms, report.warmup_bars, escape(&report.scenario_sha256))?;
    out.push_str("<article class=\"scroll\"><h2>Independent strategy comparison</h2><table><thead><tr><th>Strategy</th><th>Final equity (atoms)</th><th>Return (bps)</th><th>Drawdown (bps)</th><th>Fees (atoms)</th><th>Fills</th><th>Buy halt</th></tr></thead><tbody>");
    for r in &report.results {
        write!(out, "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            escape(&r.id), r.final_equity_atoms, r.return_bps, r.max_sampled_drawdown_bps, r.fees_atoms, r.fills, r.buys_halted)?;
    }
    out.push_str("</tbody></table></article>");
    for r in &report.results {
        write!(out, "<article><h2>{}</h2><p>Realized P&amp;L: {} atoms · Unrealized P&amp;L: {} atoms · Open lots: {}</p>",
            escape(&r.id), r.realized_pnl_atoms, r.unrealized_pnl_atoms, r.remaining_lots)?;
        if !r.equity_curve.is_empty() {
            let min = r.equity_curve.iter().map(|p| p.equity_atoms).min().unwrap_or(0);
            let max = r.equity_curve.iter().map(|p| p.equity_atoms).max().unwrap_or(min);
            out.push_str("<svg role=\"img\" aria-label=\"Close-sampled equity curve\" viewBox=\"0 0 600 100\" preserveAspectRatio=\"none\"><polyline fill=\"none\" stroke=\"#2563eb\" stroke-width=\"2\" vector-effect=\"non-scaling-stroke\" points=\"");
            for (index, p) in r.equity_curve.iter().enumerate() {
                let x = index as f64 * 600.0 / r.equity_curve.len().saturating_sub(1).max(1) as f64;
                let y = 95.0 - (p.equity_atoms - min) as f64 * 90.0 / (max - min).max(1) as f64;
                write!(out, "{x:.2},{y:.2} ")?;
            }
            write!(out, "\"/></svg><small>Close samples, min {} / max {} atoms. Scale is local to this chart.</small>", min, max)?;
        }
        out.push_str("<div class=\"scroll\"><table><thead><tr><th>Signal ms</th><th>Open ms</th><th>Side</th><th>Requested / filled lots</th><th>Price (atoms/lot)</th><th>Fee</th><th>Outcome</th></tr></thead><tbody>");
        for a in &r.attempts {
            write!(out, "<tr><td>{}</td><td>{}</td><td>{:?}</td><td>{} / {}</td><td>{}</td><td>{}</td><td>{:?}</td></tr>",
                a.signal_ms, a.execution_ms.map_or_else(|| "—".into(), |v| v.to_string()), a.side,
                a.requested_lots, a.filled_lots, a.fill_price_atoms_per_lot.map_or_else(|| "—".into(), |v| v.to_string()), a.fee_atoms, a.status)?;
        }
        out.push_str("</tbody></table></div></article>");
    }
    out.push_str("<p><small>Next/delayed opening marks; previous closed-bar volume proxy; IOC residual cancellation; sampled drawdown. Unclosed inventory is marked, not force-liquidated. Not a live dashboard or statistical proof of profitability.</small></p></body></html>");
    Ok(out)
}
