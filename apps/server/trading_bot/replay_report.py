"""Bounded read model of existing PaperRunner results; never an execution engine.

Arithmetic validation is not provenance authentication. Only a trusted local
producer should write reports; the report is never used to authorize orders.
"""
from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_FILLS = 10000
CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
CONFIG_FIELDS = {'mode', 'symbol', 'timeframe', 'strategy', 'initial_balance',
                 'position_size_pct', 'fee_pct', 'slippage_pct', 'max_daily_loss_pct',
                 'max_drawdown_pct', 'fast_period', 'slow_period', 'rsi_period'}


def _amount(value, *, positive=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError('decimal text required')
    try:
        number = Decimal(value)
        if (not number.is_finite() or number < 0 or number > Decimal('1e30')
                or (positive and number == 0) or number.as_tuple().exponent < -60):
            raise ValueError('unsupported amount')
        return number
    except DecimalException as error:
        raise ValueError('invalid amount') from error


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError('invalid text')
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('invalid integer')
    return value


def _configuration(value):
    if not isinstance(value, dict):
        raise ValueError('invalid configuration')
    result = {k: v for k, v in value.items() if k in CONFIG_FIELDS}
    if result.get('mode') != 'paper' or result.get('strategy') not in {'ma_cross', 'rsi'}:
        raise ValueError('unsupported mode or strategy')
    if not re.fullmatch(r'[A-Z0-9._-]{1,24}/(?:USDT|KRW)', _text(result.get('symbol'))):
        raise ValueError('unsupported spot symbol')
    base, quote = result['symbol'].split('/')
    if base == quote or not re.fullmatch(r'[1-9][0-9]{0,5}[mhdw]', _text(result.get('timeframe'))):
        raise ValueError('invalid market configuration')
    for key in ('initial_balance', 'fee_pct', 'slippage_pct'):
        _amount(result.get(key), positive=key == 'initial_balance')
    for key in result.keys() - {'mode', 'symbol', 'timeframe', 'strategy'}:
        if key.endswith('_period'):
            _integer(result[key], 1, 500)
        elif key.endswith('_pct'):
            rate = _amount(result[key])
            if rate > 100 or (key in {'fee_pct', 'slippage_pct'} and rate >= 100):
                raise ValueError('invalid percentage')
    return result


def _compose(configuration, fills, *, fixture_sha256, first_bar_ms, last_bar_ms,
             bars_processed, mark_price, generated_at, daily_loss_halted, drawdown_halted):
    config = _configuration(configuration)
    if not isinstance(fixture_sha256, str) or not re.fullmatch('[0-9a-f]{64}', fixture_sha256):
        raise ValueError('invalid fixture hash')
    first = _integer(first_bar_ms, 0, 253402300799999)
    last = _integer(last_bar_ms, first, 253402300799999)
    bars = _integer(bars_processed, 1, 10_000_000)
    if type(daily_loss_halted) is not bool or type(drawdown_halted) is not bool:
        raise ValueError('invalid risk flags')
    date = datetime.fromisoformat(_text(generated_at).replace('Z', '+00:00'))
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError('generation time requires timezone')
    if not isinstance(fills, list) or len(fills) > MAX_FILLS:
        raise ValueError('unsupported fill count')
    base, quote = config['symbol'].split('/')
    initial = _amount(config['initial_balance'], positive=True)
    mark = _amount(mark_price, positive=True)
    fee_rate = _amount(config['fee_pct'])
    cash, held, fees = initial, Decimal('0'), Decimal('0')
    seen_ids, seen_keys, clean_fills = set(), set(), []
    previous_time = first
    with localcontext(CONTEXT):
        for item in fills:
            identifier, key = _text(item['id']), _text(item['client_id'])
            timestamp = _integer(item['timestamp_ms'], previous_time, last)
            if identifier in seen_ids or key in seen_keys or item['symbol'] != config['symbol']:
                raise ValueError('duplicate or mismatched fill')
            if item['side'] not in {'buy', 'sell'} or item['fee_currency'] != quote:
                raise ValueError('unsupported side or fee currency')
            quantity = _amount(item['quantity'], positive=True)
            price, fee = _amount(item['price'], positive=True), _amount(item['fee'])
            cost = quantity * price
            if fee != cost * fee_rate / 100:
                raise ValueError('fee differs from configured paper fee')
            if item['side'] == 'buy':
                cash -= cost + fee
                held += quantity
            else:
                held -= quantity
                cash += cost - fee
            if cash < 0 or held < 0:
                raise ValueError('negative unleveraged balance')
            fees += fee
            seen_ids.add(identifier); seen_keys.add(key); previous_time = timestamp
            clean_fills.append(dict(id=identifier, client_id=key, symbol=config['symbol'],
                                    side=item['side'], quantity=str(quantity), price=str(price),
                                    fee=str(fee), fee_currency=quote, timestamp_ms=timestamp))
        value = held * mark
        equity = cash + value
        account = dict(currency=quote, initial_balance=str(initial), cash=str(cash),
                       equity=str(equity), total_fees=str(fees), total_pnl=str(equity-initial),
                       total_return_pct=str((equity-initial)/initial*100))
        positions = [dict(asset=base, quantity=str(held), mark_price=str(mark),
                          market_value=str(value), currency=quote)] if held else []
    identity = json.dumps({'configuration': config, 'fixture_sha256': fixture_sha256},
                          sort_keys=True, separators=(',', ':'), allow_nan=False)
    return dict(schema_version=1, kind='uulab_paper_replay', mode='paper', read_only=True,
                live_enabled=False, source='local_fixture', status='fixture_complete',
                run_id=hashlib.sha256(identity.encode()).hexdigest(),
                fixture_sha256=fixture_sha256, generated_at=date.astimezone(timezone.utc).isoformat(),
                first_bar_ms=first, last_bar_ms=last, bars_processed=bars,
                mark_price=str(mark), configuration=config, account=account,
                positions=positions, fill_count=len(clean_fills), fills=clean_fills,
                daily_loss_halted=daily_loss_halted, drawdown_halted=drawdown_halted)


def build_report(result, *, fixture_sha256, first_bar_ms, last_bar_ms, mark_price):
    """Project an actual completed runner result; independently check its totals."""
    try:
        if result['mode'] != 'paper' or result['status'] != 'fixture_complete':
            raise ValueError('completed offline replay required')
        config = _configuration(result['assumptions'])
        quote = config['symbol'].split('/')[1]
        if result.get('quote_currency', 'USDT') != quote:
            raise ValueError('quote mismatch')
        raw_fills = result['trades']
        if not isinstance(raw_fills, list) or len(raw_fills) > MAX_FILLS:
            raise ValueError('unsupported fill count')
        fills = []
        for fill in raw_fills:
            if (fill['type'] != 'market' or fill['status'] != 'closed'
                    or _amount(fill['remaining']) != 0
                    or _amount(fill['amount']) != _amount(fill['filled'])
                    or _amount(fill['price']) != _amount(fill['average'])):
                raise ValueError('only complete paper market fills are supported')
            fills.append(dict(id=fill['id'], client_id=fill['clientOrderId'], symbol=fill['symbol'],
                              side=fill['side'], quantity=fill['filled'], price=fill['average'],
                              fee=fill['fee']['cost'], fee_currency=fill['fee']['currency'],
                              timestamp_ms=fill['timestamp']))
        report = _compose(config, fills, fixture_sha256=fixture_sha256,
                          first_bar_ms=first_bar_ms, last_bar_ms=last_bar_ms,
                          bars_processed=result['bars_processed'], mark_price=str(mark_price),
                          generated_at=datetime.now(timezone.utc).isoformat(),
                          daily_loss_halted=result['daily_loss_halted'],
                          drawdown_halted=result['drawdown_halted'])
        for key in ('cash', 'equity', 'total_fees', 'total_return_pct'):
            # Return may be negative; parsing is bounded and compared to computed data.
            raw = result[key]
            if not isinstance(raw, str) or len(raw) > 100 or Decimal(raw) != Decimal(report['account'][key]):
                raise ValueError('runner totals do not reconcile')
        holdings = {p['asset']: Decimal(p['quantity']) for p in report['positions']}
        if not isinstance(result['holdings'], dict) or holdings != {k: _amount(v, positive=True) for k, v in result['holdings'].items()}:
            raise ValueError('runner holdings do not reconcile')
        return report
    except (KeyError, TypeError, DecimalException, OverflowError) as error:
        raise ValueError('invalid replay result') from error


def validate_report(report):
    """Return only whitelisted fields after checking every published total."""
    try:
        if (type(report['schema_version']) is not int or report['schema_version'] != 1
                or report['kind'] != 'uulab_paper_replay' or report['mode'] != 'paper'
                or report['read_only'] is not True or report['live_enabled'] is not False
                or report['source'] != 'local_fixture' or report['status'] != 'fixture_complete'):
            raise ValueError('unsupported report')
        fields = ('fixture_sha256', 'first_bar_ms', 'last_bar_ms', 'bars_processed',
                  'mark_price', 'generated_at', 'daily_loss_halted', 'drawdown_halted')
        expected = _compose(report['configuration'], report['fills'], **{k: report[k] for k in fields})
        if any(report[k] != expected[k] for k in ('run_id', 'account', 'positions', 'fill_count')):
            raise ValueError('report totals do not reconcile')
        return expected
    except (KeyError, TypeError, DecimalException, OverflowError) as error:
        raise ValueError('invalid replay report') from error


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def read_report(path):
    if path is None or not Path(path).is_file():
        raise ValueError('regular report file required')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('report too large')
    try:
        report = json.loads(raw, object_pairs_hook=_unique_object)
        return validate_report(report)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid report encoding') from error


def write_report(path, report):
    """Atomic no-clobber publication under the producer's report-path writer lock.

    Existing files must contain the same immutable completed replay. This function
    neither replaces another experiment nor serves as an authoritative checkpoint.
    """
    destination = Path(path)
    if destination.is_symlink():
        raise ValueError('report symlinks are not supported')
    normalized = validate_report(report)
    if destination.exists():
        old = read_report(destination)
        comparable = lambda value: {k: v for k, v in value.items() if k != 'generated_at'}
        if comparable(old) != comparable(normalized):
            raise ValueError('use a new report path for a different replay')
        return old
    body = json.dumps(normalized, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(body) > MAX_REPORT_BYTES:
        raise ValueError('report too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.replay-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        # Atomic create-if-absent: never clobber a file created by an unrelated
        # writer after the existence check. Both paths are on the same filesystem.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized
