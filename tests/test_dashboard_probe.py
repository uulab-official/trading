"""Run the operator probe against real local HTTP endpoints and wrong services."""
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_dashboard_runtime import ROOT, load_server, running_server

SCRIPT = ROOT / "tools/paper_dashboard_check.py"


def probe_payload(*, liveness=False, status="ready", sources=None):
    data = dict(schema_version=1, source="local_paper_dashboard", mode="paper",
                read_only=True, live_enabled=False, observed_at=datetime.now(timezone.utc).isoformat(),
                scope="observer_process" if liveness else "configured_source_readability",
                status="alive" if liveness else status)
    if not liveness:
        data["sources"] = sources or {"journal": "available", "replay": "not_configured",
                                      "research": "not_configured", "paper_session": "not_configured"}
    return data


@contextmanager
def fixture_service(body, *, status=200, headers=None, seen=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if seen is not None:
                seen.append((self.path, self.headers.get("Host")))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_):
            pass
    with HTTPServer(("127.0.0.1", 0), FixtureHandler) as server:
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(2)


class DashboardProbeTests(unittest.TestCase):
    def run_probe(self, *args, env=None):
        self.assertTrue(SCRIPT.is_file(), "operator probe CLI is missing")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, timeout=8, env=env)
        self.assertTrue(result.stdout.strip(), result.stderr)
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        return result, json.loads(result.stdout)

    def test_real_ready_and_no_source_service_have_distinct_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE orders(client_id TEXT,status TEXT,reserved TEXT,updated_at TEXT)")
            before = path.read_bytes()
            with running_server(journal_path=path) as server:
                result, data = self.run_probe("--port", server.server_port)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data["status"], "ready")
                self.assertEqual(data["sources"]["journal"], "available")
                self.assertEqual(path.read_bytes(), before)
            with running_server() as server:
                result, data = self.run_probe("--port", server.server_port)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(data["status"], "not_configured")
                result, data = self.run_probe("--port", server.server_port, "--liveness")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(data["status"], "alive")
                self.assertNotIn("sources", data)

    def test_real_degraded_service_exits_one_without_paths(self):
        with running_server(journal_path="/missing/SECRET.sqlite") as server:
            result, data = self.run_probe("--port", server.server_port)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["sources"]["journal"], "unavailable")
        self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_closed_port_is_transport_failure_not_not_ready(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            result, data = self.run_probe("--port", port, "--timeout", ".2")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["error"], "probe_failed")
        self.assertNotIn("ConnectionRefused", result.stdout + result.stderr)

    def test_proxy_environment_cannot_redirect_probe(self):
        env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:1",
                   HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1", NO_PROXY="")
        with running_server() as server:
            result, data = self.run_probe("--port", server.server_port, "--liveness", env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["status"], "alive")

    def test_redirect_is_rejected_without_following_it(self):
        seen = []
        with fixture_service(probe_payload(), seen=seen) as target:
            with fixture_service({}, status=302,
                    headers={"Location": f"http://127.0.0.1:{target.server_port}/SECRET"}) as redirect:
                result, data = self.run_probe("--port", redirect.server_port)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["error"], "probe_failed")
        self.assertEqual(seen, [])

    def test_wrong_service_and_inconsistent_success_are_rejected(self):
        variants = [
            {"status": "healthy", "running": True},
            {**probe_payload(), "live_enabled": True},
            {**probe_payload(), "read_only": 1},
            {**probe_payload(), "schema_version": True},
            {**probe_payload(), "source": "legacy_server"},
            {**probe_payload(), "observed_at": "yesterday"},
            {**probe_payload(), "observed_at": "2026-01-01T00:00:00"},
            {**probe_payload(), "extra": "/SECRET"},
            {**probe_payload(), "sources": {"journal": "available"}},
            {**probe_payload(), "sources": {key: "unavailable" for key in
                ("journal", "replay", "research", "paper_session")}},
        ]
        for value in variants:
            with self.subTest(value=value), fixture_service(value) as server:
                result, data = self.run_probe("--port", server.server_port)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"], "probe_failed")
                self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_http_status_must_agree_with_semantic_readiness(self):
        for code, status in ((503, "ready"), (200, "degraded"), (500, "ready")):
            with self.subTest(code=code, status=status), fixture_service(
                    probe_payload(status=status), status=code) as server:
                result, data = self.run_probe("--port", server.server_port)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"], "probe_failed")

    def test_json_duplicate_keys_oversize_and_invalid_encoding_are_rejected(self):
        for raw in (b'{"status":"ready","status":"degraded"}',
                    b"x" * 8193, b"\xff", b"[" * 2000 + b"]" * 2000):
            with self.subTest(length=len(raw)), fixture_service(raw) as server:
                result, data = self.run_probe("--port", server.server_port)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"], "probe_failed")

    def test_response_scope_matches_selected_probe(self):
        for flag, data in ((False, probe_payload(liveness=True)), (True, probe_payload())):
            with self.subTest(liveness=flag), fixture_service(data) as server:
                args = ["--port", server.server_port] + (["--liveness"] if flag else [])
                result, payload = self.run_probe(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(payload["error"], "probe_failed")

    def test_port_timeout_and_unsupported_host_are_rejected_without_echo(self):
        for args in (("--port", "0"), ("--port", "65536"), ("--port", "SECRET"),
                     ("--timeout", "0"), ("--timeout", "nan"), ("--timeout", "inf"),
                     ("--timeout", "11"), ("--host", "SECRET"), ("--url", "http://SECRET")):
            with self.subTest(args=args):
                result, data = self.run_probe(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"], "invalid_arguments")
                self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_total_timeout_stops_a_service_trickling_response_headers(self):
        class SlowHeaders(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(1)
                self.request.recv(4096)
                try:
                    self.request.sendall(b"HTTP/1.0 200 OK\r\nX-Slow: ")
                    for _ in range(100):
                        self.request.sendall(b"x")
                        time.sleep(.02)
                except OSError:
                    pass
        with socketserver.TCPServer(("127.0.0.1", 0), SlowHeaders) as server:
            worker = threading.Thread(target=server.handle_request, daemon=True)
            worker.start()
            started = time.monotonic()
            result, data = self.run_probe("--port", server.server_address[1], "--timeout", ".15")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(data["error"], "probe_failed")
            self.assertLess(time.monotonic() - started, 1)
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
