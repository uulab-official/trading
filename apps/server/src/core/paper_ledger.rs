//! Deterministic, long-only paper accounting. No market data, broker, or clock access.
//!
//! Quantities are whole lots and prices/fees/cash are whole quote atoms. A caller
//! must perform exact unit conversion before submitting events. Never interpret
//! an accepted simulated fill as a broker execution.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LedgerError {
    InvalidInput,
    ArithmeticOverflow,
    InsufficientCash,
    InsufficientLots,
    RiskLimit,
    BuysHalted,
    UnknownOrder,
    OrderIdConflict,
    EventIdConflict,
    DuplicateReplayEvent,
    ClosedOrder,
    FillOutsideTerms,
}

impl fmt::Display for LedgerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for LedgerError {}

type Result<T> = std::result::Result<T, LedgerError>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaperConfig {
    pub quote_asset: String,
    pub initial_cash: u64,
    /// Maximum buy reservation (gross at limit plus fee cap).
    pub max_order_cost: u64,
    /// Maximum cost basis plus outstanding buy reservations for one base asset.
    pub max_asset_cost: u64,
    /// Includes held assets and pending buys for not-yet-held assets.
    pub max_assets: usize,
    /// Session-wide net realized loss in quote atoms. Reaching it latches a halt.
    pub max_realized_loss: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Open,
    PartiallyFilled,
    Filled,
    Canceled,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OrderTerms {
    pub order_id: String,
    /// Canonical uppercase base asset; account quote asset is fixed.
    pub asset: String,
    pub side: Side,
    pub lots: u64,
    /// Maximum buy price / minimum sell price in quote atoms per lot.
    pub limit_price: u64,
    /// Maximum TOTAL quote fee across all fills for this order.
    pub max_fee: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaperOrder {
    pub terms: OrderTerms,
    pub filled_lots: u64,
    pub paid_fee: u64,
    pub status: Status,
}

impl PaperOrder {
    fn is_open(&self) -> bool {
        matches!(self.status, Status::Open | Status::PartiallyFilled)
    }

    fn remaining(&self) -> u64 {
        self.terms.lots - self.filled_lots
    }

    fn buy_reservation(&self) -> Result<u64> {
        if self.is_open() && self.terms.side == Side::Buy {
            add(
                mul(self.remaining(), self.terms.limit_price)?,
                self.terms.max_fee - self.paid_fee,
            )
        } else {
            Ok(0)
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Holding {
    pub lots: u64,
    /// Includes buy fees; proportional integer allocation on partial sells.
    pub cost_basis: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Submit(OrderTerms),
    Fill {
        order_id: String,
        lots: u64,
        price: u64,
        fee: u64,
    },
    Cancel {
        order_id: String,
    },
    /// Sticky; cancels all open buys but leaves inventory-reducing sells allowed.
    HaltBuys,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JournalEvent {
    pub event_id: String,
    pub action: Action,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApplyOutcome {
    Applied,
    Duplicate,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccountSnapshot {
    pub cash: u64,
    pub reserved_cash: u64,
    pub available_cash: u64,
    pub realized_pnl: i128,
    pub buys_halted: bool,
    pub holdings: BTreeMap<String, Holding>,
    pub orders: BTreeMap<String, PaperOrder>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct State {
    cash: u64,
    realized_pnl: i128,
    buys_halted: bool,
    holdings: BTreeMap<String, Holding>,
    orders: BTreeMap<String, PaperOrder>,
}

/// Only this type owns mutable accounting state. Serialize access externally if
/// shared across tasks. Journal replay is deterministic; disk durability is
/// deliberately outside this in-memory module.
#[derive(Debug, Clone)]
pub struct PaperLedger {
    config: PaperConfig,
    state: State,
    journal: Vec<JournalEvent>,
    seen: BTreeMap<String, usize>,
}

fn add(left: u64, right: u64) -> Result<u64> {
    left.checked_add(right)
        .ok_or(LedgerError::ArithmeticOverflow)
}

fn mul(left: u64, right: u64) -> Result<u64> {
    left.checked_mul(right)
        .ok_or(LedgerError::ArithmeticOverflow)
}

fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.' | b':'))
}

fn valid_asset(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 32
        && value.bytes().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
}

impl PaperLedger {
    pub fn new(config: PaperConfig) -> Result<Self> {
        if !valid_asset(&config.quote_asset)
            || config.initial_cash == 0
            || config.max_order_cost == 0
            || config.max_asset_cost == 0
            || config.max_assets == 0
            || config.max_realized_loss == 0
        {
            return Err(LedgerError::InvalidInput);
        }
        Ok(Self {
            state: State {
                cash: config.initial_cash,
                realized_pnl: 0,
                buys_halted: false,
                holdings: BTreeMap::new(),
                orders: BTreeMap::new(),
            },
            config,
            journal: Vec::new(),
            seen: BTreeMap::new(),
        })
    }

    pub fn config(&self) -> &PaperConfig {
        &self.config
    }

    pub fn journal(&self) -> &[JournalEvent] {
        &self.journal
    }

    pub fn snapshot(&self) -> Result<AccountSnapshot> {
        let reserved_cash = self.state.reserved_cash()?;
        let available_cash = self
            .state
            .cash
            .checked_sub(reserved_cash)
            .ok_or(LedgerError::InsufficientCash)?;
        Ok(AccountSnapshot {
            cash: self.state.cash,
            reserved_cash,
            available_cash,
            realized_pnl: self.state.realized_pnl,
            buys_halted: self.state.buys_halted,
            holdings: self.state.holdings.clone(),
            orders: self.state.orders.clone(),
        })
    }

    pub fn available_lots(&self, asset: &str) -> Result<u64> {
        self.state.available_lots(asset)
    }

    /// An exact retry is a no-op; a reused ID with different content is rejected.
    /// Invalid operations leave both the state and accepted journal unchanged.
    pub fn apply(&mut self, event: JournalEvent) -> Result<ApplyOutcome> {
        if !valid_id(&event.event_id) {
            return Err(LedgerError::InvalidInput);
        }
        if let Some(index) = self.seen.get(&event.event_id) {
            return if self.journal[*index] == event {
                Ok(ApplyOutcome::Duplicate)
            } else {
                Err(LedgerError::EventIdConflict)
            };
        }

        let mut next = self.state.clone();
        match &event.action {
            Action::Submit(terms) => next.submit(&self.config, terms)?,
            Action::Fill {
                order_id,
                lots,
                price,
                fee,
            } => next.fill(&self.config, order_id, *lots, *price, *fee)?,
            Action::Cancel { order_id } => next.cancel(order_id)?,
            Action::HaltBuys => next.halt_buys(),
        }

        // Even an unexpected reservation inconsistency must fail closed.
        if next.reserved_cash()? > next.cash {
            return Err(LedgerError::InsufficientCash);
        }
        for asset in next.holdings.keys() {
            next.available_lots(asset)?;
        }
        self.state = next;
        self.seen.insert(event.event_id.clone(), self.journal.len());
        self.journal.push(event);
        Ok(ApplyOutcome::Applied)
    }

    /// Restore only by reapplying accepted events to the original configuration.
    /// A duplicate in a stored journal is corruption, not a transport retry.
    pub fn replay(
        config: PaperConfig,
        events: impl IntoIterator<Item = JournalEvent>,
    ) -> Result<Self> {
        let mut ledger = Self::new(config)?;
        for event in events {
            if ledger.apply(event)? != ApplyOutcome::Applied {
                return Err(LedgerError::DuplicateReplayEvent);
            }
        }
        Ok(ledger)
    }
}

impl State {
    fn reserved_cash(&self) -> Result<u64> {
        self.orders
            .values()
            .try_fold(0, |total, order| add(total, order.buy_reservation()?))
    }

    fn available_lots(&self, asset: &str) -> Result<u64> {
        let lots = self.holdings.get(asset).map_or(0, |holding| holding.lots);
        let reserved = self
            .orders
            .values()
            .filter(|order| {
                order.is_open() && order.terms.side == Side::Sell && order.terms.asset == asset
            })
            .try_fold(0, |total, order| add(total, order.remaining()))?;
        lots.checked_sub(reserved)
            .ok_or(LedgerError::InsufficientLots)
    }

    fn submit(&mut self, config: &PaperConfig, terms: &OrderTerms) -> Result<()> {
        if !valid_id(&terms.order_id)
            || !valid_asset(&terms.asset)
            || terms.asset == config.quote_asset
            || terms.lots == 0
            || terms.limit_price == 0
        {
            return Err(LedgerError::InvalidInput);
        }
        if self.orders.contains_key(&terms.order_id) {
            return Err(LedgerError::OrderIdConflict);
        }

        // Validate the notional for both sides, even if only buys reserve cash.
        let gross = mul(terms.lots, terms.limit_price)?;
        match terms.side {
            Side::Buy => {
                if self.buys_halted {
                    return Err(LedgerError::BuysHalted);
                }
                let required = add(gross, terms.max_fee)?;
                let available = self
                    .cash
                    .checked_sub(self.reserved_cash()?)
                    .ok_or(LedgerError::InsufficientCash)?;
                if required > available {
                    return Err(LedgerError::InsufficientCash);
                }
                if required > config.max_order_cost {
                    return Err(LedgerError::RiskLimit);
                }
                let basis = self
                    .holdings
                    .get(&terms.asset)
                    .map_or(0, |holding| holding.cost_basis);
                let exposure = self
                    .orders
                    .values()
                    .filter(|order| order.terms.asset == terms.asset)
                    .try_fold(basis, |total, order| add(total, order.buy_reservation()?))?;
                if add(exposure, required)? > config.max_asset_cost {
                    return Err(LedgerError::RiskLimit);
                }
                let mut assets: BTreeSet<&str> =
                    self.holdings.keys().map(String::as_str).collect();
                assets.extend(
                    self.orders
                        .values()
                        .filter(|order| order.is_open() && order.terms.side == Side::Buy)
                        .map(|order| order.terms.asset.as_str()),
                );
                assets.insert(terms.asset.as_str());
                if assets.len() > config.max_assets {
                    return Err(LedgerError::RiskLimit);
                }
            }
            Side::Sell => {
                if terms.lots > self.available_lots(&terms.asset)? {
                    return Err(LedgerError::InsufficientLots);
                }
            }
        }
        self.orders.insert(
            terms.order_id.clone(),
            PaperOrder {
                terms: terms.clone(),
                filled_lots: 0,
                paid_fee: 0,
                status: Status::Open,
            },
        );
        Ok(())
    }

    fn fill(
        &mut self,
        config: &PaperConfig,
        order_id: &str,
        lots: u64,
        price: u64,
        fee: u64,
    ) -> Result<()> {
        if lots == 0 || price == 0 {
            return Err(LedgerError::InvalidInput);
        }
        let mut order = self
            .orders
            .get(order_id)
            .cloned()
            .ok_or(LedgerError::UnknownOrder)?;
        if !order.is_open() {
            return Err(LedgerError::ClosedOrder);
        }
        if lots > order.remaining()
            || fee > order.terms.max_fee - order.paid_fee
            || (order.terms.side == Side::Buy && price > order.terms.limit_price)
            || (order.terms.side == Side::Sell && price < order.terms.limit_price)
        {
            return Err(LedgerError::FillOutsideTerms);
        }
        let gross = mul(lots, price)?;
        let asset = order.terms.asset.clone();
        match order.terms.side {
            Side::Buy => {
                if self.buys_halted {
                    return Err(LedgerError::BuysHalted);
                }
                let cost = add(gross, fee)?;
                self.cash = self
                    .cash
                    .checked_sub(cost)
                    .ok_or(LedgerError::InsufficientCash)?;
                let holding = self.holdings.entry(asset).or_default();
                holding.lots = add(holding.lots, lots)?;
                holding.cost_basis = add(holding.cost_basis, cost)?;
            }
            Side::Sell => {
                let proceeds = gross
                    .checked_sub(fee)
                    .ok_or(LedgerError::FillOutsideTerms)?;
                let holding = self
                    .holdings
                    .get_mut(&asset)
                    .ok_or(LedgerError::InsufficientLots)?;
                if lots > holding.lots {
                    return Err(LedgerError::InsufficientLots);
                }
                // u64 * u64 always fits u128, and allocation <= cost_basis.
                let basis = (u128::from(holding.cost_basis) * u128::from(lots)
                    / u128::from(holding.lots)) as u64;
                holding.lots -= lots;
                holding.cost_basis -= basis;
                if holding.lots == 0 {
                    self.holdings.remove(&asset);
                }
                self.cash = add(self.cash, proceeds)?;
                self.realized_pnl = self
                    .realized_pnl
                    .checked_add(i128::from(proceeds) - i128::from(basis))
                    .ok_or(LedgerError::ArithmeticOverflow)?;
            }
        }

        order.filled_lots = add(order.filled_lots, lots)?;
        order.paid_fee = add(order.paid_fee, fee)?;
        order.status = if order.remaining() == 0 {
            Status::Filled
        } else {
            Status::PartiallyFilled
        };
        self.orders.insert(order_id.to_owned(), order);
        if self.realized_pnl <= -i128::from(config.max_realized_loss) {
            self.halt_buys();
        }
        Ok(())
    }

    fn cancel(&mut self, order_id: &str) -> Result<()> {
        let order = self
            .orders
            .get_mut(order_id)
            .ok_or(LedgerError::UnknownOrder)?;
        if !order.is_open() {
            return Err(LedgerError::ClosedOrder);
        }
        order.status = Status::Canceled;
        Ok(())
    }

    fn halt_buys(&mut self) {
        self.buys_halted = true;
        for order in self.orders.values_mut() {
            if order.is_open() && order.terms.side == Side::Buy {
                order.status = Status::Canceled;
            }
        }
    }
}
