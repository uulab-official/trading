"""Test-only subprocess fixtures; no synthetic mode is exposed by service CLIs."""
import asyncio
import errno
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))

BASE = datetime(2026, 11, 14, tzinfo=timezone.utc)
START = int(BASE.timestamp()) * 1000
CONFIG = dict(market='KRW-BTC', unit_minutes=1, bootstrap_bars=50,
              retention_bars=200, poll_seconds=5)
OPTIONS = dict(initial_balance='1000', fee_pct='0', slippage_pct='0',
               position_size_pct='100', max_daily_loss_pct='100', max_drawdown_pct='100')


def has_unix_sockets():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            return True
    except OSError as error:
        if (error.errno not in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT)
                or os.environ.get('PAPER_SERVICE_REQUIRE_SOCKET') == '1'):
            raise
        return False


class PipeListener:
    """Test-only inert selector FD; establishes NO actual socket coverage."""
    def __init__(self):
        self.reader, self.writer = os.pipe()

    def fileno(self):
        return self.reader

    def accept(self):
        raise BlockingIOError()

    def close(self):
        if self.reader is not None:
            os.close(self.reader)
            os.close(self.writer)
            self.reader = self.writer = None


@contextmanager
def network_forbidden():
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden in PAPER service fixture')

    with ExitStack() as stack:
        for name in ('getaddrinfo', 'create_connection'):
            stack.enter_context(patch.object(socket, name, forbidden))
        for name in ('connect', 'connect_ex', 'sendto'):
            original = getattr(socket.socket, name)

            def guarded(sock, *args, _original=original, **kwargs):
                if sock.family in (socket.AF_INET, socket.AF_INET6):
                    forbidden()
                return _original(sock, *args, **kwargs)

            stack.enter_context(patch.object(socket.socket, name, guarded))
        yield


def fixture(first, count, now):
    from trading_data.upbit_history import candles_hash
    rows = []
    for index in range(first, first + count):
        if index < 50:
            values = ('100', '101', '99', '100')
        elif index == 50:
            values = ('105', '115', '100', '110')
        else:
            price = str((120, 90, 80, 110, 125, 105)[(index - 51) % 6])
            values = (price, str(int(price) + 5), str(int(price) - 5), price)
        rows.append([START + index * 60000, *values, '1'])
    return dict(mode='paper', symbol='BTC/KRW', timeframe='1m', candles=rows,
        data_provenance=dict(schema_version=1, provider='upbit_public_rest', market='KRW-BTC',
            unit_minutes=1, end_exclusive_ms=rows[-1][0] + 60000,
            retrieved_at=now.isoformat(), bar_count=count,
            candles_sha256=candles_hash(rows), gap_policy='reject',
            missing_intervals=0, authenticated=False))


async def create_pair(root):
    from trading_bot.market_capture_store import capture_writer
    from trading_bot.paper_stream import run_stream
    capture, account = Path(root) / 'capture', Path(root) / 'account'
    now = BASE + timedelta(minutes=50, seconds=6)
    with capture_writer(capture, config=CONFIG, now=now) as owner:
        owner.apply(fixture(0, 50, now), at=now)
    first = await run_stream(account, capture=capture, config_options=OPTIONS,
                             max_polls=1, clock=lambda: now)
    assert first['exit_code'] == 0
    now = BASE + timedelta(minutes=52, seconds=6)
    with capture_writer(capture) as owner:
        owner.apply(fixture(49, 3, now), at=now)
    resumed = await run_stream(account, max_polls=1, clock=lambda: now)
    assert resumed['exit_code'] == 0
    with capture_writer(capture) as owner:
        owner.stop(now)
    return account, capture


def record(events, **value):
    with Path(events).open('a', encoding='utf-8') as output:
        output.write(json.dumps(value, sort_keys=True) + '\n')


def native_result(role, directory, code, reason=None):
    if role == 'capture':
        from trading_bot.market_capture import capture_status
        observation = capture_status(directory)
        observation['capture'].update(phase='stopped', last_error=reason)
        observation['status'] = 'stopped'
        return dict(exit_code=code, observation=observation)
    from trading_bot.paper_stream import stream_status
    observation = stream_status(directory)
    observation['control'].update(phase='stopped', last_error=reason)
    observation['status'] = 'stopped'
    return dict(schema_version=1, source='continuous_paper_operation', operation='resume',
                mode='paper', live_enabled=False, read_only=False,
                exit_code=code, observation=observation)


