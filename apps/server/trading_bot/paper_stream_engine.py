"""Bounded original-engine transitions for delayed closed-candle PAPER fills.

This module owns no files, clocks, source identity, or execution adapter other
than the original offline PaperExchange. A caller must commit a candidate bar,
its fill and the complete returned state together. Structural state validation
is not an economic audit; authoritative recovery replays the retained journal.

Source numbers retain the original Upbit fixed-point representation. Account
numbers retain str(Decimal) representation, including trailing zeroes, so a
carried holding produces the original fill quantity when it is later sold.
Account values are finite, nonnegative, at most 1e100, have exponent -100..100,
and occupy at most 100 characters. Initial cash/config decimals use the smaller
source bounds (1e18 and exponent -18..18). Limits reject, never round or clamp.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
import re

from trading_bot.paper_runner import PaperRunConfig, PaperRunner
from trading_core.order_manager import OrderManager
from trading_core.paper_exchange import PaperExchange, PAPER_CALCULATION_CONTEXT
from trading_data import Candle, CandleBuffer
from trading_data.upbit_history import _decimal_text, UNITS, timeframe


MAX_BARS = 100_000
MAX_WINDOW = 500
MAX_OPEN_MS = 253402300799999
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FIELDS = frozenset({'schema_version', 'config', 'bootstrap_count', 'next_index',
    'last_open_ms', 'candles', 'cash', 'holding', 'equity', 'total_fees',
    'fill_count', 'risk_day', 'daily_start_equity', 'peak_equity',
    'daily_loss_halted', 'drawdown_halted', 'pending_signal', 'strategy_state'})
_CONFIG_DECIMALS = ('initial_balance', 'position_size_pct', 'fee_pct',
                    'slippage_pct', 'max_daily_loss_pct', 'max_drawdown_pct')
_ACCOUNT_DECIMALS = ('cash', 'holding', 'equity', 'total_fees',
                     'daily_start_equity', 'peak_equity')
_TIMEFRAMES = frozenset(timeframe(unit) for unit in UNITS)


def _invalid():
    return ValueError('paper_stream_engine_invalid')


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise _invalid()
    return value


def _number(value, *, maximum=Decimal('1e100'), exponent=-100):
    if type(value) is not str or not 1 <= len(value) <= 100:
        raise _invalid()
    try:
        number = Decimal(value)
        if (not number.is_finite() or number.is_signed() or number > maximum
                or not exponent <= number.as_tuple().exponent <= maximum.adjusted()
                or str(number) != value):
            raise _invalid()
    except DecimalException:
        raise _invalid() from None
    return number


def _configuration(value):
    if type(value) is not dict or set(value) != set(PaperRunConfig.__dataclass_fields__):
        raise _invalid()
    if (value['mode'] != 'paper' or type(value['symbol']) is not str
            or not re.fullmatch(r'[A-Z0-9]{1,20}/KRW', value['symbol'])
            or value['symbol'] == 'KRW/KRW' or type(value['timeframe']) is not str
            or value['timeframe'] not in _TIMEFRAMES
            or value['strategy'] not in ('ma_cross', 'rsi')):
        raise _invalid()
    for name in _CONFIG_DECIMALS:
        _number(value[name], maximum=Decimal('1e18'), exponent=-18)
    try:
        config = PaperRunConfig(**value)
        if config.identity() != value:
            raise _invalid()
        return config
    except (DecimalException, ValueError, TypeError, AttributeError):
        raise _invalid() from None


def _minimum_history(config):
    return max(50, config.slow_period if config.strategy == 'ma_cross' else config.rsi_period + 1)


def _candle(row, duration):
    if type(row) is not list or len(row) != 6:
        raise _invalid()
    opened = _integer(row[0], 0, MAX_OPEN_MS)
    if opened % duration:
        raise _invalid()
    try:
        for index, value in enumerate(row[1:]):
            if type(value) is not str or _decimal_text(value, positive=index < 4) != value:
                raise _invalid()
        return Candle(*row)
    except (DecimalException, ValueError, TypeError):
        raise _invalid() from None


def _history(rows, duration):
    if type(rows) is not list or not 1 <= len(rows) <= MAX_WINDOW:
        raise _invalid()
    candles = []
    for row in rows:
        candle = _candle(row, duration)
        if candles and candle.timestamp != candles[-1].timestamp + duration:
            raise _invalid()
        candles.append(candle)
    return candles


def _day(opened):
    return (_EPOCH + timedelta(milliseconds=opened)).date().isoformat()


def _strategy_state(strategy, kind):
    if kind == 'ma_cross':
        return dict(kind=kind, prev_fast_above_slow=strategy._prev_fast_above_slow,
                    consecutive_bars=strategy._consecutive_bars)
    return dict(kind=kind, prev_rsi=str(strategy._prev_rsi))


def _validate(state):
    if type(state) is not dict or set(state) != _FIELDS:
        raise _invalid()
    if type(state['schema_version']) is not int or state['schema_version'] != 1:
        raise _invalid()
    config = _configuration(state['config'])
    bootstrap = _integer(state['bootstrap_count'], _minimum_history(config), MAX_WINDOW)
    consumed = _integer(state['next_index'], 0, MAX_BARS)
    _integer(state['fill_count'], 0, max(0, consumed - 1))
    opened = _integer(state['last_open_ms'], 0, MAX_OPEN_MS)
    duration = CandleBuffer(timeframe=config.timeframe).duration_ms
    candles = _history(state['candles'], duration)
    if len(candles) != min(MAX_WINDOW, bootstrap + consumed) or candles[-1].timestamp != opened:
        raise _invalid()
    numbers = {name: _number(state[name]) for name in _ACCOUNT_DECIMALS}
    if (numbers['equity'] != numbers['cash'] + numbers['holding'] * candles[-1].close
            or numbers['peak_equity'] < max(config.initial_balance, numbers['equity'])
            or type(state['daily_loss_halted']) is not bool
            or type(state['drawdown_halted']) is not bool):
        raise _invalid()
    if state['risk_day'] != (_day(opened) if consumed else None):
        raise _invalid()
    pending = state['pending_signal']
    if pending is not None:
        if (type(pending) is not dict or set(pending) != {'action', 'source_index'}
                or pending['action'] not in ('buy', 'sell') or not consumed
                or type(pending['source_index']) is not int
                or pending['source_index'] != consumed - 1):
            raise _invalid()
    strategy = state['strategy_state']
    if type(strategy) is not dict or strategy.get('kind') != config.strategy:
        raise _invalid()
    if config.strategy == 'ma_cross':
        if (set(strategy) != {'kind', 'prev_fast_above_slow', 'consecutive_bars'}
                or type(strategy['prev_fast_above_slow']) is not bool):
            raise _invalid()
        _integer(strategy['consecutive_bars'], 0, bootstrap + consumed)
    else:
        if set(strategy) != {'kind', 'prev_rsi'}:
            raise _invalid()
        _number(strategy['prev_rsi'], maximum=Decimal('100'))
    if not consumed and (pending is not None or state['fill_count']
            or state['daily_loss_halted'] or state['drawdown_halted']
            or any(numbers[name] != config.initial_balance
                   for name in ('cash', 'equity', 'daily_start_equity', 'peak_equity'))
            or numbers['holding'] or numbers['total_fees']):
        raise _invalid()
    return config, candles


def validate_state(state):
    """Return a detached, structurally validated state, without repairing it.

    This bounds one snapshot and checks its directly derivable invariants. It
    does not establish lifetime cash, risk, or strategy truth; replay does that.
    """
    with localcontext(PAPER_CALCULATION_CONTEXT):
        _validate(state)
        return deepcopy(state)


def initial_state(config, bootstrap):
    """Warm indicators only; discard every bootstrap signal and create no fill."""
    if type(config) is not PaperRunConfig:
        raise _invalid()
    with localcontext(PAPER_CALCULATION_CONTEXT):
        config = _configuration(config.identity())
        duration = CandleBuffer(timeframe=config.timeframe).duration_ms
        candles = _history(bootstrap, duration)
        if len(candles) < _minimum_history(config):
            raise _invalid()
        strategy = PaperRunner(config)._strategy()
        for candle in candles:
            strategy.on_candle(candle)
        initial = str(config.initial_balance)
        state = dict(schema_version=1, config=config.identity(), bootstrap_count=len(candles),
            next_index=0, last_open_ms=candles[-1].timestamp, candles=deepcopy(bootstrap),
            cash=initial, holding='0', equity=initial, total_fees='0', fill_count=0,
            risk_day=None, daily_start_equity=initial, peak_equity=initial,
            daily_loss_halted=False, drawdown_halted=False, pending_signal=None,
            strategy_state=_strategy_state(strategy, config.strategy))
        return validate_state(state)


async def advance_state(state, candle):
    """Return the next detached account state and at most one original fill."""
    try:
        return await _advance_state(state, candle)
    except DecimalException:
        # The original quantity quantizer can refuse finite extreme inputs.
        # Do not widen its precision or commit a partially calculated candidate.
        raise _invalid() from None


async def _advance_state(state, candle):
    with localcontext(PAPER_CALCULATION_CONTEXT):
        config, retained = _validate(state)
        if state['next_index'] >= MAX_BARS:
            raise ValueError('paper_stream_engine_capacity')
        duration = CandleBuffer(timeframe=config.timeframe).duration_ms
        new_candle = _candle(candle, duration)
        if new_candle.timestamp != state['last_open_ms'] + duration:
            raise _invalid()
        next_state = deepcopy(state)
        runner = PaperRunner(config)
        strategy = runner._strategy()
        # Directly restore the completed buffer. on_candle would replay signals
        # and apply its initial 50-bar analysis gate to an arbitrary retained tail.
        # _validate has already checked every row and exact adjacency. Extending
        # its bounded deque avoids repeating add_candle's retained-duplicate scan
        # for every restored row (quadratic work at each restart/transition).
        strategy.candles._candles.extend(retained)
        saved = state['strategy_state']
        if config.strategy == 'ma_cross':
            strategy._prev_fast_above_slow = saved['prev_fast_above_slow']
            strategy._consecutive_bars = saved['consecutive_bars']
        else:
            strategy._prev_rsi = Decimal(saved['prev_rsi'])
        exchange = PaperExchange(initial_balance=state['cash'],
            initial_positions={config.symbol: state['holding']},
            fee_pct=config.fee_pct, slippage_pct=config.slippage_pct,
            quote_currency='KRW')
        manager = OrderManager(exchange)
        await exchange.connect()
        try:
            exchange.set_price(config.symbol, new_candle.open, timestamp=new_candle.timestamp)
            day = _day(new_candle.timestamp)
            if day != next_state['risk_day']:
                next_state['risk_day'] = day
                next_state['daily_start_equity'] = str(exchange.get_total_value())
                next_state['daily_loss_halted'] = False
            if next_state['pending_signal'] is not None:
                await runner._execute_pending(exchange, manager, next_state['pending_signal'], next_state)
                if await manager.get_open_orders():
                    raise ValueError('paper_stream_engine_unresolved_order')
            exchange.set_price(config.symbol, new_candle.close, timestamp=new_candle.timestamp)
            equity = exchange.get_total_value()
            daily_start = Decimal(next_state['daily_start_equity'])
            peak = max(Decimal(next_state['peak_equity']), equity)
            next_state['peak_equity'] = str(peak)
            if daily_start > 0 and (daily_start - equity) / daily_start * 100 >= config.max_daily_loss_pct:
                next_state['daily_loss_halted'] = True
            if peak > 0 and (peak - equity) / peak * 100 >= config.max_drawdown_pct:
                next_state['drawdown_halted'] = True
            signal = strategy.on_candle(new_candle)
            action = signal.type.value if signal is not None else None
            if action not in (None, 'hold', 'buy', 'sell', 'close'):
                raise ValueError('paper_stream_engine_unknown_signal')
            next_state['pending_signal'] = (
                dict(action='sell' if action == 'close' else action,
                     source_index=next_state['next_index'])
                if action in ('buy', 'sell', 'close') else None)
            book = exchange.snapshot()
            fills = book['trades']
            if len(fills) > 1:
                raise ValueError('paper_stream_engine_multiple_fills')
            base = config.symbol.split('/')[0]
            next_state.update(next_index=next_state['next_index'] + 1,
                last_open_ms=new_candle.timestamp, candles=(next_state['candles'] + [deepcopy(candle)])[-MAX_WINDOW:],
                cash=book['balances']['KRW'], holding=book['balances'][base], equity=str(equity),
                total_fees=str(Decimal(state['total_fees']) + exchange.total_fees),
                fill_count=state['fill_count'] + len(fills),
                strategy_state=_strategy_state(strategy, config.strategy))
            return validate_state(next_state), fills[0] if fills else None
        finally:
            await exchange.disconnect()
