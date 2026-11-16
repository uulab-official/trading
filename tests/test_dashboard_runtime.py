"""Real loopback connection and subprocess lifecycle guarantees."""
from contextlib import contextmanager, ExitStack, redirect_stdout
import http.client
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "apps/dashboard/server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("dashboard_runtime_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def running_server(module=None, **options):
    module = module or load_server()
    server = module.create_server(port=0, **options)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        if thread.is_alive():
            raise AssertionError("dashboard accept loop did not stop")


def request(server, route="/", *, method="GET", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request(method, route, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded condition did not become true")


class DashboardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_server()

    def bounded_server(self, **options):
        self.assertIn("socket_timeout", inspect.signature(self.module.create_server).parameters,
                      "bounded server options are missing")
        return running_server(self.module, **options)

    def test_default_capacity_refuses_ninth_idle_connection_then_recovers(self):
        with running_server(self.module) as server, ExitStack() as cleanup:
            clients = []
            for _ in range(8):
                client = cleanup.enter_context(socket.create_connection(server.server_address, timeout=1))
                client.sendall(b"GET / HTTP/1.1\r\n")
                clients.append(client)
            # The ninth request is queued after the eight incomplete requests.
            ninth = cleanup.enter_context(socket.create_connection(server.server_address, timeout=1))
            ninth.sendall(f"GET / HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n\r\n".encode())
            ninth.settimeout(.8)
            try:
                data = ninth.recv(1024)
            except ConnectionResetError:
                data = b""
            self.assertEqual(data, b"", "capacity overflow created another request worker")
            for client in clients:
                client.close()
            # A completed ordinary request proves worker slots were released.
            wait_for(lambda: self.available(server))
            self.assertEqual(request(server)[0], 200)

    @staticmethod
    def available(server):
        try:
            return request(server)[0] == 200
        except (OSError, http.client.HTTPException):
            return False

    def test_idle_socket_deadline_releases_capacity(self):
        with self.bounded_server(max_connections=1, socket_timeout=.08, connection_timeout=.4) as server:
            with socket.create_connection(server.server_address, timeout=1) as idle:
                idle.settimeout(1)
                self.assertEqual(idle.recv(1), b"")
            self.assertEqual(request(server)[0], 200)

    def test_trickled_headers_cannot_extend_absolute_deadline(self):
        with self.bounded_server(max_connections=1, socket_timeout=.15, connection_timeout=.3) as server:
            with socket.create_connection(server.server_address, timeout=1) as client:
                client.settimeout(.05)
                client.sendall(b"GET / HTTP/1.1\r\nHost: ")
                started = time.monotonic()
                closed = False
                while time.monotonic() - started < 1:
                    try:
                        client.sendall(b"x")
                        if client.recv(1) == b"":
                            closed = True
                            break
                    except socket.timeout:
                        continue
                    except (BrokenPipeError, ConnectionResetError):
                        closed = True
                        break
                self.assertTrue(closed, "trickling bypassed absolute connection lifetime")
                self.assertLess(time.monotonic() - started, .9)
            wait_for(lambda: self.available(server))

    def test_close_interrupts_incomplete_requests_and_accept_loop(self):
        with self.bounded_server(socket_timeout=2, connection_timeout=10) as server:
            client = socket.create_connection(server.server_address, timeout=1)
            self.addCleanup(client.close)
            client.sendall(b"GET / HTTP/1.1\r\n")
            before = time.monotonic()
            server.shutdown()
            server.server_close()
            client.settimeout(1)
            try:
                remaining = client.recv(1)
            except ConnectionResetError:
                remaining = b""  # A queued/unread connection may close with RST.
            self.assertEqual(remaining, b"")
            self.assertLess(time.monotonic() - before, 1.5)

    def test_invalid_resource_settings_fail_before_binding(self):
        self.assertIn("max_connections", inspect.signature(self.module.create_server).parameters)
        for options in ({"max_connections": 0}, {"max_connections": 9},
                        {"max_connections": True}, {"socket_timeout": 0},
                        {"socket_timeout": float("nan")}, {"connection_timeout": float("inf")},
                        {"socket_timeout": .2, "connection_timeout": .1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.module.create_server(port=0, **options)

    def test_unexpected_request_failure_is_sanitized_and_server_survives(self):
        with running_server(self.module) as server:
            with patch.object(self.module, "read_snapshot",
                              side_effect=RuntimeError("/private/SECRET account-token")):
                try:
                    status, headers, raw = request(server, "/api/health")
                except (OSError, http.client.HTTPException) as error:
                    self.fail(f"request failed without a bounded HTTP response: {type(error).__name__}")
            self.assertEqual(status, 500)
            self.assertEqual(json.loads(raw), {"error": "internal_error"})
            self.assertNotIn(b"SECRET", raw)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(request(server)[0], 200)

    def test_unsupported_method_is_readonly_without_echoing_method(self):
        with running_server(self.module) as server:
            for method in ("CONNECT", "SECRET_METHOD"):
                status, headers, raw = request(server, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(json.loads(raw), {"error": "read_only"})
                self.assertEqual(headers["Allow"], "GET, HEAD")
                self.assertNotIn(method.encode(), raw)

    def test_header_budget_rejects_oversize_without_echo_and_recovers(self):
        with running_server(self.module) as server:
            status, headers, raw = request(server, headers={"X-Private": "SECRET" + "x" * 17000})
            self.assertEqual(status, 431)
            self.assertEqual(json.loads(raw), {"error": "invalid_request"})
            self.assertNotIn(b"SECRET", raw)
            self.assertLess(len(raw), 128)
            self.assertIn("Content-Security-Policy", headers)
            self.assertEqual(request(server)[0], 200)

    def test_bad_http_version_response_does_not_echo_target(self):
        with running_server(self.module) as server:
            with socket.create_connection(server.server_address, timeout=1) as client:
                client.sendall(b"GET /SECRET_PRIVATE_PATH HTTP/not-a-version\r\n\r\n")
                client.shutdown(socket.SHUT_WR)
                raw = b""
                while True:
                    part = client.recv(4096)
                    if not part:
                        break
                    raw += part
            self.assertNotIn(b"SECRET_PRIVATE_PATH", raw)
            self.assertNotIn(b"not-a-version", raw)
            self.assertIn(b"invalid_request", raw)

    @unittest.skipUnless(os.name == "posix", "POSIX signal and descriptor checks")
    def test_sigint_sigterm_exit_codes_close_idle_connections_and_release_port(self):
        for signum, expected in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signum=signum):
                process = subprocess.Popen([sys.executable, "-u", str(SCRIPT), "--port", "0"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    readable, _, _ = select.select([process.stdout], [], [], 5)
                    self.assertTrue(readable, "server never announced a startup result")
                    first = process.stdout.readline()
                    match = re.search(r"http://127[.]0[.]0[.]1:([0-9]+)", first)
                    self.assertIsNotNone(match, first)
                    port = int(match.group(1))
                    with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
                        client.sendall(b"GET / HTTP/1.1\r\n")
                        process.send_signal(signum)
                        out, err = process.communicate(timeout=4)
                        self.assertEqual(process.returncode, expected, out + err)
                        self.assertNotIn("Traceback", out + err)
                        client.settimeout(1)
                        try:
                            remaining = client.recv(1)
                        except ConnectionResetError:
                            remaining = b""  # Queued/unread bytes can cause TCP RST.
                        self.assertEqual(remaining, b"")
                    with socket.socket() as reused:
                        reused.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        reused.bind(("127.0.0.1", port))
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)

    @unittest.skipUnless(os.name == "posix", "POSIX signal and descriptor checks")
    def test_signal_during_owner_event_wait_does_not_reenter_its_lock(self):
        # A signal can arrive while Event.wait owns its non-reentrant condition.
        # Force that real interval in a child so a deadlock remains bounded.
        script = r'''
import importlib.util
import inspect
import os
import signal
import sys
import threading
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("signal_wait_subject", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
signum = int(sys.argv[2])
real_wait = threading.Event.wait
fired = []

def signal_inside_wait(event, timeout=None):
    caller = inspect.currentframe().f_back
    if (not fired and threading.current_thread() is threading.main_thread()
            and caller.f_code is module.serve.__code__):
        fired.append(True)
        with event._cond:
            os.kill(os.getpid(), signum)
    return real_wait(event, timeout)

with patch.object(threading.Event, "wait", signal_inside_wait):
    code = module.serve(port=0)
assert fired, "signal injection did not execute"
raise SystemExit(code)
'''
        for signum, expected in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signum=signum):
                process = subprocess.Popen(
                    [sys.executable, "-I", "-c", script, str(SCRIPT), str(int(signum))],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    try:
                        out, err = process.communicate(timeout=4)
                    except subprocess.TimeoutExpired:
                        self.fail("signal handler reentered the owner wait lock")
                    self.assertEqual(process.returncode, expected, out + err)
                    self.assertNotIn("Traceback", out + err)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)

    def test_startup_failure_returns_sanitized_error(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            result = subprocess.run([sys.executable, str(SCRIPT), "--port",
                                     str(occupied.getsockname()[1])],
                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"], "startup_failed")

    def test_invalid_cli_arguments_do_not_echo_private_input(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--port", "SECRET_PRIVATE"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("SECRET_PRIVATE", result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"], "invalid_arguments")

    def test_accept_thread_start_failure_is_sanitized_and_handlers_are_restored(self):
        before = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        output = io.StringIO()
        with redirect_stdout(output), patch.object(threading.Thread, "start",
                side_effect=RuntimeError("SECRET startup resource exhausted")):
            try:
                code = self.module.cli(["--port", "0"])
            except Exception as error:
                self.fail(f"startup exception escaped CLI boundary: {type(error).__name__}")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "startup_failed"})
        self.assertEqual({signum: signal.getsignal(signum) for signum in before}, before)

    def test_repeated_close_does_not_restart_worker_drain_deadline(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_read(*args, **kwargs):
            entered.set()
            release.wait(6)
            raise ValueError("source unavailable")
        def caller(server):
            try:
                request(server, "/api/health")
            except (OSError, http.client.HTTPException):
                pass
        with running_server(self.module) as server:
            with patch.object(self.module, "read_snapshot", side_effect=blocked_read):
                client = threading.Thread(target=caller, args=(server,), daemon=True)
                client.start()
                try:
                    self.assertTrue(entered.wait(2))
                    server.shutdown()
                    server.server_close()
                    started = time.monotonic()
                    server.server_close()
                    self.assertLess(time.monotonic() - started, .3,
                                    "a repeated close restarted the two-second drain")
                finally:
                    release.set()
                    client.join(2)


if __name__ == "__main__":
    unittest.main()
