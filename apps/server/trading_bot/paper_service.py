"""Foreground supervision of one existing PAPER pair; no detached control API.

The live probe establishes direct process progress only. Native data checks and
full account audit remain separate; neither a PID nor a retained record is live
evidence. A worker is never replaced within an attempt.
"""
import asyncio
from collections import deque
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

from .paper_service_protocol import FrameReader, ProtocolError, decode_document, encode_document
from .paper_service_state import (
    MAX_STDERR_BYTES, ServiceDirectory, ServiceInterrupted, ServiceUnavailable,
    configure_service, prepare_pair,
)

_ROOT = Path(__file__).resolve().parents[3]
_ROLES = ('capture', 'stream')
_CHALLENGE = re.compile(r'[0-9a-f]{32}\Z')
_LABEL = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z')
_REAP_SECONDS = 5
_REQUEST_BYTES = 256
_RESPONSE_BYTES = 8192


def _unavailable():
    return ServiceUnavailable('paper_service_unavailable')


def _check_platform():
    if (sys.platform != 'linux' or not Path('/proc/self/fd').is_dir()
            or threading.current_thread() is not threading.main_thread()):
        raise _unavailable()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            pass
    except OSError:
        raise _unavailable() from None


def _intent(stop):
    if stop is not None and not callable(stop):
        raise _unavailable()
    value = stop() if stop else 0
    if type(value) is not int or value not in (0, 2, 15):
        raise _unavailable()
    return value


def _spawn_worker(role, directory, attempt, parent_pid):
    """Fixed isolated entrypoint; no caller-selected command or inherited lease."""
    return subprocess.Popen([sys.executable, '-I', '-B', '-u',
        str(_ROOT / 'tools/paper_service_worker.py'), '--role', role,
        '--directory', str(directory), '--attempt', attempt,
        '--parent-pid', str(parent_pid)], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
        start_new_session=True)


def _report(owner, attempt, operation, status, code, children=None, responding=False):
    return dict(schema_version=1, source='paper_service', operation=operation,
        mode='paper', live_enabled=False, read_only=operation == 'check',
        service_id=owner.manifest['service_id'],
        attempt_id=attempt.attempt_id if attempt else None,
        attempt_number=attempt.number if attempt else None,
        status=status, exit_code=code, children=children or {},
        supervisor_response=responding,
        boundaries=dict(data_readiness='not_assessed', live_execution=False))


class _Child:
    def __init__(self, role, process, attempt):
        self.role, self.process = role, process
        self.frames = FrameReader(role, attempt)
        self.started = time.monotonic()
        self.last_progress = None
        self.frame_times = deque()
        self.stderr = bytearray()
        self.stderr_bytes = self.stdout_bytes = 0
        self.stderr_truncated = self.protocol_failed = self.forced = False
        self.stdout_eof = self.stderr_eof = False
        self.sent_signal = None

    def public(self, now):
        final = self.frames.result
        native = None if final is None else {key: final[key]
            for key in ('exit_code', 'terminal', 'state', 'reason')}
        code = self.process.poll()
        return dict(pid=self.process.pid, guarded=self.frames.guarded,
            owned=self.frames.owned, progress_age_ms=None if self.last_progress is None
                else max(0, int((now - self.last_progress) * 1000)),
            returncode=code, native_result=native, forced=self.forced,
            reaped=code is not None, stderr_bytes=self.stderr_bytes,
            stderr_truncated=self.stderr_truncated, stdout_bytes=self.stdout_bytes)


