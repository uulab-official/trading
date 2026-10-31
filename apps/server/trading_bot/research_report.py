"""Bounded, dependency-free research contract. Not a trading authorization.

Arithmetic/identity checks are not proof of data origin or investment validity.
Only the trusted local producer should write this immutable read model.
"""
from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
CONTEXT = Context(prec=28)
MAX_BYTES = 2 * 1024 * 1024
MAX_WORK = 5000000
DEFAULT_CANDIDATES = [{'id': 'ma-3-5', 'strategy': 'ma_cross', 'fast_period': 3, 'slow_period': 5}, {'id': 'ma-5-20', 'strategy': 'ma_cross', 'fast_period': 5, 'slow_period': 20}, {'id': 'rsi-14', 'strategy': 'rsi', 'rsi_period': 14}]
DEFAULT_SETTINGS = dict(train_size=120, test_size=60, gap=1, warmup=100, min_training_fills=2, anchored=False, initial_balance='10000', position_size_pct='20', fee_pct='0.1', slippage_pct='0.05', max_daily_loss_pct='3', max_drawdown_pct='10')
METRIC_KEYS = {'cash', 'equity', 'position_value', 'fees', 'fills', 'return_pct', 'daily_loss_halted', 'drawdown_halted'}

def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('unexpected or missing fields')

def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid integer')
    return value

