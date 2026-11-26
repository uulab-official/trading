//! Exact decimal conversion for isolated long-only paper accounts.
//! No float, network, rounding, exchange-rule discovery or order execution.

use std::{collections::BTreeMap, fmt};

use super::paper_ledger::{Action, JournalEvent, OrderTerms, PaperConfig, Side};

pub const MAX_SCALE: u32 = 18;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnitError {
    InvalidDecimal,
    ArithmeticOverflow,
    OffGrid,
    NonIntegralTickLot,
    InvalidRules,
    UnknownAsset,
    DuplicateAsset,
    QuoteMismatch,
    QuantityBounds,
    PriceBounds,
    NotionalBounds,
    InvalidId,
    FillOutsideTerms,
}

impl fmt::Display for UnitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for UnitError {}

type Result<T> = std::result::Result<T, UnitError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Decimal {
    coefficient: u128,
    scale: u32,
}

impl Decimal {
    fn parse(input: &str) -> Result<Self> {
        if input.is_empty() || input.len() > 64 {
            return Err(UnitError::InvalidDecimal);
        }
        let mut coefficient = 0u128;
        let mut scale = 0;
        let mut after_dot = false;
        let mut whole_digits = 0;
        for byte in input.bytes() {
            match byte {
                b'0'..=b'9' => {
                    coefficient = coefficient
                        .checked_mul(10)
                        .and_then(|n| n.checked_add(u128::from(byte - b'0')))
                        .ok_or(UnitError::ArithmeticOverflow)?;
                    if after_dot {
                        scale += 1;
                        if scale > MAX_SCALE {
                            return Err(UnitError::InvalidDecimal);
                        }
                    } else {
                        whole_digits += 1;
                    }
                }
                b'.' if !after_dot && whole_digits > 0 => after_dot = true,
                _ => return Err(UnitError::InvalidDecimal),
            }
        }
        if whole_digits == 0 || (after_dot && scale == 0) {
            return Err(UnitError::InvalidDecimal);
        }
        // Reduce exact trailing zeros without rounding.
        while scale > 0 && coefficient % 10 == 0 {
            coefficient /= 10;
            scale -= 1;
        }
        Ok(Self { coefficient, scale })
    }

    fn positive(input: &str) -> Result<Self> {
        let value = Self::parse(input)?;
        if value.coefficient == 0 {
            return Err(UnitError::InvalidRules);
        }
        Ok(value)
    }

    /// Compute the integer number of units by reducing the rational expression
    /// before scaling, avoiding unnecessary intermediate multiplication.
    fn units(self, unit: Self) -> Result<u64> {
        let common = gcd(self.coefficient, unit.coefficient);
        let numerator = self.coefficient / common;
        let denominator = unit.coefficient / common;
        let value = if self.scale >= unit.scale {
            let factor = power10(self.scale - unit.scale)?;
            if denominator != 1 || numerator % factor != 0 {
                return Err(UnitError::OffGrid);
            }
            numerator / factor
        } else {
            let factor = power10(unit.scale - self.scale)?;
            if factor % denominator != 0 {
                return Err(UnitError::OffGrid);
            }
            numerator
                .checked_mul(factor / denominator)
                .ok_or(UnitError::ArithmeticOverflow)?
        };
        narrow(value)
    }
}

fn gcd(mut a: u128, mut b: u128) -> u128 {
    while b != 0 {
        let remainder = a % b;
        a = b;
        b = remainder;
    }
    a
}

fn power10(exponent: u32) -> Result<u128> {
    10u128
        .checked_pow(exponent)
        .ok_or(UnitError::ArithmeticOverflow)
}

fn narrow(value: u128) -> Result<u64> {
    u64::try_from(value).map_err(|_| UnitError::ArithmeticOverflow)
}

fn multiply(a: u64, b: u64) -> Result<u64> {
    a.checked_mul(b).ok_or(UnitError::ArithmeticOverflow)
}

fn quote_atoms(value: &str, scale: u32) -> Result<u64> {
    Decimal::parse(value)?.units(Decimal { coefficient: 1, scale })
}

/// The ledger's price is quote atoms PER LOT, not the exchange's quote price
/// per base unit. The smallest tick times one lot must be an exact atom count.
/// A nonrepresentable market is rejected instead of silently rounding.
fn atoms_per_tick_lot(tick: Decimal, lot: Decimal, quote_scale: u32) -> Result<u64> {
    let mut a = tick.coefficient;
    let mut b = lot.coefficient;
    let scale = tick.scale + lot.scale;
    let value = if scale > quote_scale {
        let mut divisor = power10(scale - quote_scale)?;
        let common = gcd(a, divisor);
        a /= common;
        divisor /= common;
        let common = gcd(b, divisor);
        b /= common;
        divisor /= common;
        if divisor != 1 {
            return Err(UnitError::NonIntegralTickLot);
        }
        a.checked_mul(b).ok_or(UnitError::ArithmeticOverflow)?
    } else {
        a.checked_mul(b)
            .and_then(|n| n.checked_mul(10u128.checked_pow(quote_scale - scale)?))
            .ok_or(UnitError::ArithmeticOverflow)?
    };
    let atoms = narrow(value)?;
    if atoms == 0 {
        return Err(UnitError::NonIntegralTickLot);
    }
    Ok(atoms)
}

