"""Read-only comparison of complete saved paper observations, not a live ledger.

Both files are independently validated, then checked for compatible forward
history. The report describes those captured bytes, not the current account or
an authenticated event trail. No SQLite connection, order or restore operation.
"""
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import json

from trading_core.paper_evidence import _read_evidence
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT

DETAIL_LIMIT = 100
# Exact sums/differences of bounded input coefficients, not execution arithmetic.
_DIFF_CONTEXT = Context(prec=512, rounding=ROUND_HALF_EVEN)


class PaperEvidenceComparisonError(ValueError):
    """Individually valid files cannot support this forward comparison."""
    def __init__(self, reason):
        super().__init__('paper_evidence_comparison_conflict')
        self.reason = reason


def _require(condition, reason):
    if not condition:
        raise PaperEvidenceComparisonError(reason)


def _number(value):
    number = Decimal(value)
    parts = number.as_tuple()
    _require(number.is_finite() and len(parts.digits) <= 128
             and -120 <= parts.exponent <= 120, 'numeric_analysis_bound')
    return number


def _delta(before, after):
    if before is None or after is None:
        return None
    with localcontext(_DIFF_CONTEXT):
        return str(_number(after) - _number(before))


def _bounded(items):
    return dict(count=len(items), items=items[:DETAIL_LIMIT],
                truncated=len(items) > DETAIL_LIMIT)


def _compatible(left, right):
    a, b = left['payload'], right['payload']
    ls, rs = left['snapshot'], right['snapshot']
    _require(ls['account_id'] == rs['account_id'], 'different_account')
    _require(ls['session_version'] == rs['session_version']
             and a['initial'] == b['initial'] and a.get('policy') == b.get('policy'),
             'configuration_changed')
    _require(int(rs['revision']) >= int(ls['revision']), 'revision_reversed')
    if rs['revision'] == ls['revision']:
        _require(rs['digest'] == ls['digest'], 'same_revision_changed')
    old_trades, new_trades = a['book']['trades'], b['book']['trades']
    _require(new_trades[:len(old_trades)] == old_trades, 'fill_history_changed')
    for key, request in a['book']['requests'].items():
        _require(b['book']['requests'].get(key) == request, 'fill_history_changed')
    _require(a['orders'].keys() <= b['orders'].keys(), 'order_history_changed')
    for key, old in a['orders'].items():
        new = b['orders'][key]
        fixed = ('client_order_id', 'symbol', 'side', 'order_type', 'strategy_id', 'created_at')
        _require(all(old[name] == new[name] for name in fixed), 'order_history_changed')
        _require(Decimal(old['amount']) == Decimal(new['amount'])
                 and (old['price'] is None) == (new['price'] is None)
                 and (old['price'] is None or Decimal(old['price']) == Decimal(new['price']))
                 and Decimal(new['filled']) >= Decimal(old['filled'])
                 and datetime.fromisoformat(new['updated_at']) >= datetime.fromisoformat(old['updated_at'])
                 and (old['exchange_order_id'] is None or old['exchange_order_id'] == new['exchange_order_id'])
                 and (old['status'] != 'filled' or new['status'] == 'filled')
                 and not (old['status'] == 'unknown' and new['status'] == 'submitting')
                 and (not old['cancel_requested'] or new['cancel_requested']),
                 'order_history_changed')
    old, new = a.get('control'), b.get('control')
    if old is not None:
        _require(new is not None and new['generation'] >= old['generation']
                 and (new['generation'] != old['generation'] or new == old),
                 'control_history_changed')
    return new_trades[len(old_trades):]


def _account_changes(a, b, trades):
    """Replay the suffix with the ORIGINAL operation order and context.

    Nominal fill cashflows and exact endpoint differences need not be equal at
    finite precision. Their residual is reported, never credited to the account.
    """
    quote = a['initial']['quote_currency']
    old, new = a['book'], b['book']
    balances = {asset: _number(value) for asset, value in old['balances'].items()}
    fees = _number(old['total_fees'])
    flow, recorded_fees = Decimal(0), Decimal(0)
    quantities, fills = {}, []
    for trade in trades:
        base, currency = trade['symbol'].split('/')
        amount, price, fee = (_number(trade['amount']), _number(trade['average']),
                              _number(trade['fee']['cost']))
        with localcontext(PAPER_CALCULATION_CONTEXT):
            cost = amount * price
            if trade['side'] == 'buy':
                balances[quote] -= cost + fee
                balances[base] = balances.get(base, Decimal(0)) + amount
            else:
                balances[base] -= amount
                balances[quote] = balances.get(quote, Decimal(0)) + cost - fee
            fees += fee
        with localcontext(_DIFF_CONTEXT):
            signed_amount = amount if trade['side'] == 'buy' else -amount
            quantities[base] = quantities.get(base, Decimal(0)) + signed_amount
            cashflow = -cost - fee if trade['side'] == 'buy' else cost - fee
            flow += cashflow
            recorded_fees += fee
        fills.append(dict(client_order_id=trade['clientOrderId'], symbol=trade['symbol'],
                          side=trade['side'], quantity=trade['amount'], price=trade['average'],
                          notional=str(cost), fee=trade['fee']['cost'], currency=currency,
                          timestamp_ms=trade['timestamp']))
    final = {asset: _number(value) for asset, value in new['balances'].items()}
    _require(balances == final, 'balance_reconciliation_failed')
    _require(fees == _number(new['total_fees']), 'fee_reconciliation_failed')
    with localcontext(_DIFF_CONTEXT):
        cash_delta = final[quote] - _number(old['balances'][quote])
        fee_delta = fees - _number(old['total_fees'])
        cash = dict(before=old['balances'][quote], after=new['balances'][quote],
                    delta=str(cash_delta), from_new_fills=str(flow),
                    rounding_delta=str(cash_delta - flow))
        fee_report = dict(before=old['total_fees'], after=new['total_fees'],
                          delta=str(fee_delta), from_new_fills=str(recorded_fees),
                          rounding_delta=str(fee_delta - recorded_fees))
        positions = []
        for asset in sorted(set(old['balances']) | set(new['balances'])):
            if asset == quote:
                continue
            before, after = old['balances'].get(asset, '0'), new['balances'].get(asset, '0')
            delta = _number(after) - _number(before)
            nominal = quantities.get(asset, Decimal(0))
            if delta or nominal:
                positions.append(dict(asset=asset, before=before, after=after,
                    delta=str(delta), from_new_fills=str(nominal), rounding_delta=str(delta - nominal)))
    return cash, fee_report, _bounded(positions), _bounded(fills)