def number(value, *, signed=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError('decimal text required')
    try:
        n = Decimal(value)
        if not n.is_finite() or n.copy_abs() > Decimal('1e40') or (not signed and n < 0) or (n.as_tuple().exponent < -60):
            raise ValueError('invalid amount')
        return n
    except DecimalException as error:
        raise ValueError('invalid amount') from error

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def candidate(value):
    if not isinstance(value, dict):
        raise ValueError('candidate object required')
    strategy = value.get('strategy')
    fields = {'id', 'strategy', 'fast_period', 'slow_period'} if strategy == 'ma_cross' else {'id', 'strategy', 'rsi_period'}
    exact(value, fields)
    if not isinstance(value['id'], str) or not re.fullmatch('[a-zA-Z0-9_-]{1,48}', value['id']):
        raise ValueError('invalid candidate id')
    if strategy == 'ma_cross':
        fast = integer(value['fast_period'], 1, 499)
        if integer(value['slow_period'], 2, 500) <= fast:
            raise ValueError('fast must precede slow')
    elif strategy == 'rsi':
        integer(value['rsi_period'], 1, 499)
    else:
        raise ValueError('unsupported strategy')
    return dict(value)

def experiment(value):
    exact(value, {'symbol', 'timeframe', 'bars', 'first_bar_ms', 'fixture_sha256', 'candidates', 'settings'})
    symbol, timeframe = (value['symbol'], value['timeframe'])
    if not isinstance(symbol, str) or not re.fullmatch('[A-Z0-9._-]{1,24}/(?:KRW|USDT)', symbol) or len(set(symbol.split('/'))) != 2:
        raise ValueError('invalid spot symbol')
    if not isinstance(timeframe, str) or not re.fullmatch('[1-9][0-9]{0,4}[mhdw]', timeframe):
        raise ValueError('unsupported timeframe')
    duration = int(timeframe[:-1]) * {'m': 60000, 'h': 3600000, 'd': 86400000, 'w': 604800000}[timeframe[-1]]
    bars = integer(value['bars'], 2, 5000)
    first = integer(value['first_bar_ms'], 0, 253402300799999)
    if first + (bars - 1) * duration > 253402300799999:
        raise ValueError('unrepresentable history')
    if not isinstance(value['fixture_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['fixture_sha256']):
        raise ValueError('invalid source identity')
    candidates = value['candidates']
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
        raise ValueError('one to eight candidates required')
    candidates = sorted((candidate(c) for c in candidates), key=lambda c: c['id'])
    if len({c['id'] for c in candidates}) != len(candidates) or len({canonical({k: v for k, v in c.items() if k != 'id'}) for c in candidates}) != len(candidates):
        raise ValueError('duplicate candidate')
    s = dict(value['settings']) if isinstance(value['settings'], dict) else None
    exact(s, DEFAULT_SETTINGS)
    for name, low, high in [('train_size', 3, 1000), ('test_size', 2, 1000), ('gap', 0, 1000), ('warmup', 1, 500), ('min_training_fills', 1, 10000)]:
        integer(s[name], low, high)
    if type(s['anchored']) is not bool:
        raise ValueError('anchored must be boolean')
    lookback = max((c.get('slow_period', c.get('rsi_period', 0)) for c in candidates))
    if s['warmup'] < lookback + 1 or s['train_size'] < lookback + 2:
        raise ValueError('insufficient history for candidate periods')
    for name in ('initial_balance', 'position_size_pct', 'fee_pct', 'slippage_pct', 'max_daily_loss_pct', 'max_drawdown_pct'):
        amount = number(s[name])
        if amount > Decimal('1e18') or (name not in {'fee_pct', 'slippage_pct'} and amount == 0):
            raise ValueError('invalid engine assumption')
        if name.endswith('_pct') and (amount > 100 or (name in {'fee_pct', 'slippage_pct'} and amount >= 100)):
            raise ValueError('invalid percent')
        s[name] = str(amount)
    normalized = dict(symbol=symbol, timeframe=timeframe, bars=bars, first_bar_ms=first, fixture_sha256=value['fixture_sha256'], candidates=candidates, settings=s)
    fold_ranges(normalized)
    return normalized

def fold_ranges(spec):
    s = spec['settings']
    folds = []
    work = 0
    for start in range(s['train_size'] + s['gap'], spec['bars'] - s['test_size'] + 1, s['test_size']):
        end = start - s['gap']
        begin = 0 if s['anchored'] else end - s['train_size']
        n = end - begin
        work += len(spec['candidates']) * n * (n + 1) // 2 + s['test_size'] * (s['test_size'] + 1) // 2
        if len(folds) >= 24 or work > MAX_WORK:
            raise ValueError('research work budget exceeded')
        folds.append(dict(train_start=begin, train_end=end, test_start=start, test_end=start + s['test_size']))
    if not folds:
        raise ValueError('history has no complete test window')
    return folds

def checked_metrics(value, settings):
    exact(value, METRIC_KEYS)
    cash, equity, held, fees = [number(value[k]) for k in ('cash', 'equity', 'position_value', 'fees')]
    fills = integer(value['fills'], 0, 10000)
    for key in ('daily_loss_halted', 'drawdown_halted'):
        if type(value[key]) is not bool:
            raise ValueError('invalid risk flag')
    initial = number(settings['initial_balance'])
    with localcontext(CONTEXT):
        if cash + held != equity or number(value['return_pct'], signed=True) != (equity - initial) / initial * 100:
            raise ValueError('metrics do not reconcile')
    if fills == 0 and (fees != 0 or cash != initial or held != 0):
        raise ValueError('zero-fill accounting is inconsistent')
    return dict(value)

def selection(scores, minimum):
    eligible = [s for s in scores if s['metrics']['fills'] >= minimum]
    return min(eligible, key=lambda s: (number(s['metrics']['return_pct'], signed=True).copy_negate(), s['candidate_id']))['candidate_id'] if eligible else None

def summary(folds, spec):
    values = [number(f['test']['return_pct'], signed=True) if f['test'] else Decimal(0) for f in folds]
    with localcontext(CONTEXT):
        mean = sum(values) / len(values)
    return dict(total_folds=len(folds), evaluated_folds=sum((f['test'] is not None for f in folds)), skipped_folds=sum((f['test'] is None for f in folds)), positive_folds=sum((v > 0 for v in values)), mean_test_return_pct=str(mean), worst_test_return_pct=str(min(values)), best_test_return_pct=str(max(values)), unused_tail_bars=spec['bars'] - folds[-1]['test_end'])

def validate_report(value):
    try:
        exact(value, {'schema_version', 'kind', 'mode', 'read_only', 'live_enabled', 'generated_at', 'run_id', 'experiment', 'folds', 'summary'})
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'uulab_walk_forward' or (value['mode'] != 'paper') or (value['read_only'] is not True) or (value['live_enabled'] is not False):
            raise ValueError('unsupported research report')
        date = datetime.fromisoformat(value['generated_at'].replace('Z', '+00:00'))
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError('timestamp must be aware')
        spec = experiment(value['experiment'])
        if value['experiment'] != spec:
            raise ValueError('noncanonical experiment')
        identity = hashlib.sha256(canonical(spec).encode()).hexdigest()
        if value['run_id'] != identity:
            raise ValueError('experiment identity differs')
        expected = fold_ranges(spec)
        folds = value['folds']
        if not isinstance(folds, list) or len(folds) != len(expected):
            raise ValueError('incomplete folds')
        for fold, bounds in zip(folds, expected):
            exact(fold, set(bounds) | {'train_scores', 'selected', 'test'})
            if any((type(fold[k]) is not int or fold[k] != v for k, v in bounds.items())):
                raise ValueError('invalid fold bounds')
            scores = fold['train_scores']
            if not isinstance(scores, list) or len(scores) != len(spec['candidates']):
                raise ValueError('candidate scores missing')
            for score, c in zip(scores, spec['candidates']):
                exact(score, {'candidate_id', 'metrics'})
                if score['candidate_id'] != c['id']:
                    raise ValueError('candidate score mismatch')
                checked_metrics(score['metrics'], spec['settings'])
            if fold['selected'] != selection(scores, spec['settings']['min_training_fills']):
                raise ValueError('selection differs from training')
            if fold['selected'] is None:
                if fold['test'] is not None:
                    raise ValueError('unselected test is not allowed')
            else:
                checked_metrics(fold['test'], spec['settings'])
        calculated = summary(folds, spec)
        exact(value['summary'], calculated)
        for key in ('total_folds', 'evaluated_folds', 'skipped_folds', 'positive_folds', 'unused_tail_bars'):
            integer(value['summary'][key], 0, 5000)
        for key in ('mean_test_return_pct', 'worst_test_return_pct', 'best_test_return_pct'):
            number(value['summary'][key], signed=True)
        if value['summary'] != calculated:
            raise ValueError('summary mismatch')
        return json.loads(canonical(value))
    except (KeyError, TypeError, AttributeError, DecimalException, OverflowError) as error:
        raise ValueError('invalid research report') from error

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result

def read_report(path):
    if path is None:
        raise ValueError('report unavailable')
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError('regular report required')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(p, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular report required')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('report too large')
    try:
        return validate_report(json.loads(raw, object_pairs_hook=unique_object))
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid report encoding') from error

def write_report(path, value):
    normalized = validate_report(value)
    destination = Path(path)

    def existing():
        old = read_report(destination)
        if {k: v for k, v in old.items() if k != 'generated_at'} != {k: v for k, v in normalized.items() if k != 'generated_at'}:
            raise ValueError('use a new report path for another experiment')
        return old
    if destination.is_symlink():
        raise ValueError('report symlinks are not supported')
    if destination.exists():
        return existing()
    body = canonical(normalized).encode()
    if len(body) > MAX_BYTES:
        raise ValueError('report too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.research-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return existing()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized
