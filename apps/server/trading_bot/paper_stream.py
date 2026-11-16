"""Continuous closed-candle PAPER consumption with explicit verified recovery.

No transport, credentials or broker adapter is selected here. The source is a
separately operated local capture. Fills are delayed next-open simulations and
all account effects remain detached until the complete bar transaction commits.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time

from trading_data.upbit_history import _milliseconds, parse_end
from .market_capture import _observation as capture_observation
from .market_capture_store import CaptureUnavailable, read_capture
from .paper_runner import PaperRunConfig
from .paper_stream_engine import advance_state, initial_state
from .paper_stream_store import (StreamCapacity, StreamStorageError, StreamUnavailable,
                                read_stream, stream_reader, stream_writer)

_CONFIG_OPTIONS = {'strategy', 'initial_balance', 'position_size_pct', 'fee_pct',
    'slippage_pct', 'max_daily_loss_pct', 'max_drawdown_pct', 'fast_period',
    'slow_period', 'rsi_period'}
_TERMINAL_SOURCE = {'rate_blocked', 'request_rejected', 'cooldown_required',
    'data_rejected', 'history_conflict', 'catchup_limit', 'clock_regression'}
_TERMINAL_STREAM = {'source_identity_changed', 'source_history_conflict',
    'source_regression', 'retention_lost', 'source_blocked', 'clock_regression',
    'capacity', 'engine_rejected'}
_ZERO_HASH = '0' * 64


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _utc(value):
    _milliseconds(value)
    return value.astimezone(timezone.utc)


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise StreamUnavailable('continuous_paper_invalid_option')
    return value


def _engine_fingerprint():
    names = ('trading_bot.paper_runner', 'trading_bot.paper_checkpoint',
        'trading_bot.durable_paper_run', 'trading_bot.durable_paper_resume',
        'trading_core.exchange', 'trading_core.paper_exchange',
        'trading_core.order_manager', 'trading_core.order_reconciliation',
        'trading_core.order_state_store', 'trading_core.paper_session',
        'trading_core.paper_admission', 'trading_core.paper_control',
        'trading_data.candle', 'trading_data.indicator', 'trading_data._validation',
        'trading_data.upbit_history', 'trading_strategy.base',
        'trading_strategy.ma_strategy', 'trading_strategy.rsi_strategy',
        'trading_bot.paper_stream_engine', 'trading_bot.paper_stream_store',
        'trading_bot.paper_stream', 'trading_bot.market_capture_store',
        'trading_bot.market_capture')
    sources = {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
               for name in names}
    return _digest(dict(sources=sources, python=list(sys.version_info[:2]),
                        implementation=sys.implementation.name))


class _Blocked(Exception):
    def __init__(self, reason):
        self.reason = reason


class _Stopped(Exception):
    def __init__(self, signum):
        self.signum = signum


class _ClockRegression(Exception):
    def __init__(self, observed):
        self.observed = observed


def _stop_requested(stop):
    signum = stop() if stop is not None else 0
    if type(signum) is not int or signum not in (0, 2, 15):
        raise StreamUnavailable('continuous_paper_invalid_stop')
    if signum:
        raise _Stopped(signum)


class _Clock:
    def __init__(self, clock, floor=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.last = floor

    def now(self):
        try:
            value = _utc(self.clock())
        except (ValueError, TypeError, OverflowError, OSError):
            raise _ClockRegression(None) from None
        if self.last is not None and value < self.last:
            raise _ClockRegression(value)
        self.last = value
        return value


def _physical(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]


class _SourceBinding:
    """Hold source names across polls; read_capture validates the actual bytes.

