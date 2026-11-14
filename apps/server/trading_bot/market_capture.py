"""Bounded public candle collection, passive observation and explicit export.

This module owns no account, strategy, order or execution process. A saved
capture phase describes the last committed operation, never process liveness.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from trading_data.upbit_history import (
    CLOSE_MARGIN_MS, EPOCH, MarketDataUnavailable, UpbitHistoryClient,
    _milliseconds, candles_hash, parse_end, validate_market_fixture,
)
from trading_data.public_candle_transport import PublicCandleTransport
from .local_report_io import _parent_observation, publish_new_report
from .market_capture_store import (
    CaptureConflict, CaptureUnavailable, capture_writer, read_capture, validate_config,
)

_RETRYABLE = frozenset({'transport_unavailable', 'rate_limited'})
_TERMINAL = frozenset({'rate_blocked', 'request_rejected', 'cooldown_required',
    'data_rejected', 'history_conflict', 'catchup_limit', 'clock_regression'})
_FIXTURE_KEYS = {'mode', 'symbol', 'timeframe', 'candles', 'data_provenance'}


def _utc(value):
    _milliseconds(value)
    return value.astimezone(timezone.utc)


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise CaptureUnavailable('invalid capture option')
    return value


def _observation(snapshot, now, *, clock_error=False):
    now = _utc(now)
    config, state, fixture = snapshot['config'], snapshot['state'], snapshot['fixture']
    duration = config['unit_minutes'] * 60000
    data_age = observation_age = None
    clock_valid = not clock_error
    for name in ('created_at', 'updated_at', 'last_attempt_at', 'last_verified_at'):
        if state[name] is not None and parse_end(state[name]) > now:
            clock_valid = False
    window = dict(bars=0, first_open_ms=None, end_exclusive_ms=None, candles_sha256=None)
    latest = None
    if fixture is not None:
        p, rows = fixture['data_provenance'], fixture['candles']
        window = dict(bars=len(rows), first_open_ms=rows[0][0],
            end_exclusive_ms=p['end_exclusive_ms'], candles_sha256=p['candles_sha256'])
        age = _milliseconds(now) - p['end_exclusive_ms']
        if age < 0:
            clock_valid = False
        else:
            data_age = age
        row = rows[-1]
        latest = dict(open_ms=row[0], close_ms=row[0]+duration,
            **dict(zip(('open', 'high', 'low', 'close', 'volume'), row[1:])))
    if state['last_verified_at'] is not None:
        age = _milliseconds(now) - _milliseconds(parse_end(state['last_verified_at']))
        if age < 0:
            clock_valid = False
        else:
            observation_age = age
    max_data_age = duration + CLOSE_MARGIN_MS + config['poll_seconds']*1000 + 10000
    max_observation_age = max(30000, 2*config['poll_seconds']*1000)
    phase = state['phase']
    if not clock_valid:
        status = 'clock_error'
        # A clock error invalidates age comparisons as a whole, even if one
        # subtraction happens to remain positive.
        data_age = observation_age = None
    elif phase == 'running':
        status = ('current' if data_age is not None and observation_age is not None
            and data_age <= max_data_age and observation_age <= max_observation_age else 'stale')
    else:
        status = 'catching_up' if phase == 'fetching' else phase
    return dict(schema_version=1, source='upbit_market_capture', mode='paper',
        read_only=True, live_enabled=False, scope='closed_public_candle_observation',
        observed_at=now.isoformat(), status=status, configuration=config, capture=state,
        window=window, freshness=dict(data_age_ms=data_age,
            observation_age_ms=observation_age, max_data_age_ms=max_data_age,
            max_observation_age_ms=max_observation_age, clock_valid=clock_valid), latest=latest,
        boundaries=dict(orders=False, account_access=False, process_liveness='not_asserted',
            provider_authenticity='not_authenticated', gap_policy='reject', retention='rolling_window'))


def capture_status(directory, *, now=None):
    """One bounded read transaction; does not create, repair or acquire data."""
    observed = _utc(now if now is not None else datetime.now(timezone.utc))
    return _observation(read_capture(directory), observed)


class _Stopped(Exception):
    def __init__(self, signum):
        self.signum = signum
        self.uncertain_acquisition = False


class _ClockRegression(Exception):
    def __init__(self, observed):
        self.observed = observed


def _retry_policy(error):
    reason = getattr(error, 'reason', 'unavailable')
    if reason not in _RETRYABLE | _TERMINAL:
        return 'request_rejected', False, 0
    retryable = reason in _RETRYABLE and getattr(error, 'retryable', False) is True
    delay = getattr(error, 'retry_after', 0)
    if type(delay) not in (int, float) or not math.isfinite(delay) or not 0 <= delay <= 30:
        return 'cooldown_required', False, 0
    return reason, retryable, float(delay)


def _backoff(state, config, cooldown=0):
    return max(config['poll_seconds'], cooldown,
               min(300, 2 ** min(state['consecutive_failures'], 8)))


def collect_capture(directory, *, config=None, max_polls=None, stop=None,
                    client=None, clock=None, sleep=None):
    """Run the explicit foreground collector; return one final observation.

    A known provider refusal and an interrupted in-flight request cannot be
    erased by `resume`. Interrupted fetching requires operator investigation.
    Only committed retryable failures have a conservative automatic resume.
    """
    if max_polls is not None:
        _integer(max_polls, 1, 10000)
    if config is not None:
        config = validate_config(config)
    source_clock = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleep or time.sleep
    stopper = stop or (lambda: 0)
    last_time = [_utc(source_clock())]

    def check_stop():
        signum = stopper()
        _integer(signum, 0, 64)
        if signum:
            raise _Stopped(signum)

    def read_clock():
        observed = _utc(source_clock())
        if observed < last_time[0]:
            raise _ClockRegression(observed)
        last_time[0] = observed
        return observed

    def wait(seconds):
        remaining = float(seconds)
        while remaining > 1e-9:
            check_stop()
            chunk = min(.1, remaining)
            sleeper(chunk)
            remaining -= chunk
            read_clock()
        check_stop()

    with capture_writer(directory, config=config, now=last_time[0]) as writer:
        initial = writer.read()
        config = initial['config']
        duration = config['unit_minutes'] * 60000
        initial_state = initial['state']
        resume_refused = (initial_state['phase'] in {'fetching', 'blocked'}
                          or initial_state['last_error'] in _TERMINAL)

        def finish(code, *, stop_state=False):
            at = read_clock()
            snapshot = writer.read()
            if at < parse_end(snapshot['state']['updated_at']):
                raise _ClockRegression(at)
            if stop_state:
                writer.stop(at)
                snapshot = writer.read()
            return dict(exit_code=code, observation=_observation(snapshot, at))

        try:
            check_stop()
            if last_time[0] < parse_end(initial['state']['updated_at']):
                raise _ClockRegression(last_time[0])
            state = initial['state']
            if resume_refused:
                return finish(3)
            if state['last_error'] in _RETRYABLE:
                # Retry-After is not stored in schema1. Thirty seconds is the
                # largest cooldown this client ever accepts; wait conservatively.
                elapsed = (last_time[0] - parse_end(state['updated_at'])).total_seconds()
                wait(max(0, _backoff(state, config, 30) - elapsed))
            if client is None:
                client = UpbitHistoryClient(get=PublicCandleTransport(), sleep=wait, clock=read_clock)
            polls = 0
            while True:
                check_stop()
                at = read_clock()
                snapshot = writer.read()
                boundary = ((_milliseconds(at) - CLOSE_MARGIN_MS) // duration) * duration
                existing = snapshot['fixture']
                count = config['bootstrap_bars']
                if existing is not None:
                    old_end = existing['data_provenance']['end_exclusive_ms']
                    if boundary < old_end:
                        raise _ClockRegression(at)
                    count = (boundary - old_end) // duration + 1
                if count > 200:
                    writer.fail('catchup_limit', at=at)
                    return finish(3)
                if boundary < count * duration:
                    writer.fail('data_rejected', at=at)
                    return finish(3)
                writer.attempt(at)
                polls += 1
                try:
                    try:
                        incoming = client.download(config['market'], unit=config['unit_minutes'],
                            count=count, end=EPOCH + timedelta(milliseconds=boundary))
                    except _Stopped as interrupted:
                        # The history client may be between HTTP attempts or
                        # waiting on a received cooldown. No completed typed
                        # outcome reached us; never erase that uncertainty.
                        interrupted.uncertain_acquisition = True
                        raise
                    check_stop()
                    accepted_at = read_clock()
                    if not isinstance(incoming, dict) or set(incoming) != _FIXTURE_KEYS:
                        raise ValueError('unexpected fixture fields')
                    provenance = validate_market_fixture(incoming)
                    verified = _utc(parse_end(provenance['retrieved_at']))
                    if verified < at or verified > accepted_at:
                        raise _ClockRegression(accepted_at)
                    if (provenance['bar_count'] != count or provenance['end_exclusive_ms'] != boundary
                            or provenance['market'] != config['market']
                            or provenance['unit_minutes'] != config['unit_minutes']):
                        raise ValueError('mismatched acquisition')
                    writer.apply(incoming, at=accepted_at)
                except MarketDataUnavailable as error:
                    failed_at = read_clock()
                    reason, retryable, cooldown = _retry_policy(error)
                    writer.fail(reason, at=failed_at, retryable=retryable)
                    # A received refusal remains binding when stop intent
                    # arrives with the response. Persist it before stopping.
                    check_stop()
                    if not retryable or (max_polls is not None and polls >= max_polls):
                        return finish(3)
                    wait(_backoff(writer.read()['state'], config, cooldown))
                    continue
                except CaptureConflict:
                    writer.fail('history_conflict', at=read_clock())
                    return finish(3)
                except ValueError:
                    writer.fail('data_rejected', at=read_clock())
                    return finish(3)
                if max_polls is not None and polls >= max_polls:
                    return finish(0, stop_state=True)
                wait(config['poll_seconds'])
        except _Stopped as stopped:
            try:
                state = writer.read()['state']
                # A signal is not a reset operation. Preserve a preexisting
                # resume refusal and explicit nonretryability carried by a
                # blocked phase even when its reason is usually transient.
                blocked_policy = state['phase'] == 'blocked' and state['last_error'] in _RETRYABLE
                return finish(128 + stopped.signum,
                    stop_state=not (stopped.uncertain_acquisition or resume_refused or blocked_policy))
            except _ClockRegression as error:
                return dict(exit_code=128 + stopped.signum,
                    observation=_observation(writer.read(), error.observed, clock_error=True))
        except _ClockRegression as error:
            # Never clamp a regressing wall clock into a successful timestamp.
            return dict(exit_code=3,
                observation=_observation(writer.read(), error.observed, clock_error=True))


def export_capture(directory, output, *, count=None):
    """Publish a new bounded original fixture; no collection or account action."""
    directory, output = Path(directory), Path(output)
    try:
        source_binding = _parent_observation(directory)
        output_binding = _parent_observation(output.parent)
    except (OSError, ValueError) as error:
        raise CaptureUnavailable('capture export directory unavailable') from error
    snapshot = read_capture(directory)
    fixture = snapshot['fixture']
    if fixture is None:
        raise CaptureUnavailable('capture has no historical candles')
    available = len(fixture['candles'])
    count = available if count is None else _integer(count, 1, available)
    capture_path = Path(source_binding[0])
    parent = Path(output_binding[0])
    if parent == capture_path or capture_path in parent.parents:
        raise CaptureUnavailable('export must be outside capture')
    parent_stat = output.parent.stat()
    if (not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700 or output.parent.is_symlink()):
        raise CaptureUnavailable('export requires a private owned parent')
    rows = fixture['candles'][-count:]
    provenance = dict(fixture['data_provenance'], bar_count=count, candles_sha256=candles_hash(rows))
    exported = dict(fixture, candles=rows, data_provenance=provenance)
    validate_market_fixture(exported)
    body = (json.dumps(exported, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')
    if len(body) > 4 * 1024 * 1024:
        raise CaptureUnavailable('export exceeds historical input bound')
    def validate_destination(pinned_parent):
        if (pinned_parent != output_binding
                or _parent_observation(directory) != source_binding):
            raise CaptureUnavailable('capture export binding changed')
    publish_new_report(output, body, private_parent=True,
                       validate_destination=validate_destination)
    return dict(schema_version=1, source='upbit_market_capture_export', mode='paper',
        live_enabled=False, read_only=False, operation='export', scope='historical_fixture_export',
        capture_id=snapshot['state']['capture_id'], capture_revision=snapshot['state']['revision'],
        bar_count=count, end_exclusive_ms=provenance['end_exclusive_ms'],
        candles_sha256=provenance['candles_sha256'], fixture_sha256=hashlib.sha256(body).hexdigest())
