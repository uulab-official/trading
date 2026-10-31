"""Chronological bounded experiments, reusing PaperRunner for execution.

Selection sees training segments only. Each test has fresh cash/risk state.
This is endpoint-based independent-window research, not a portfolio simulator.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
from .research_report import CONTEXT, DEFAULT_CANDIDATES, DEFAULT_SETTINGS, canonical, checked_metrics, experiment, fold_ranges, integer, number, selection, summary, validate_report

def run_research(data, fixture_sha256, *, candidates=None, settings=None, evaluator=None):
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
    evaluate = evaluator or evaluate_with_runner
    engine = {k: s[k] for k in ('initial_balance', 'position_size_pct', 'fee_pct', 'slippage_pct', 'max_daily_loss_pct', 'max_drawdown_pct')}
    engine.update(symbol=spec['symbol'], timeframe=spec['timeframe'])

    def evaluate_window(c, start, end):
        warm = clean[max(0, start - s['warmup']):start]
        return checked_metrics(evaluate(dict(c), warm, clean[start:end], dict(engine)), s)
    folds = []
    for bounds in fold_ranges(spec):
        scores = [dict(candidate_id=c['id'], metrics=evaluate_window(c, bounds['train_start'], bounds['train_end'])) for c in spec['candidates']]
        chosen = selection(scores, s['min_training_fills'])
        c = next((c for c in spec['candidates'] if c['id'] == chosen), None)
        result = evaluate_window(c, bounds['test_start'], bounds['test_end']) if c else None
        folds.append(dict(**bounds, train_scores=scores, selected=chosen, test=result))
    result = dict(schema_version=1, kind='uulab_walk_forward', mode='paper', read_only=True, live_enabled=False, generated_at=datetime.now(timezone.utc).isoformat(), run_id=hashlib.sha256(canonical(spec).encode()).hexdigest(), experiment=spec, folds=folds, summary=summary(folds, spec))
    return validate_report(result)

def evaluate_with_runner(candidate, warmup, bars, options):
    """Use the actual existing engine. Warm-up changes indicators only, not cash.

    Warm-up signals are deliberately discarded. A first evaluation-bar signal
    can execute no earlier than the next evaluation bar; no orders cross splits.
    """
    import asyncio
    from trading_data import Candle
    from trading_strategy import MACrossStrategy, RSIStrategy, StrategyConfig
    from trading_bot.paper_runner import PaperRunner, PaperRunConfig
    from trading_bot.replay_report import build_report
    parameters = {k: v for k, v in candidate.items() if k != 'id'}
    config = PaperRunConfig(**options, **parameters)

    def strategy_factory():
        cfg = StrategyConfig(name='walk-forward', symbol=config.symbol, timeframe=config.timeframe)
        strategy = RSIStrategy(cfg, rsi_period=config.rsi_period) if config.strategy == 'rsi' else MACrossStrategy(cfg, fast_period=config.fast_period, slow_period=config.slow_period)
        with localcontext(CONTEXT):
            for row in warmup:
                strategy.on_candle(Candle(*row))
        return strategy
    result = asyncio.run(PaperRunner(config, strategy_factory=strategy_factory).run(bars))
    report = build_report(result, fixture_sha256=hashlib.sha256(canonical(bars).encode()).hexdigest(), first_bar_ms=bars[0][0], last_bar_ms=bars[-1][0], mark_price=bars[-1][4])
    account = report['account']
    with localcontext(CONTEXT):
        held = Decimal(account['equity']) - Decimal(account['cash'])
    return dict(cash=account['cash'], equity=account['equity'], position_value=str(held), fees=account['total_fees'], fills=report['fill_count'], return_pct=account['total_return_pct'], daily_loss_halted=report['daily_loss_halted'], drawdown_halted=report['drawdown_halted'])
