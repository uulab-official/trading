"""Chronological bounded experiments, reusing PaperRunner for execution.

Selection sees training segments only. Each test has fresh cash/risk state.
This is endpoint-based independent-window research, not a portfolio simulator.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
import hashlib
from .research_report import (cash_metrics, check_robustness_budget, cost_scenarios,
                              research_identity, robustness_policy, robustness_summary)
from .research_report import CONTEXT, DEFAULT_CANDIDATES, DEFAULT_SETTINGS, canonical, checked_metrics, experiment, fold_ranges, integer, number, selection, summary, validate_report

def run_research(data, fixture_sha256, *, candidates=None, settings=None, evaluator=None,
                 robustness=False, benchmark_evaluator=None):
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
    evaluate = evaluator or evaluate_with_runner
    engine = {k: s[k] for k in ('initial_balance', 'position_size_pct', 'fee_pct', 'slippage_pct', 'max_daily_loss_pct', 'max_drawdown_pct')}
    engine.update(symbol=spec['symbol'], timeframe=spec['timeframe'])

    def evaluate_window(c, start, end, options=None):
        warm = clean[max(0, start - s['warmup']):start]
        return checked_metrics(evaluate(dict(c), warm, clean[start:end], dict(engine if options is None else options)), s)
    folds, diagnostics = [], []
    for bounds in fold_ranges(spec):
        scores = [dict(candidate_id=c['id'], metrics=evaluate_window(c, bounds['train_start'], bounds['train_end'])) for c in spec['candidates']]
        chosen = selection(scores, s['min_training_fills'])
        c = next((c for c in spec['candidates'] if c['id'] == chosen), None)
        result = evaluate_window(c, bounds['test_start'], bounds['test_end']) if c else None
        folds.append(dict(**bounds, train_scores=scores, selected=chosen, test=result))
        if robustness:
            start, end = bounds['test_start'], bounds['test_end']
            rows = []
            baseline = benchmark_evaluator or evaluate_benchmark_with_exchange
            for i, costs in enumerate(cost_scenarios(s)):
                options = {**engine, 'fee_pct':costs['fee_pct'], 'slippage_pct':costs['slippage_pct']}
                stressed = (result if i == 0 else evaluate_window(c, start, end, options)) if c else cash_metrics(s)
                benchmark = checked_metrics(baseline(clean[start:end], dict(options)), s)
                with localcontext(CONTEXT):
                    delta = number(stressed['return_pct'], signed=True) - number(benchmark['return_pct'], signed=True)
                rows.append(dict(**costs, strategy=stressed, buy_hold=benchmark, excess_return_pp=str(delta)))
            diagnostics.append(dict(entry_bar_ms=clean[start+1][0], mark_bar_ms=clean[end-1][0],
                entry_open=clean[start+1][1], terminal_close=clean[end-1][4], scenarios=rows))
    result = dict(schema_version=2 if robustness else 1, kind='uulab_walk_forward', mode='paper', read_only=True, live_enabled=False, generated_at=datetime.now(timezone.utc).isoformat(), run_id=research_identity(spec, robustness), experiment=spec, folds=folds, summary=summary(folds, spec))
    if robustness:
        result['robustness'] = dict(policy=robustness_policy(), folds=diagnostics, summary=robustness_summary(diagnostics))
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


def evaluate_benchmark_with_exchange(bars, options):
    """One same-allocation buy at the second scored open; no active risk exits.

    Original PaperExchange owns fill price, fees, cash and holdings. Independent
    reference arithmetic in report validation checks the resulting metrics.
    """
    import asyncio
    from trading_core.paper_exchange import PaperExchange

    async def evaluate():
        if len(bars) < 2:
            raise ValueError('at least two scored bars required')
        with localcontext(CONTEXT):
            initial = number(options['initial_balance'])
            fee, slip = number(options['fee_pct']), number(options['slippage_pct'])
            symbol = options['symbol']
            base, quote = symbol.split('/')
            exchange = PaperExchange(initial_balance=initial, fee_pct=fee,
                                     slippage_pct=slip, quote_currency=quote)
            await exchange.connect()
            try:
                opening = number(str(bars[1][1]))
                exchange.set_price(symbol, opening, timestamp=bars[1][0])
                price = opening * (1 + slip / 100)
                quantity = (initial * number(options['position_size_pct']) / 100 /
                            (price * (1 + fee / 100))).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                if quantity > 0:
                    await exchange.create_order(symbol, 'market', 'buy', quantity,
                                                params={'clientOrderId':'research-reference'})
                exchange.set_price(symbol, number(str(bars[-1][4])), timestamp=bars[-1][0])
                balances = await exchange.fetch_balance()
                cash = balances[quote].free
                equity = exchange.get_total_value()
                return dict(cash=str(cash), equity=str(equity), position_value=str(equity-cash),
                            fees=str(exchange.total_fees), fills=int(quantity > 0),
                            return_pct=str((equity-initial)/initial*100),
                            daily_loss_halted=False, drawdown_halted=False)
            finally:
                await exchange.disconnect()
    return asyncio.run(evaluate())