class _Supervisor:
    def __init__(self, owner, attempt, listener, stop):
        self.owner, self.attempt, self.listener, self.stop = owner, attempt, listener, stop
        self.policy = owner.manifest['policy']
        self.children, self.clients = {}, {}
        self.selector = selectors.DefaultSelector()
        self.selector.register(listener, selectors.EVENT_READ, ('listener', None))
        self.phase = 'starting'
        self.requested = 0
        self.failure = None
        self.reason = None
        self.last_check = 0.0

    def fail(self, code, reason):
        if self.failure is None or (code == 4 and self.failure != 4):
            self.failure, self.reason = code, reason

    def _poll_intent(self):
        value = _intent(self.stop)
        if value and not self.requested:
            self.requested = value

    def launch(self, role):
        self.owner.check()
        directory = self.owner.manifest['pair']['capture_path' if role == 'capture' else 'account_path']
        process = _spawn_worker(role, directory, self.attempt.attempt_id, os.getpid())
        # Record the live handle before any fallible pipe setup so cleanup owns it.
        child = _Child(role, process, self.attempt.attempt_id)
        self.children[role] = child
        for name in ('stdout', 'stderr'):
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, (name, child))

    def snapshot(self):
        now = time.monotonic()
        children = {role: child.public(now) for role, child in self.children.items()}
        healthy = (self.phase == 'running' and not self.failure and not self.requested
            and set(children) == set(_ROLES) and all(c['guarded'] and c['owned']
                and not c['reaped'] and not c['forced'] and not c['stderr_truncated']
                and c['native_result'] is None
                and c['progress_age_ms'] is not None
                and c['progress_age_ms'] <= self.policy['heartbeat_seconds'] * 1000
                for c in children.values()))
        if healthy:
            status = 'running'
        elif self.failure:
            status = 'degraded'
        elif self.requested or self.phase == 'stopping':
            status = 'stopping'
        else:
            status = 'degraded' if self.phase == 'running' else self.phase
        return _report(self.owner, self.attempt, 'check', status, 0 if healthy else 1,
                       children, responding=True)

    def _close_registered(self, value):
        try:
            self.selector.unregister(value)
        except (KeyError, ValueError):
            pass
        value.close()

    def _pipe(self, name, child):
        pipe = getattr(child.process, name)
        # Pipe setup can fail after spawn. Fallback cleanup must never perform a
        # blocking read before it can signal/reap the already owned process.
        if os.get_blocking(pipe.fileno()):
            os.set_blocking(pipe.fileno(), False)
        try:
            data = os.read(pipe.fileno(), 4096)
        except BlockingIOError:
            return
        if not data:
            setattr(child, name + '_eof', True)
            self._close_registered(pipe)
            if name == 'stdout' and not child.protocol_failed:
                try:
                    child.frames.finish()
                except ProtocolError:
                    child.protocol_failed = True
                    self.fail(4, 'worker_protocol_invalid')
            return
        if name == 'stderr':
            child.stderr_bytes = min(2**63 - 1, child.stderr_bytes + len(data))
            space = MAX_STDERR_BYTES - len(child.stderr)
            child.stderr.extend(data[:space])
            if len(data) > space:
                child.stderr_truncated = True
                self.fail(4, 'worker_stderr_overflow')
            return
        child.stdout_bytes = min(2**63 - 1, child.stdout_bytes + len(data))
        if child.protocol_failed:
            return  # Keep draining after failure without unbounded storage.
        try:
            frames = child.frames.feed(data)
            now = time.monotonic()
            while child.frame_times and child.frame_times[0] <= now - 1:
                child.frame_times.popleft()
            if len(child.frame_times) + len(frames) > 32:
                raise ProtocolError('paper_service_protocol_invalid')
            child.frame_times.extend(now for _ in frames)
            if any(frame['event'] in ('guarded', 'owned', 'progress') for frame in frames):
                child.last_progress = now
        except ProtocolError:
            child.protocol_failed = True
            self.fail(4, 'worker_protocol_invalid')

    def _inspect_children(self):
        now = time.monotonic()
        for child in self.children.values():
            code = child.process.poll()
            final = child.frames.result
            if final is not None:
                native = final['exit_code']
                if native == 4:
                    self.fail(4, 'worker_storage_or_runtime_failed')
                elif final['terminal'] or native in (2, 3):
                    self.fail(3, 'native_refused')
                elif child.sent_signal is None:
                    self.fail(4, 'worker_exited_without_request')
                elif native != 128 + child.sent_signal:
                    self.fail(4, 'worker_stop_unconfirmed')
                if code is not None and code != native:
                    self.fail(4, 'worker_exit_mismatch')
            elif code is not None and child.stdout_eof:
                self.fail(4, 'worker_exit_unconfirmed')
            if self.phase == 'stopping' or code is not None or final is not None:
                continue
            if not child.frames.owned:
                if now - child.started > self.policy['startup_seconds']:
                    self.fail(4, 'worker_startup_timeout')
            elif child.last_progress is None or now - child.last_progress > self.policy['heartbeat_seconds']:
                self.fail(4, 'worker_progress_timeout')

    def _drop_client(self, client):
        self.clients.pop(client, None)
        self._close_registered(client)

    def _accept(self):
        # One accept per selector event keeps a connection flood bounded per loop.
        try:
            client, _ = self.listener.accept()
        except BlockingIOError:
            return
        try:
            client.setblocking(False)
            credentials = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if len(self.clients) >= 4 or struct.unpack('3i', credentials)[1] != os.geteuid():
                client.close()
                return
            self.clients[client] = dict(deadline=time.monotonic() + 1, pending=bytearray(), output=None)
            self.selector.register(client, selectors.EVENT_READ, ('client', client))
        except (OSError, ValueError, struct.error):
            client.close()
            self.clients.pop(client, None)

    def _client(self, client):
        current = self.clients[client]
        try:
            if current['output'] is not None:
                sent = client.send(current['output'])
                if sent <= 0:
                    raise OSError('client_closed')
                current['output'] = current['output'][sent:]
                if not current['output']:
                    self._drop_client(client)
                return
            data = client.recv(_REQUEST_BYTES + 1)
            if not data or len(current['pending']) + len(data) > _REQUEST_BYTES:
                raise ProtocolError('client_request_invalid')
            current['pending'].extend(data)
            if b'\n' not in current['pending']:
                return
            raw = bytes(current['pending'])
            if not raw.endswith(b'\n') or raw.count(b'\n') != 1:
                raise ProtocolError('client_request_invalid')
            request = decode_document(raw[:-1], _REQUEST_BYTES - 1)
            if (type(request) is not dict or set(request) != {'v', 'challenge'}
                    or type(request['v']) is not int or request['v'] != 1
                    or type(request['challenge']) is not str
                    or _CHALLENGE.fullmatch(request['challenge']) is None):
                raise ProtocolError('client_request_invalid')
            response = self.snapshot()
            response['challenge'] = request['challenge']
            current['output'] = encode_document(response, _RESPONSE_BYTES - 1) + b'\n'
            self.selector.modify(client, selectors.EVENT_WRITE, ('client', client))
        except BlockingIOError:
            pass
        except (OSError, ValueError):
            self._drop_client(client)

    def _step(self):
        self._poll_intent()
        now = time.monotonic()
        if now - self.last_check >= .2:
            self.owner.check()
            self.attempt.check()
            self.last_check = now
        for key, _ in self.selector.select(.05):
            kind, value = key.data
            if kind == 'listener':
                self._accept()
            elif kind == 'client':
                if value in self.clients:
                    self._client(value)
            else:
                self._pipe(kind, value)
        for client, state in list(self.clients.items()):
            if time.monotonic() >= state['deadline']:
                self._drop_client(client)
        self._inspect_children()

    def _cleanup_step(self):
        try:
            self._step()
        except BaseException:
            # A changed directory/callback must not prevent draining and reaping.
            self.fail(4, 'supervisor_cleanup_uncertain')
            for child in self.children.values():
                for name in ('stdout', 'stderr'):
                    if not getattr(child, name + '_eof') and not getattr(child.process, name).closed:
                        try:
                            self._pipe(name, child)
                        except (OSError, ValueError):
                            self.fail(4, 'worker_pipe_unconfirmed')
            self._inspect_children()
            time.sleep(.01)

    def _stop_child(self, child):
        process = child.process
        self._cleanup_step()
        if process.poll() is None:
            # A final frame means native work has returned. Give that process its
            # exit grace: another signal can otherwise hit restored handlers and
            # destroy an already observed native refusal during interpreter exit.
            if child.frames.result is None:
                child.sent_signal = self.requested or int(signal.SIGTERM)
                try:
                    process.send_signal(child.sent_signal)
                except BaseException:
                    self.fail(4, 'worker_signal_unconfirmed')
            deadline = time.monotonic() + self.policy['stop_seconds']
            while process.poll() is None and time.monotonic() < deadline:
                self._cleanup_step()
            if process.poll() is None:
                child.forced = True
                self.fail(4, 'worker_forced_stop')
                try:
                    process.kill()
                except BaseException:
                    self.fail(4, 'worker_kill_unconfirmed')
        deadline = time.monotonic() + _REAP_SECONDS
        while (process.poll() is None or not child.stdout_eof or not child.stderr_eof):
            if time.monotonic() >= deadline:
                self.fail(4, 'worker_cleanup_unconfirmed')
                break
            self._cleanup_step()
        self._inspect_children()

    def run(self):
        try:
            for role in _ROLES:
                self._poll_intent()
                if self.requested or self.failure:
                    break
                self.launch(role)
                while not self.children[role].frames.owned and not self.requested and not self.failure:
                    self._step()
            if not self.requested and not self.failure:
                self.phase = 'running'
                while not self.requested and not self.failure:
                    self._step()
        except BaseException:
            self.fail(4, 'supervisor_runtime_failed')
        finally:
            self.phase = 'stopping'
            for role in ('stream', 'capture'):
                if role in self.children:
                    child = self.children[role]
                    try:
                        self._stop_child(child)
                    except BaseException:
                        # Contain an unexpected cleanup exception without losing
                        # the sibling. Only this attempt's owned handle is used.
                        self.fail(4, 'worker_cleanup_unconfirmed')
                        try:
                            if child.process.poll() is None:
                                child.forced = True
                                child.process.kill()
                            child.process.wait(timeout=_REAP_SECONDS)
                        except BaseException:
                            self.fail(4, 'worker_cleanup_unconfirmed')
            for client in list(self.clients):
                try:
                    self._drop_client(client)
                except BaseException:
                    self.fail(4, 'health_cleanup_unconfirmed')
            for child in self.children.values():
                for name in ('stdout', 'stderr'):
                    pipe = getattr(child.process, name)
                    if not pipe.closed:
                        try:
                            self._close_registered(pipe)
                        except BaseException:
                            self.fail(4, 'worker_pipe_unconfirmed')
            try:
                self._close_registered(self.listener)
            except BaseException:
                self.fail(4, 'health_cleanup_unconfirmed')
            finally:
                self.selector.close()
        confirmed = all(c.process.poll() is not None for c in self.children.values())
        if not confirmed:
            self.fail(4, 'worker_cleanup_unconfirmed')
        code = self.failure or (128 + self.requested if self.requested else 4)
        status = 'stopped' if code in (130, 143) else ('refused' if code == 3 else 'uncertain')
        result = _report(self.owner, self.attempt, 'run', status, code,
            {role: child.public(time.monotonic()) for role, child in self.children.items()})
        result.update(cleanup_confirmed=confirmed, reason=self.reason or 'requested_stop')
        try:
            self.attempt.save_result(result, {role: bytes(self.children[role].stderr)
                if role in self.children else b'' for role in _ROLES})
        except BaseException:
            result.update(status='uncertain', exit_code=4, reason='evidence_unconfirmed')
        return result