def _order_changes(a, b):
    new, changed = [], []
    confirmations = 0
    for key in sorted(b['orders']):
        row = b['orders'][key]
        if key not in a['orders']:
            new.append(dict(client_order_id=key, status=row['status'],
                            account_fill_present=key in b['book']['orders']))
        elif row != a['orders'][key]:
            old = a['orders'][key]
            confirmed_existing = (old['status'] != 'filled' and row['status'] == 'filled'
                                  and key in a['book']['orders'])
            confirmations += confirmed_existing
            changed.append(dict(client_order_id=key, before_status=old['status'],
                after_status=row['status'], before_filled=old['filled'], after_filled=row['filled'],
                confirmed_existing_fill=confirmed_existing))
    return dict(new=_bounded(new), changed=_bounded(changed),
                confirmed_existing_fills=confirmations,
                unresolved_before=sum(o['status'] != 'filled' for o in a['orders'].values()),
                unresolved_after=sum(o['status'] != 'filled' for o in b['orders'].values()))


def compare_evidence(before_path, after_path):
    """Compare complete validated saved files; no source DB or output mutation.

    No claim that these files still occupy the paths after their completed reads.
    Captured byte hashes identify the actual inputs to the result.
    """
    before_raw, before_report = _read_evidence(before_path)
    after_raw, after_report = _read_evidence(after_path)
    left, right = json.loads(before_raw), json.loads(after_raw)
    trades = _compatible(left, right)
    a, b = left['payload'], right['payload']
    cash, fees, positions, fills = _account_changes(a, b, trades)
    marks = []
    for symbol in sorted(set(a['book']['tickers']) | set(b['book']['tickers'])):
        old, new = a['book']['tickers'].get(symbol), b['book']['tickers'].get(symbol)
        if old != new:
            for value in (old, new):
                if value is not None:
                    _number(value['last'])
            marks.append(dict(symbol=symbol,
                before=None if old is None else dict(price=old['last'], timestamp_ms=old['timestamp']),
                after=None if new is None else dict(price=new['last'], timestamp_ms=new['timestamp'])))
    old_control, new_control = a.get('control'), b.get('control')
    warnings = ['unsigned_snapshots', 'intermediate_events_not_recorded', 'saved_prices_not_live']
    if datetime.fromisoformat(right['captured_at']) < datetime.fromisoformat(left['captured_at']):
        warnings.append('capture_clock_reversed')
    valuation_before, valuation_after = (before_report['account']['marked_equity'],
                                        after_report['account']['marked_equity'])
    def identity(value):
        return {key: value[key] for key in ('snapshot', 'content_sha256', 'captured_at')}
    return dict(schema_version=1, source='paper_evidence_comparison', mode='paper',
        read_only=True, live_enabled=False, scope='offline_observations_not_current_account',
        compared_at=datetime.now(timezone.utc).isoformat(),
        before=identity(left), after=identity(right),
        relation='same_snapshot' if left['snapshot']['revision'] == right['snapshot']['revision'] else 'forward_snapshot',
        revision_delta=str(int(right['snapshot']['revision']) - int(left['snapshot']['revision'])),
        quote_currency=a['initial']['quote_currency'], cash=cash, fees=fees,
        positions=positions, new_fills=fills, orders=_order_changes(a, b), marks=_bounded(marks),
        valuation=dict(before=valuation_before, after=valuation_after,
            delta=_delta(valuation_before, valuation_after), basis='saved_marks_not_realized_pnl'),
        control=dict(before=old_control, after=new_control, changed=old_control != new_control,
            generation_delta=None if old_control is None else new_control['generation'] - old_control['generation'],
            intermediate_changes='not_recorded'),
        reconciliation=dict(balances_match=True, fees_match=True,
            method='original_decimal_context_suffix_replay', live_authorization=False),
        warnings=warnings)