Only identity/ownership is checked here so a temporary journal remains an
unavailable observation, rather than being confused with capture replacement.
"""
    def __init__(self, directory, expected=None):
        self.path = Path(directory).absolute()
        self.fds = {}
        self.identity = None
        self.pid = os.getpid()
        try:
            if self.path.name in ('', '.', '..'):
                raise ValueError('source name')
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            self.fds['parent'] = os.open(self.path.parent, flags | os.O_DIRECTORY)
            self.fds['directory'] = os.open(self.path.name, flags | os.O_DIRECTORY,
                                             dir_fd=self.fds['parent'])
            for key, name in (('database', 'capture.sqlite3'), ('lock', '.capture.lock')):
                self.fds[key] = os.open(name, flags, dir_fd=self.fds['directory'])
            self.identity = {name: _physical(os.fstat(fd)) for name, fd in self.fds.items()}
            self.identity['parent_path'] = str(self.path.parent.resolve(strict=True))
            self.check()
            if expected is not None and self.identity != expected:
                raise ValueError('source replaced')
        except (OSError, ValueError):
            self.close()
            raise _Blocked('source_identity_changed') from None

    def check(self):
        try:
            if os.getpid() != self.pid or self.identity is None:
                raise ValueError('source owner')
            named = {'parent': self.path.parent.lstat(), 'directory': self.path.lstat(),
                'database': os.stat('capture.sqlite3', dir_fd=self.fds['directory'], follow_symlinks=False),
                'lock': os.stat('.capture.lock', dir_fd=self.fds['directory'], follow_symlinks=False)}
            if str(self.path.parent.resolve(strict=True)) != self.identity['parent_path']:
                raise ValueError('parent replaced')
            for key, fd in self.fds.items():
                held = os.fstat(fd)
                if _physical(held) != self.identity[key] or _physical(named[key]) != self.identity[key]:
                    raise ValueError('source replaced')
                is_dir = key in ('parent', 'directory')
                if not (stat.S_ISDIR(held.st_mode) if is_dir else stat.S_ISREG(held.st_mode)):
                    raise ValueError('source type')
                if key != 'parent' and (held.st_uid != os.geteuid()
                    or stat.S_IMODE(held.st_mode) != (0o700 if is_dir else 0o600)
                    or (not is_dir and held.st_nlink != 1)):
                    raise ValueError('source privacy')
                if key == 'lock' and held.st_size != 0:
                    raise ValueError('source lock')
        except (OSError, ValueError):
            raise _Blocked('source_identity_changed') from None

    def read(self):
        self.check()
        result = read_capture(self.path)
        self.check()
        return result

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


def _source_marker(snapshot):
    state, fixture = snapshot['state'], snapshot['fixture']
    return dict(revision=state['revision'], accepted_bars_total=state['accepted_bars_total'],
        evicted_bars_total=state['evicted_bars_total'], last_verified_at=state['last_verified_at'],
        latest_open_ms=fixture['candles'][-1][0] if fixture else None,
        snapshot_sha256=_digest(snapshot))


def _separate_destination(directory, source):
    target = Path(directory).absolute()
    parent = target.parent.resolve(strict=True)
    destination = parent / target.name
    capture = source.path.resolve(strict=True)
    if (destination == capture or capture in destination.parents
            or destination in capture.parents
            or _physical(parent.stat()) == source.identity['directory']):
        raise StreamUnavailable('continuous_paper_source_destination_overlap')


def _progress(current, previous, duration):
    if current['revision'] < previous['revision']:
        raise _Blocked('source_regression')
    if current['revision'] == previous['revision'] and current != previous:
        raise _Blocked('source_history_conflict')
    for key in ('accepted_bars_total', 'evicted_bars_total', 'latest_open_ms'):
        if current[key] is None or current[key] < previous[key]:
            raise _Blocked('source_regression')
    if (current['last_verified_at'] is None
            or parse_end(current['last_verified_at']) < parse_end(previous['last_verified_at'])
            or current['latest_open_ms'] - previous['latest_open_ms']
                != (current['accepted_bars_total'] - previous['accepted_bars_total']) * duration):
        raise _Blocked('source_regression')


def _admit(snapshot, saved, source, now, writer):
    manifest, engine = saved['manifest'], saved['engine']
    if (snapshot['state']['capture_id'] != manifest['capture']['capture_id']
            or snapshot['config'] != manifest['capture']['config']):
        raise _Blocked('source_identity_changed')
    marker = _source_marker(snapshot)
    duration = snapshot['config']['unit_minutes'] * 60000
    _progress(marker, saved['control']['source'], duration)
    if (snapshot['state']['phase'] == 'blocked'
            or snapshot['state']['last_error'] in _TERMINAL_SOURCE):
        raise _Blocked('source_blocked')
    observation = capture_observation(snapshot, now)
    if observation['status'] == 'clock_error':
        raise _Blocked('clock_regression')
    rows = snapshot['fixture']['candles']
    cursor = engine['last_open_ms']
    if rows[-1][0] < cursor:
        raise _Blocked('source_regression')
    if rows[0][0] > cursor:
        raise _Blocked('retention_lost')
    first = max(rows[0][0], manifest['bootstrap'][0][0])
    observed = {bar[0]: bar for bar in rows if first <= bar[0] <= cursor}
    archived = {bar[0]: bar for bar in writer.history(first, cursor)}
    if observed != archived or cursor not in observed:
        raise _Blocked('source_history_conflict')
    source.check()
    return marker, observation['status'] == 'current'


def _observe(snapshot, now, *, clock_error=False):
    now = _utc(now)
    manifest, control, engine = snapshot['manifest'], snapshot['control'], snapshot['engine']
    source = control['source']
    duration = manifest['capture']['config']['unit_minutes'] * 60000
    limits = dict(max_consumer_age_ms=max(30000, 2 * manifest['poll_seconds'] * 1000),
        max_source_data_age_ms=duration + 5000 + manifest['capture']['config']['poll_seconds'] * 1000 + 10000,
        max_source_observation_age_ms=max(30000, 2 * manifest['capture']['config']['poll_seconds'] * 1000))
    clock_valid = not clock_error
    times = [parse_end(manifest['created_at']), parse_end(control['updated_at']),
             parse_end(source['last_verified_at'])]
    if any(value > now for value in times):
        clock_valid = False
    ages = dict(consumer_age_ms=_milliseconds(now) - _milliseconds(times[1]),
        source_data_age_ms=_milliseconds(now) - source['latest_open_ms'] - duration,
        source_observation_age_ms=_milliseconds(now) - _milliseconds(times[2]))
    if any(age < 0 for age in ages.values()):
        clock_valid = False
    if not clock_valid:
        ages = {key: None for key in ages}
    phase = control['phase']
    if not clock_valid or control['last_error'] == 'clock_regression':
        status = 'clock_error'
    elif phase != 'running':
        status = phase
    elif any(ages[key] > limits['max_' + key] for key in ages):
        status = 'stale'
    else:
        status = 'current' if engine['last_open_ms'] == source['latest_open_ms'] else 'catching_up'
    return dict(schema_version=1, source='continuous_paper_stream', mode='paper',
        read_only=True, live_enabled=False, scope='delayed_next_open_simulation',
        observed_at=now.isoformat(), status=status, stream_id=manifest['stream_id'],
        configuration=deepcopy(manifest['config']), control=deepcopy(control),
        cursor=dict(bars_processed=engine['next_index'], bootstrap_bars=engine['bootstrap_count'],
                    last_open_ms=engine['last_open_ms'], next_open_ms=engine['last_open_ms'] + duration),
        account=dict(currency='KRW', symbol=engine['config']['symbol'],
            **{key: engine[key] for key in ('cash', 'holding', 'equity', 'total_fees', 'fill_count')}),
        risk=dict(day=engine['risk_day'], **{key: engine[key] for key in
            ('daily_start_equity', 'peak_equity', 'daily_loss_halted', 'drawdown_halted')}),
        pending_signal=deepcopy(engine['pending_signal']),
        freshness=dict(clock_valid=clock_valid, **ages, **limits),
        integrity=dict(verification='structural_head_only', engine_sha256=snapshot['engine_sha256'],
                       event_head_sha256=snapshot['event_head_sha256']),
        boundaries=dict(execution_model='delayed_next_open', live_orders=False,
            provider_authenticity='not_authenticated', process_liveness='not_asserted',
            bootstrap='indicators_only_no_signals', audit='stopped_owner_full_replay',
            capacity_bars=manifest['max_bars']))


def stream_status(directory, *, now=None):
    """Passive bounded structural observation, never a full financial audit."""
    return _observe(read_stream(directory), _utc(now if now is not None else datetime.now(timezone.utc)))


async def _verify(owner, snapshot, stop):
    manifest = snapshot['manifest']
    if _engine_fingerprint() != manifest['engine_sha256']:
        raise StreamUnavailable('continuous_paper_engine_changed')
    _stop_requested(stop)
    engine = initial_state(PaperRunConfig(**manifest['config']), manifest['bootstrap'])
    previous = _ZERO_HASH
    previous_source = manifest['activation_source']
    previous_at = parse_end(manifest['created_at'])
    count = 0
    duration = manifest['capture']['config']['unit_minutes'] * 60000
    for event in owner.events():
        _stop_requested(stop)
        count += 1
        if (count > manifest['max_bars'] or event['sequence'] != count
                or event['previous_sha256'] != previous
                or event['sha256'] != _digest({key: value for key, value in event.items() if key != 'sha256'})):
            raise StreamUnavailable('continuous_paper_audit_mismatch')
        at = parse_end(event['applied_at'])
        try:
            _progress(event['source'], previous_source, duration)
        except _Blocked:
            raise StreamUnavailable('continuous_paper_audit_mismatch') from None
        absolute_cursor = manifest['activation_source']['accepted_bars_total'] + count - 1
        if (at < previous_at or parse_end(event['source']['last_verified_at']) > at
                or event['source']['latest_open_ms'] < event['bar'][0]
                or event['source']['evicted_bars_total'] > absolute_cursor - 1
                or event['source']['accepted_bars_total'] <= absolute_cursor
                or event['bar'][0] + duration > _milliseconds(at) - 5000):
            raise StreamUnavailable('continuous_paper_audit_mismatch')
        engine, fill = await advance_state(engine, event['bar'])
        if fill != event['fill'] or _digest(engine) != event['engine_sha256']:
            raise StreamUnavailable('continuous_paper_audit_mismatch')
        previous, previous_source, previous_at = event['sha256'], event['source'], at
        await asyncio.sleep(0)
    try:
        _progress(snapshot['control']['source'], previous_source, duration)
    except _Blocked:
        raise StreamUnavailable('continuous_paper_audit_mismatch') from None
    if (count != snapshot['engine']['next_index'] or engine != snapshot['engine']
            or _digest(engine) != snapshot['engine_sha256']
            or previous != snapshot['event_head_sha256']
            or previous_at > parse_end(snapshot['control']['updated_at'])
            or _engine_fingerprint() != manifest['engine_sha256']):
        raise StreamUnavailable('continuous_paper_audit_mismatch')
    _stop_requested(stop)
    return dict(schema_version=1, source='continuous_paper_audit', mode='paper',
        live_enabled=False, read_only=True, status='verified_prefix', exit_code=0,
        stream_id=manifest['stream_id'], events_verified=count,
        engine_sha256=snapshot['engine_sha256'], event_head_sha256=previous,
        boundaries=dict(provider_authenticity='not_authenticated', live_orders=False,
                        verification='full_archived_replay'))


async def audit_stream(directory, *, stop=None):
    """Verify a stopped owner's archived prefix without repairing or writing it."""
    try:
        with stream_reader(directory, audit=True) as owner:
            return await _verify(owner, owner.read(), stop)
    except _Stopped as error:
        return dict(schema_version=1, source='continuous_paper_audit', mode='paper',
            live_enabled=False, read_only=True, status='interrupted', exit_code=128 + error.signum)