def fake_native(role, directory, stop, sleep, mode, events):
    record(events, event='native_entry', role=role, pid=os.getpid())
    if mode == 'unowned' and role == 'capture':
        while not (signum := stop()):
            time.sleep(.05)
        return native_result(role, directory, 128 + signum)
    sleep(.01)
    if mode == 'ignore_capture' and role == 'capture':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode == 'exit_zero' and role == 'capture':
        return native_result(role, directory, 0)
    if mode == 'exit_refusal' and role == 'stream':
        return native_result(role, directory, 3, 'source_history_conflict')
    if mode == 'self_term' and role == 'stream':
        os.kill(os.getpid(), signal.SIGTERM)
    while True:
        signum = stop()
        if signum:
            record(events, event='stopped', role=role, signal=signum, pid=os.getpid())
            if mode == 'storage_on_stop' and role == 'capture':
                raise OSError('SECRET-storage-failure')
            reason = 'clock_regression' if mode == 'terminal_on_stop' and role == 'capture' else None
            return native_result(role, directory, 128 + signum, reason)
        if mode == 'stderr_flood' and role == 'capture':
            os.write(2, b'SECRET-diagnostic\n' * 1024)
        elif mode == 'stdout_flood' and role == 'capture':
            os.write(1, b'x' * 4096)
        elif mode == 'malformed' and role == 'stream':
            os.write(1, b'{"not":"a worker frame"}\n')
        sleep(.01 if 'flood' in mode else .05)


def real_native(role, directory, stop, sleep, mode, events):
    now = BASE + timedelta(minutes=54 if mode == 'native1' else 56, seconds=6)
    record(events, event='native_entry', role=role, pid=os.getpid())
    if role == 'capture':
        from trading_bot.market_capture import collect_capture

        class SyntheticClient:
            def download(self, market, *, unit, count, end):
                record(events, event='synthetic_download', role=role)
                last = (int(end.timestamp()) * 1000 - START) // 60000
                return fixture(last - count, count, now)

        result = collect_capture(directory, stop=stop, sleep=sleep,
                                 client=SyntheticClient(), clock=lambda: now)
    else:
        from trading_bot.paper_stream import run_stream
        result = asyncio.run(run_stream(directory, stop=stop, sleep=sleep, clock=lambda: now))
    record(events, event='stopped', role=role, signal=result['exit_code'] - 128,
           pid=os.getpid())
    return result


def spawn_fixture(role, directory, attempt, parent_pid, mode, events):
    process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(Path(__file__)),
        'worker', role, str(directory), attempt, str(parent_pid), mode, str(events)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, close_fds=True)
    record(events, event='spawn', role=role, pid=process.pid)
    return process


def service_process(directory, mode, events, headless=False):
    from trading_bot import paper_service as service
    from trading_bot.paper_service_state import (
        ServiceUnavailable, ServiceRefused, ServiceInterrupted,
    )
    received, previous = [], {}

    def signal_intent(signum, frame):
        if not received:
            received.append(int(signum))

    service._spawn_worker = lambda role, directory, attempt, parent_pid: spawn_fixture(
        role, directory, attempt, parent_pid, mode, events)
    if headless:
        # Exercise the actual coordinator and real processes without claiming a
        # health socket exists on executors that prohibit socket creation.
        from trading_bot.paper_service_state import Attempt
        service._check_platform = lambda: None
        Attempt.bind_health_socket = lambda self: PipeListener()
        original_step = service._Supervisor._step

        def observe_step(self):
            original_step(self)
            record(events, event='test_monitor_observation', value=self.snapshot())

        service._Supervisor._step = observe_step
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, signal_intent)
        result = service.run_service(directory, stop=lambda: received[0] if received else 0)
        code = result['exit_code']
    except ServiceRefused:
        code, result = 3, dict(error='fixture_service_refused')
    except ServiceUnavailable:
        code, result = 2, dict(error='fixture_service_unavailable')
    except ServiceInterrupted as error:
        code, result = 128 + error.signum, dict(error='fixture_service_interrupted')
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        code, result = 4, dict(error='fixture_service_failed')
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return code


def main():
    with network_forbidden():
        if sys.argv[1] == 'worker':
            role, directory, attempt, parent, mode, events = sys.argv[2:]
            from trading_bot import paper_service_worker as worker
            native = real_native if mode.startswith('native') else fake_native
            worker._run_native = lambda role, directory, stop, sleep: native(
                role, directory, stop, sleep, mode, events)
            return worker.worker_main(role, directory, attempt, int(parent))
        if sys.argv[1] == 'service':
            return service_process(*sys.argv[2:])
        if sys.argv[1] == 'service_without_socket':
            return service_process(*sys.argv[2:], headless=True)
        raise AssertionError('unknown fixture command')


if __name__ == '__main__':
    raise SystemExit(main())
