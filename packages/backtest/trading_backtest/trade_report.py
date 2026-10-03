"""Fee-inclusive FIFO reports derived from exact, successful simulated fills."""

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Optional, Tuple

REPORT_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
ZERO = Decimal("0")


@dataclass(frozen=True)
class FillRecord:
    order_id: str
    symbol: str
    side: str
    amount: Decimal
    notional: Decimal
    fee: Decimal
    holding_before: Decimal
    holding_after: Decimal
    timestamp: Any = None


@dataclass(frozen=True)
class OpenLot:
    entry_order_id: str
    symbol: str
    amount: Decimal
    entry_notional: Decimal
    entry_fee: Decimal


@dataclass(frozen=True)
class LotMatch:
    entry_order_id: str
    amount: Decimal
    entry_notional: Decimal
    entry_fee: Decimal


@dataclass(frozen=True)
class ClosedExit:
    exit_order_id: str
    symbol: str
    amount: Decimal
    entry_notional: Decimal
    entry_fees: Decimal
    exit_notional: Decimal
    exit_fee: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    net_return_pct: Decimal
    matches: Tuple[LotMatch, ...]
    timestamp: Any = None


@dataclass(frozen=True)
class InventoryRounding:
    order_id: str
    symbol: str
    holding_before: Decimal
    holding_after: Decimal
    signed_fill_amount: Decimal
    inventory_change: Decimal
    rounding_residue: Decimal