def run_service(directory, *, stop=None):
    """Run on the persistent main thread until an explicit stop or pair failure."""
    received = _intent(stop)
    if received:
        raise ServiceInterrupted(received)
    with ServiceDirectory(directory, exclusive=True, stop=stop) as owner:
        _check_platform()
        runtime = owner.manifest['runtime']
        if runtime != dict(python=str(Path(sys.executable).absolute()), repository=str(_ROOT),
                implementation=sys.implementation.name, major_minor=list(sys.version_info[:2])):
            raise _unavailable()
        pair = owner.manifest['pair']
        prepared = asyncio.run(prepare_pair(pair['account_path'], expected=pair, stop=stop))
        owner.check()
        received = _intent(stop)
        if received:
            raise ServiceInterrupted(received)
        attempt = owner.new_attempt(prepared['audit'])
        listener = attempt.bind_health_socket()
        return _Supervisor(owner, attempt, listener, stop).run()


def _valid_int(value, low, high):
    return type(value) is int and low <= value <= high


def _health_response(raw, owner, attempt, challenge):
    value = decode_document(raw, _RESPONSE_BYTES - 1)
    expected = _report(owner, attempt, 'check', 'starting', 1, responding=True)
    if type(value) is not dict or set(value) != set(expected) | {'challenge'}:
        raise _unavailable()
    for key in set(expected) - {'status', 'exit_code', 'children'}:
        if encode_document(value[key], _RESPONSE_BYTES) != encode_document(expected[key], _RESPONSE_BYTES):
            raise _unavailable()
    if (value['challenge'] != challenge or value['status'] not in
            ('starting', 'running', 'stopping', 'degraded')
            or type(value['exit_code']) is not int
            or value['exit_code'] != (0 if value['status'] == 'running' else 1)
            or type(value['children']) is not dict or set(value['children']) - set(_ROLES)):
        raise _unavailable()
    keys = {'pid', 'guarded', 'owned', 'progress_age_ms', 'returncode', 'native_result',
            'forced', 'reaped', 'stderr_bytes', 'stderr_truncated', 'stdout_bytes'}
    for child in value['children'].values():
        if (type(child) is not dict or set(child) != keys
                or not _valid_int(child['pid'], 1, 2**31 - 1)
                or any(type(child[key]) is not bool
                       for key in ('guarded', 'owned', 'forced', 'reaped', 'stderr_truncated'))
                or any(not _valid_int(child[key], 0, 2**63 - 1)
                       for key in ('stderr_bytes', 'stdout_bytes'))
                or (child['progress_age_ms'] is not None
                    and not _valid_int(child['progress_age_ms'], 0, 2**63 - 1))
                or (child['returncode'] is not None
                    and not _valid_int(child['returncode'], -128, 255))
                or child['reaped'] != (child['returncode'] is not None)):
            raise _unavailable()
        native = child['native_result']
        if native is not None:
            if (type(native) is not dict or set(native) != {'exit_code', 'terminal', 'state', 'reason'}
                    or type(native['exit_code']) is not int
                    or native['exit_code'] not in (0, 2, 3, 4, 130, 143)
                    or type(native['terminal']) is not bool):
                raise _unavailable()
            for key in ('state', 'reason'):
                if native[key] is not None and (type(native[key]) is not str
                        or len(native[key]) > 64 or _LABEL.fullmatch(native[key]) is None):
                    raise _unavailable()
    if len({c['pid'] for c in value['children'].values()}) != len(value['children']):
        raise _unavailable()
    if value['status'] == 'running' and (set(value['children']) != set(_ROLES)
            or any(not c['guarded'] or not c['owned'] or c['reaped'] or c['forced']
                or c['stderr_truncated'] or c['stderr_bytes'] > MAX_STDERR_BYTES
                or c['native_result'] is not None or c['progress_age_ms'] is None
                or c['progress_age_ms'] > owner.manifest['policy']['heartbeat_seconds'] * 1000
                for c in value['children'].values())):
        raise _unavailable()
    value.pop('challenge')
    return value