fn valid_asset(asset: &str) -> bool {
    !asset.is_empty()
        && asset.len() <= 32
        && asset.bytes().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.' | b':'))
}

/// Explicit, static, uniform tick and lot rules. Amounts and prices must be
/// decimal strings in original market units, not floating-point values.
/// This does not model a variable price-tier tick schedule.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketRules {
    pub base_asset: String,
    pub lot_size: String,
    pub price_tick: String,
    pub min_quantity: String,
    pub max_quantity: Option<String>,
    pub min_price: Option<String>,
    pub max_price: Option<String>,
    pub min_notional: String,
    pub max_notional: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Instrument {
    lot: Decimal,
    tick: Decimal,
    atoms_per_tick_lot: u64,
    min_lots: u64,
    max_lots: Option<u64>,
    min_ticks: Option<u64>,
    max_ticks: Option<u64>,
    min_notional: u64,
    max_notional: Option<u64>,
}

impl Instrument {
    fn new(rules: &MarketRules, quote_scale: u32) -> Result<Self> {
        let lot = Decimal::positive(&rules.lot_size)?;
        let tick = Decimal::positive(&rules.price_tick)?;
        let atoms_per_tick_lot = atoms_per_tick_lot(tick, lot, quote_scale)?;
        let min_lots = Decimal::positive(&rules.min_quantity)?.units(lot)?;
        let max_lots = rules.max_quantity.as_deref()
            .map(|value| Decimal::positive(value)?.units(lot))
            .transpose()?;
        let min_ticks = rules.min_price.as_deref()
            .map(|value| Decimal::positive(value)?.units(tick))
            .transpose()?;
        let max_ticks = rules.max_price.as_deref()
            .map(|value| Decimal::positive(value)?.units(tick))
            .transpose()?;
        let min_notional = quote_atoms(&rules.min_notional, quote_scale)?;
        let max_notional = rules.max_notional.as_deref()
            .map(|value| quote_atoms(value, quote_scale))
            .transpose()?;
        if min_lots == 0
            || max_lots.is_some_and(|max| max < min_lots)
            || matches!((min_ticks, max_ticks), (Some(min), Some(max)) if max < min)
            || max_notional.is_some_and(|max| max == 0 || max < min_notional)
        {
            return Err(UnitError::InvalidRules);
        }
        // Explicit price bounds must fit the ledger's per-lot integer domain.
        if let Some(min) = min_ticks {
            multiply(min, atoms_per_tick_lot)?;
        }
        if let Some(max) = max_ticks {
            multiply(max, atoms_per_tick_lot)?;
        }
        Ok(Self {
            lot, tick, atoms_per_tick_lot, min_lots, max_lots,
            min_ticks, max_ticks, min_notional, max_notional,
        })
    }

    fn quantity(&self, raw: &str) -> Result<u64> {
        let lots = Decimal::parse(raw)?.units(self.lot)?;
        if lots == 0 {
            return Err(UnitError::QuantityBounds);
        }
        Ok(lots)
    }

    fn price(&self, raw: &str) -> Result<u64> {
        let ticks = Decimal::parse(raw)?.units(self.tick)?;
        if ticks == 0
            || self.min_ticks.is_some_and(|min| ticks < min)
            || self.max_ticks.is_some_and(|max| ticks > max)
        {
            return Err(UnitError::PriceBounds);
        }
        multiply(ticks, self.atoms_per_tick_lot)
    }

    fn order_notional(&self, lots: u64, price: u64) -> Result<u64> {
        if lots < self.min_lots || self.max_lots.is_some_and(|max| lots > max) {
            return Err(UnitError::QuantityBounds);
        }
        let gross = multiply(lots, price)?;
        if gross < self.min_notional || self.max_notional.is_some_and(|max| gross > max) {
            return Err(UnitError::NotionalBounds);
        }
        Ok(gross)
    }

    fn validate_terms_grid(&self, terms: &OrderTerms) -> Result<()> {
        if terms.limit_price == 0 || terms.limit_price % self.atoms_per_tick_lot != 0 {
            return Err(UnitError::OffGrid);
        }
        let ticks = terms.limit_price / self.atoms_per_tick_lot;
        if self.min_ticks.is_some_and(|min| ticks < min)
            || self.max_ticks.is_some_and(|max| ticks > max)
        {
            return Err(UnitError::PriceBounds);
        }
        let gross = self.order_notional(terms.lots, terms.limit_price)?;
        if terms.side == Side::Buy {
            gross.checked_add(terms.max_fee).ok_or(UnitError::ArithmeticOverflow)?;
        }
        Ok(())
    }
}

/// A single quote denomination and one immutable rule set per base asset.
/// Duplicate registrations are rejected. The application must keep the exact
/// same catalog for the lifetime of a ledger; persistence/pinning of catalog
/// identity is intentionally outside this conversion module.
#[derive(Debug, Clone)]
pub struct MarketCatalog {
    quote_asset: String,
    quote_scale: u32,
    instruments: BTreeMap<String, Instrument>,
}

