"""Pure original research-input preflight, shared with read-only inspection.

These are the producer's existing constraints under explicit candidates and
settings. Acceptance does not authenticate data or reproduce research execution.
"""
from .research_report import (DEFAULT_CANDIDATES, DEFAULT_SETTINGS,
                              check_robustness_budget, experiment, integer, number)


def prepare_research_input(data, fixture_sha256, *, candidates=None, settings=None,
                           robustness=False):
    if type(robustness) is not bool:
        raise ValueError('robustness must be boolean')
    if not isinstance(data, dict) or data.get('mode', 'paper') != 'paper':
        raise ValueError('paper history required')
    if settings is not None and (not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS)):
        raise ValueError('unknown settings')
    rows = data.get('candles')
    if not isinstance(rows, list) or not rows:
        raise ValueError('history required')
    if any((not isinstance(row, list) or len(row) != 6 for row in rows)):
        raise ValueError('six-column candles required')
    if 'data_provenance' in data:
        from trading_data.upbit_history import validate_market_fixture
        validate_market_fixture(data)
    spec = experiment(dict(symbol=data.get('symbol', 'BTC/USDT'), timeframe=data.get('timeframe', '1h'), bars=len(rows), first_bar_ms=rows[0][0], fixture_sha256=fixture_sha256, candidates=DEFAULT_CANDIDATES if candidates is None else candidates, settings={**DEFAULT_SETTINGS, **(settings or {})}))
    if robustness:
        check_robustness_budget(spec)
    s = spec['settings']
    frame = spec['timeframe']
    duration = int(frame[:-1]) * {'m': 60000, 'h': 3600000, 'd': 86400000, 'w': 604800000}[frame[-1]]
    clean = []
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError('six-column candles required')
        timestamp = integer(row[0], 0, 253402300799999)
        if timestamp != spec['first_bar_ms'] + i * duration:
            raise ValueError('contiguous chronological bars required')
        values = [number(str(v)) for v in row[1:]]
        o, h, l, c, volume = values
        if min(o, h, l, c) <= 0 or not l <= o <= h or (not l <= c <= h):
            raise ValueError('invalid OHLC')
        clean.append((timestamp, *(str(v) for v in values)))
    clean = tuple(clean)
    return spec, clean
