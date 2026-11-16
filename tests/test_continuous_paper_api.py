"""Passive continuous-paper route keeps the bounded server and readiness contract."""
import asyncio
import json
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_dashboard_runtime import load_server, request, running_server
from test_continuous_paper_cli import ROOT, create_current_capture


class ContinuousPaperApiTests(unittest.TestCase):
    def setUp(self):
        self.module = load_server()

    def test_unconfigured_route_is_a_private_unavailable_response(self):
        with running_server(self.module) as server:
            code, headers, raw = request(server, "/api/paper-stream")
            self.assertEqual(code, 503)
            self.assertEqual(json.loads(raw), {
                "error": "paper_stream_unavailable", "mode": "paper",
                "read_only": True, "live_enabled": False,
            })
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(request(server, "/api/paper-stream", method="HEAD")[2], b"")
            self.assertEqual(request(server, "/api/paper-stream?extra=1")[0], 400)
            self.assertEqual(request(server, "/api/paper-stream", method="POST")[0], 405)
            self.assertEqual(request(server, "/api/paper-stream",
                headers={"Origin": "https://evil.test"})[0], 403)
            code, _, raw = request(server, "/api/readiness")
            self.assertEqual(code, 503)
            self.assertEqual(set(json.loads(raw)["sources"]),
                {"journal", "replay", "research", "paper_session"})

    def test_missing_source_is_not_created_or_echoed(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "SECRET-stream"
            with running_server(self.module, paper_stream_path=missing) as server:
                code, _, raw = request(server, "/api/paper-stream")
                self.assertEqual(code, 503)
                self.assertEqual(json.loads(raw)["error"], "paper_stream_unavailable")
                self.assertNotIn(b"SECRET", raw)
            self.assertFalse(missing.exists())

    def test_unexpected_observer_failure_is_private_and_server_survives(self):
        with running_server(self.module) as server:
            with patch.object(self.module, "_paper_stream_status",
                    side_effect=RuntimeError("/SECRET/account")):
                code, _, raw = request(server, "/api/paper-stream")
            self.assertEqual(code, 500)
            self.assertEqual(json.loads(raw), {"error": "internal_error"})
            self.assertEqual(request(server)[0], 200)

    def test_actual_account_observation_and_head_leave_saved_bytes_unchanged(self):
        from trading_bot.paper_stream import run_stream
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, account = root / "source", root / "account"
            clock = create_current_capture(source)
            result = asyncio.run(run_stream(account, capture=source,
                                           max_polls=1, clock=clock))
            self.assertEqual(result["exit_code"], 0)
            before = {directory.name: {p.name: p.read_bytes() for p in directory.iterdir()}
                      for directory in (source, account)}
            with running_server(self.module, paper_stream_path=account) as server:
                code, headers, raw = request(server, "/api/paper-stream")
                self.assertEqual(code, 200)
                report = json.loads(raw)
                self.assertEqual(report["stream_id"], result["observation"]["stream_id"])
                self.assertEqual(report["status"], "stopped")
                self.assertEqual(report["cursor"]["bars_processed"], 0)
                self.assertEqual(report["account"]["fill_count"], 0)
                self.assertIsNone(report["pending_signal"])
                self.assertEqual(report["integrity"]["verification"], "structural_head_only")
                self.assertEqual(report["scope"], "delayed_next_open_simulation")
                self.assertIs(report["read_only"], True)
                self.assertIs(report["live_enabled"], False)
                self.assertLess(len(raw), 16384)
                self.assertNotIn(str(root).encode(), raw)
                self.assertEqual(headers["Cache-Control"], "no-store")
                code, head_headers, body = request(server, "/api/paper-stream", method="HEAD")
                self.assertEqual(code, 200)
                self.assertEqual(body, b"")
                self.assertGreater(int(head_headers["Content-Length"]), 0)
                _, _, readiness = request(server, "/api/readiness")
                self.assertEqual(set(json.loads(readiness)["sources"]),
                    {"journal", "replay", "research", "paper_session"})
            self.assertEqual(before, {
                directory.name: {p.name: p.read_bytes() for p in directory.iterdir()}
                for directory in (source, account)})

    def test_dashboard_cli_passes_the_configured_account_to_its_live_route(self):
        from trading_bot.paper_stream import run_stream
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, account = root / "source", root / "account"
            clock = create_current_capture(source)
            result = asyncio.run(run_stream(account, capture=source,
                                           max_polls=1, clock=clock))
            self.assertEqual(result["exit_code"], 0)
            process = subprocess.Popen([
                sys.executable, "-u", str(ROOT / "apps/dashboard/server.py"),
                "--paper-stream", str(account), "--port", "0",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                readable, _, _ = select.select([process.stdout], [], [], 8)
                self.assertTrue(readable, "dashboard did not announce its port")
                line = process.stdout.readline()
                match = re.search(r"http://127[.]0[.]0[.]1:([0-9]+)", line)
                self.assertIsNotNone(match, line)
                server = types.SimpleNamespace(server_port=int(match.group(1)))
                code, _, raw = request(server, "/api/paper-stream")
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(raw)["stream_id"], result["observation"]["stream_id"])
                process.send_signal(signal.SIGTERM)
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 143, out + err)
                self.assertNotIn("Traceback", out + err)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