def probe_service(directory, *, timeout=1.0):
    """Challenge the current supervisor; no cached liveness, signals or DB reads."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not .1 <= timeout <= 5):
        raise _unavailable()
    try:
        with ServiceDirectory(directory) as owner:
            attempt = owner.latest_attempt()
            if not owner.owner_present():
                return _report(owner, attempt, 'check', 'not_running', 1)
            if attempt is None:
                return _report(owner, None, 'check', 'starting', 1)
            address = attempt.health_address()
            challenge = secrets.token_hex(16)
            deadline = time.monotonic() + timeout
            raw = bytearray()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(max(.001, deadline - time.monotonic()))
                client.connect(address)
                credentials = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                if struct.unpack('3i', credentials)[1] != os.geteuid():
                    raise _unavailable()
                client.settimeout(max(.001, deadline - time.monotonic()))
                client.sendall(encode_document(dict(v=1, challenge=challenge), _REQUEST_BYTES - 1) + b'\n')
                while not raw.endswith(b'\n'):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or len(raw) >= _RESPONSE_BYTES:
                        raise _unavailable()
                    client.settimeout(remaining)
                    chunk = client.recv(min(4096, _RESPONSE_BYTES - len(raw)))
                    if not chunk:
                        raise _unavailable()
                    raw.extend(chunk)
                if raw.count(b'\n') != 1:
                    raise _unavailable()
            report = _health_response(bytes(raw[:-1]), owner, attempt, challenge)
            attempt.check()
            owner.check()
            if not owner.owner_present():
                return _report(owner, attempt, 'check', 'not_running', 1)
            return report
    except (OSError, ValueError, TypeError, struct.error):
        raise _unavailable() from None