impl MarketCatalog {
    pub fn new(quote_asset: &str, quote_scale: u32) -> Result<Self> {
        if !valid_asset(quote_asset) || quote_scale > MAX_SCALE {
            return Err(UnitError::InvalidRules);
        }
        Ok(Self {
            quote_asset: quote_asset.to_owned(),
            quote_scale,
            instruments: BTreeMap::new(),
        })
    }

    pub fn register(&mut self, rules: MarketRules) -> Result<()> {
        if !valid_asset(&rules.base_asset) || rules.base_asset == self.quote_asset {
            return Err(UnitError::InvalidRules);
        }
        if self.instruments.contains_key(&rules.base_asset) {
            return Err(UnitError::DuplicateAsset);
        }
        let instrument = Instrument::new(&rules, self.quote_scale)?;
        self.instruments.insert(rules.base_asset, instrument);
        Ok(())
    }

    pub fn quote_asset(&self) -> &str {
        &self.quote_asset
    }

    pub fn quote_scale(&self) -> u32 {
        self.quote_scale
    }

    pub fn quote_atoms(&self, amount: &str) -> Result<u64> {
        quote_atoms(amount, self.quote_scale)
    }

    /// Convert a valuation mark using the same price grid and lot denomination
    /// as simulated orders. Timestamp/source trust remains the caller's duty.
    pub fn mark_price_atoms(&self, config: &PaperConfig, asset: &str, price: &str) -> Result<u64> {
        self.validate_quote(config)?;
        self.instrument(asset)?.price(price)
    }

    /// A checked string boundary; no implicit rounding or quote conversion.
    pub fn order_terms(&self, config: &PaperConfig, request: LimitRequest<'_>) -> Result<OrderTerms> {
        self.validate_quote(config)?;
        if !valid_id(request.order_id) {
            return Err(UnitError::InvalidId);
        }
        let instrument = self.instrument(request.asset)?;
        let lots = instrument.quantity(request.quantity)?;
        let limit_price = instrument.price(request.price)?;
        let gross = instrument.order_notional(lots, limit_price)?;
        let max_fee = self.quote_atoms(request.max_fee)?;
        if request.side == Side::Buy {
            gross.checked_add(max_fee).ok_or(UnitError::ArithmeticOverflow)?;
        }
        Ok(OrderTerms {
            order_id: request.order_id.to_owned(),
            asset: request.asset.to_owned(),
            side: request.side,
            lots,
            limit_price,
            max_fee,
        })
    }

    /// Convert an explicitly synthetic fill using the order's base asset and
    /// limit. Partial fills need not meet the minimum *order* size or notional.
    /// The ledger still enforces remaining quantity, cumulative fee, status,
    /// idempotency and risk controls; this is not an execution/matching engine.
    pub fn fill_event(
        &self,
        config: &PaperConfig,
        terms: &OrderTerms,
        request: FillRequest<'_>,
    ) -> Result<JournalEvent> {
        self.validate_quote(config)?;
        if !valid_id(&terms.order_id) || !valid_id(request.event_id) {
            return Err(UnitError::InvalidId);
        }
        let instrument = self.instrument(&terms.asset)?;
        instrument.validate_terms_grid(terms)?;
        let lots = instrument.quantity(request.quantity)?;
        let price = instrument.price(request.price)?;
        let fee = self.quote_atoms(request.fee)?;
        let gross = multiply(lots, price)?;
        if lots > terms.lots || fee > terms.max_fee
            || (terms.side == Side::Buy && price > terms.limit_price)
            || (terms.side == Side::Sell && (price < terms.limit_price || fee > gross))
        {
            return Err(UnitError::FillOutsideTerms);
        }
        if terms.side == Side::Buy {
            gross.checked_add(fee).ok_or(UnitError::ArithmeticOverflow)?;
        }
        Ok(JournalEvent {
            event_id: request.event_id.to_owned(),
            action: Action::Fill {
                order_id: terms.order_id.clone(), lots, price, fee,
            },
        })
    }

    fn validate_quote(&self, config: &PaperConfig) -> Result<()> {
        if config.quote_asset != self.quote_asset {
            return Err(UnitError::QuoteMismatch);
        }
        Ok(())
    }

    fn instrument(&self, asset: &str) -> Result<&Instrument> {
        self.instruments.get(asset).ok_or(UnitError::UnknownAsset)
    }
}

#[derive(Debug, Clone, Copy)]
pub struct LimitRequest<'a> {
    pub order_id: &'a str,
    pub asset: &'a str,
    pub side: Side,
    pub quantity: &'a str,
    pub price: &'a str,
    /// Explicit total quote fee ceiling, not a rate or per-fill allowance.
    pub max_fee: &'a str,
}

#[derive(Debug, Clone, Copy)]
pub struct FillRequest<'a> {
    pub event_id: &'a str,
    pub quantity: &'a str,
    pub price: &'a str,
    /// Actual synthetic fee in original quote currency units.
    pub fee: &'a str,
}