def _operation(operation, snapshot, at, code, *, clock_error=False):
    return dict(schema_version=1, source='continuous_paper_operation', operation=operation,
        mode='paper', live_enabled=False, read_only=False, exit_code=code,
        observation=_observe(snapshot, at, clock_error=clock_error)
            if snapshot is not None and at is not None else None)


async def _wait(seconds, sleep, stop):
    remaining = seconds
    deadline = time.monotonic() + seconds
    while remaining > 0:
        _stop_requested(stop)
        step = min(0.1, remaining)
        sleep(step)
        remaining = min(remaining - step, deadline - time.monotonic())
        await asyncio.sleep(0)
    _stop_requested(stop)


async def run_stream(directory, *, capture=None, config_options=None, poll_seconds=5,
                     max_polls=None, stop=None, clock=None, sleep=None):
    """Create a new indicator-warmed account, or explicitly verify and resume one.

The default loop stays in the foreground. A bounded invocation is also useful
for supervised steps; it never infers that a stopped capture is current.
"""
    _integer(poll_seconds, 5, 300)
    if max_polls is not None:
        _integer(max_polls, 1, 10000)
    if capture is None and (config_options is not None or poll_seconds != 5):
        raise StreamUnavailable('continuous_paper_resume_options')
    if config_options is not None and (not isinstance(config_options, dict)
                                      or set(config_options) - _CONFIG_OPTIONS):
        raise StreamUnavailable('continuous_paper_invalid_option')
    operation = 'run' if capture is not None else 'resume'
    wall = _Clock(clock)
    source, saved, manifest, engine, marker = None, None, None, None, None
    sleeper = sleep or time.sleep
    try:
        at = wall.now()
        _stop_requested(stop)
        if capture is not None:
            try:
                source = _SourceBinding(capture)
                observed = source.read()
            except (CaptureUnavailable, _Blocked):
                raise StreamUnavailable('continuous_paper_source_unavailable') from None
            _separate_destination(directory, source)
            at = wall.now()
            if capture_observation(observed, at)['status'] != 'current':
                raise StreamUnavailable('continuous_paper_source_not_current')
            config = PaperRunConfig(symbol=observed['fixture']['symbol'],
                timeframe=observed['fixture']['timeframe'],
                **dict({'initial_balance': '1000000'}, **(config_options or {})))
            bootstrap = deepcopy(observed['fixture']['candles'][-500:])
            engine = initial_state(config, bootstrap)
            marker = _source_marker(observed)
            manifest = dict(format='uulab.continuous-paper', version=1, mode='paper',
                live_enabled=False, execution_model='delayed_next_open',
                stream_id=secrets.token_hex(16), created_at=at.isoformat(),
                config=config.identity(), activation_source=marker, bootstrap=bootstrap,
                capture=dict(path=str(source.path), binding=source.identity,
                    capture_id=observed['state']['capture_id'], config=observed['config']),
                engine_sha256=_engine_fingerprint(), poll_seconds=poll_seconds, max_bars=100000)
            source.check()
            # The next commit creates only an unfunded-by-trades bootstrap. A
            # signalled or newly stale initialization must not create a directory.
            at = wall.now()
            if capture_observation(observed, at)['status'] != 'current':
                raise StreamUnavailable('continuous_paper_source_not_current')
            _stop_requested(stop)
            manifest['created_at'] = at.isoformat()
        creation = dict(manifest=manifest, engine=engine, at=at, source=marker) if manifest else {}
        with stream_writer(directory, **creation) as writer:
            saved = writer.read()
            def checked_time(reason='clock_regression'):
                nonlocal saved
                try:
                    return wall.now()
                except _ClockRegression:
                    # We still own the lock here. Preserve refusal before closing
                    # the owner, without fabricating a newer successful timestamp.
                    saved = writer.latch(reason)
                    raise
            wall.last = max(wall.last, parse_end(saved['control']['updated_at']))
            at = checked_time()
            if (saved['control']['phase'] == 'blocked'
                    or saved['control']['last_error'] in _TERMINAL_STREAM):
                try:
                    _stop_requested(stop)
                except _Stopped as error:
                    return _operation(operation, saved, at, 128 + error.signum)
                return _operation(operation, saved, at, 3)
            if capture is None:
                await _verify(writer, saved, stop)
                writer.verify_head(saved['engine_sha256'], saved['event_head_sha256'])
                try:
                    source = _SourceBinding(saved['manifest']['capture']['path'],
                                            saved['manifest']['capture']['binding'])
                except _Blocked as error:
                    at = checked_time(error.reason)
                    saved = writer.control('blocked', error.reason, at=at)
                    try:
                        _stop_requested(stop)
                    except _Stopped as stopped:
                        return _operation(operation, saved, at, 128 + stopped.signum)
                    return _operation(operation, saved, at, 3)
            polls = 0
            try:
                while True:
                    _stop_requested(stop)
                    at = checked_time()
                    if _engine_fingerprint() != saved['manifest']['engine_sha256']:
                        raise _Blocked('engine_rejected')
                    polls += 1
                    waiting = None
                    try:
                        observed = source.read()
                        at = checked_time()
                        marker, current = _admit(observed, saved, source, at, writer)
                    except CaptureUnavailable:
                        waiting, marker = 'source_unavailable', None
                    else:
                        if not current:
                            waiting = 'source_not_current'
                    if waiting is None:
                        saved = writer.control('running', None, at=at, source=marker)
                        rows = [bar for bar in observed['fixture']['candles']
                                if bar[0] > saved['engine']['last_open_ms']]
                        for bar in rows:
                            _stop_requested(stop)
                            if saved['engine']['next_index'] >= saved['manifest']['max_bars']:
                                raise _Blocked('capacity')
                            try:
                                candidate, fill = await advance_state(saved['engine'], bar)
                            except (ValueError, ArithmeticError, TypeError, KeyError):
                                raise _Blocked('engine_rejected') from None
                            # A new source read authorizes this prepared candidate.
                            # Never commit it merely because an earlier read worked.
                            try:
                                latest = source.read()
                                at = checked_time()
                                latest_marker, current = _admit(latest, saved, source, at, writer)
                            except CaptureUnavailable:
                                waiting = 'source_unavailable'
                                break
                            if not current:
                                waiting, marker = 'source_not_current', latest_marker
                                break
                            if not any(item == bar for item in latest['fixture']['candles']):
                                raise _Blocked('source_history_conflict')
                            source.check()
                            _stop_requested(stop)
                            at = checked_time()
                            if capture_observation(latest, at)['status'] != 'current':
                                waiting, marker = 'source_not_current', latest_marker
                                break
                            try:
                                saved = writer.commit_bar(candidate, bar, fill, at=at, source=latest_marker)
                            except StreamCapacity:
                                raise _Blocked('capacity') from None
                            marker = latest_marker
                            await asyncio.sleep(0)
                    if waiting is not None:
                        at = checked_time()
                        saved = writer.control('waiting', waiting, at=at, source=marker)
                    if max_polls is not None and polls >= max_polls:
                        at = checked_time()
                        _stop_requested(stop)
                        if waiting is None:
                            saved = writer.control('stopped', None, at=at)
                        return _operation(operation, saved, at, 0 if waiting is None else 3)
                    await _wait(saved['manifest']['poll_seconds'], sleeper, stop)
            except _Blocked as error:
                at = checked_time(error.reason)
                saved = writer.control('blocked', error.reason, at=at)
                try:
                    _stop_requested(stop)
                except _Stopped as stopped:
                    return _operation(operation, saved, at, 128 + stopped.signum)
                return _operation(operation, saved, at, 3)
            except _Stopped as error:
                at = checked_time()
                # Preserve waiting reasons; stop never overrides terminal state.
                if saved['control']['phase'] == 'running':
                    saved = writer.control('stopped', None, at=at)
                return _operation(operation, saved, at, 128 + error.signum)
    except _ClockRegression as error:
        # The held owner latched refusal with its last trusted stamp. Report the
        # actually observed regressed clock; never claim a fresh successful time.
        code = 3
        try:
            _stop_requested(stop)
        except _Stopped as stopped:
            code = 128 + stopped.signum
        return _operation(operation, saved, error.observed, code, clock_error=True)
    except _Blocked:
        # A new account has no durable control record to mark before creation.
        raise StreamUnavailable('continuous_paper_source_unavailable') from None
    except _Stopped as error:
        return _operation(operation, saved, wall.last, 128 + error.signum)
    finally:
        if source is not None:
            source.close()