@dataclass(frozen=True)
class TradeReport:
    fill_count: int
    quote_currency: Optional[str]
    closed_exits: Tuple[ClosedExit, ...]
    open_lots: Tuple[OpenLot, ...]
    closed_trade_count: int
    win_count: int
    loss_count: int
    break_even_count: int
    win_rate: Decimal
    profit_factor: Optional[Decimal]
    avg_win: Decimal
    avg_loss: Decimal
    avg_trade: Decimal
    expectancy: Decimal
    gross_realized_pnl: Decimal
    realized_net_pnl: Decimal
    inventory_rounding: Tuple[InventoryRounding, ...]

    def to_dict(self):
        """Exact Decimal strings keep report amounts and percentages lossless."""
        def encode(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [encode(item) for item in value]
            return value

        data = encode(asdict(self))
        data["pnl_unit"] = self.quote_currency or "account_quote_units"
        data["return_unit"] = "percent_of_fee_inclusive_entry_cost"
        data["matching"] = "FIFO; one successful sell fill is one closed exit"
        return data


def _quantity_sum(values):
    """Exact inventory addition; monetary operations still use the fixed context."""
    values = tuple(values)
    if not values:
        return ZERO
    exponent = min(value.as_tuple().exponent for value in values)
    highest = max(value.adjusted() for value in values)
    with localcontext(REPORT_CONTEXT) as context:
        context.prec = max(28, highest - exponent + len(str(len(values))) + 2)
        return sum(values, ZERO)


def _quantity_difference(left, right):
    return _quantity_sum((left, right.copy_negate()))


def _reconcile_inventory(symbol_lots, recorded_holding):
    """Apply only the recorded broker rounding residue, retaining all cost/fees."""
    residue = _quantity_difference(recorded_holding, _quantity_sum(lot.amount for lot in symbol_lots))
    if not residue:
        return
    if not symbol_lots:
        raise ValueError("Recorded inventory has no successful buy cost basis")
    if residue > ZERO:
        symbol_lots[-1] = replace(symbol_lots[-1], amount=_quantity_sum((symbol_lots[-1].amount, residue)))
    else:
        deficit = residue.copy_negate()
        for index in range(len(symbol_lots) - 1, -1, -1):
            lot = symbol_lots[index]
            reduction = min(lot.amount, deficit)
            symbol_lots[index] = replace(lot, amount=_quantity_difference(lot.amount, reduction))
            deficit = _quantity_difference(deficit, reduction)
            if not deficit:
                break
        if deficit:
            raise ValueError("Recorded inventory rounding exceeds the matched inventory")


def build_trade_report(fills: Tuple[FillRecord, ...]) -> TradeReport:
    """Match by symbol without mutating fills or treating open lots as realized."""
    quotes = {fill.symbol.rpartition("/")[2] if "/" in fill.symbol else None for fill in fills}
    if len(quotes) > 1:
        raise ValueError("Trade reporting requires a single quote currency; no FX conversion is modeled")
    quote = next(iter(quotes), None)
    lots = defaultdict(deque)
    exits = []
    rounding = []
    with localcontext(REPORT_CONTEXT):
        for fill in fills:
            _reconcile_inventory(lots[fill.symbol], fill.holding_before)
            actual_delta = _quantity_difference(fill.holding_after, fill.holding_before)
            expected_delta = fill.amount if fill.side == "buy" else fill.amount.copy_negate()
            residue = _quantity_difference(actual_delta, expected_delta)
            if residue:
                rounding.append(InventoryRounding(fill.order_id, fill.symbol, fill.holding_before,
                    fill.holding_after, expected_delta, actual_delta, residue))
            if fill.side == "buy":
                lots[fill.symbol].append(OpenLot(fill.order_id, fill.symbol, fill.amount,
                                                fill.notional, fill.fee))
                _reconcile_inventory(lots[fill.symbol], fill.holding_after)
                continue
            # The known final close consumes all retained cost/fees, including
            # zero-quantity rounding lots. A genuine small holding is not erased.
            full_close = fill.holding_after == ZERO
            remaining = fill.holding_before if full_close else fill.amount
            matches = []
            while remaining > ZERO or (full_close and lots[fill.symbol]):
                if not lots[fill.symbol]:
                    raise ValueError("A successful sell cannot be matched to prior spot buys")
                lot = lots[fill.symbol].popleft()
                amount = lot.amount if full_close and remaining <= ZERO else min(remaining, lot.amount)
                if amount == lot.amount:
                    notional, fee = lot.entry_notional, lot.entry_fee
                else:
                    notional = lot.entry_notional * amount / lot.amount
                    fee = lot.entry_fee * amount / lot.amount
                    # Allocate the final residue to the final matched portion.
                    lots[fill.symbol].appendleft(replace(lot, amount=_quantity_difference(lot.amount, amount),
                        entry_notional=lot.entry_notional - notional, entry_fee=lot.entry_fee - fee))
                matches.append(LotMatch(lot.entry_order_id, amount, notional, fee))
                remaining = _quantity_difference(remaining, amount)
            _reconcile_inventory(lots[fill.symbol], fill.holding_after)
            entry_notional = sum((match.entry_notional for match in matches), ZERO)
            entry_fees = sum((match.entry_fee for match in matches), ZERO)
            gross = fill.notional - entry_notional
            net = gross - entry_fees - fill.fee
            net_return = net / (entry_notional + entry_fees) * 100
            exits.append(ClosedExit(fill.order_id, fill.symbol, fill.amount, entry_notional,
                entry_fees, fill.notional, fill.fee, gross, net, net_return, tuple(matches), deepcopy(fill.timestamp)))

        winners = [exit for exit in exits if exit.net_pnl > ZERO]
        losers = [exit for exit in exits if exit.net_pnl < ZERO]
        count = len(exits)
        win_rate = Decimal(len(winners)) / count * 100 if count else ZERO
        positive_pnl = sum((exit.net_pnl for exit in winners), ZERO)
        negative_pnl = -sum((exit.net_pnl for exit in losers), ZERO)
        profit_factor = positive_pnl / negative_pnl if negative_pnl else None

        def mean(items):
            return sum((item.net_return_pct for item in items), ZERO) / len(items) if items else ZERO

        average = mean(exits)
        return TradeReport(len(fills), quote, tuple(exits),
            tuple(lot for symbol_lots in lots.values() for lot in symbol_lots),
            count, len(winners), len(losers), count - len(winners) - len(losers),
            win_rate, profit_factor, mean(winners), mean(losers), average, average,
            sum((exit.gross_pnl for exit in exits), ZERO), sum((exit.net_pnl for exit in exits), ZERO),
            tuple(rounding))
