"""Explicit local-paper operating control, not broker authorization or a kill service."""
from copy import deepcopy

from trading_core.order_manager import OrderSide
from trading_core.paper_admission import PaperOrderRejected

MODES = {'active': 0, 'reduce_only': 1, 'halted': 2}
REASONS = frozenset(('operator', 'risk_review', 'maintenance'))
MAX_GENERATION = 2**31 - 1


def initial_control():
    return dict(mode='halted', generation=0, reason='created')


def validate_control(value):
    if (not isinstance(value, dict) or set(value) != {'mode', 'generation', 'reason'}
            or type(value['mode']) is not str or value['mode'] not in MODES
            or type(value['generation']) is not int
            or not 0 <= value['generation'] <= MAX_GENERATION
            or type(value['reason']) is not str):
        raise ValueError('invalid_paper_control')
    if value['generation'] == 0:
        if value != initial_control():
            raise ValueError('invalid_initial_paper_control')
    elif value['reason'] not in REASONS:
        raise ValueError('invalid_paper_control_reason')
    if value['generation'] == MAX_GENERATION and value['mode'] != 'halted':
        raise ValueError('exhausted_control_must_be_halted')
    return deepcopy(value)


def control_status(value):
    if value is None:
        return dict(enabled=False, mode=None, generation=None, reason=None)
    return dict(enabled=True, **validate_control(value))


def next_control(current, mode, expected_generation, reason):
    if current is None:
        raise ValueError('paper_control_not_enabled')
    current = validate_control(current)
    if (type(mode) is not str or mode not in MODES
            or type(reason) is not str or reason not in REASONS
            or type(expected_generation) is not int or expected_generation < 0):
        raise ValueError('invalid_paper_control_request')
    if expected_generation != current['generation']:
        raise ValueError('stale_paper_control_generation')
    if mode == current['mode']:
        return current
    generation = current['generation'] + 1
    if generation > MAX_GENERATION or (generation == MAX_GENERATION and mode != 'halted'):
        raise ValueError('paper_control_generation_exhausted')
    return dict(mode=mode, generation=generation, reason=reason)


def require_mode(control, side):
    if control is None:
        return
    mode = validate_control(control)['mode']
    if mode == 'halted':
        raise PaperOrderRejected('paper_session_halted')
    if mode == 'reduce_only' and side != OrderSide.SELL:
        raise PaperOrderRejected('paper_session_reduce_only')
