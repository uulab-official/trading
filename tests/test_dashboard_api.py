import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

path = Path(__file__).resolve().parents[1] / "apps/dashboard/server.py"
spec = importlib.util.spec_from_file_location("dashboard_server", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "paper.db")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE orders(client_id TEXT,status TEXT,reserved TEXT,updated_at TEXT)")
            db.execute("INSERT INTO orders VALUES('a','unknown','12','2026-01-01')")
        class TestHandler(module.Handler):
            journal_path = self.path
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def test_read_only_orders(self):
        with urlopen(self.base + "/api/orders") as response:
            data = json.load(response)
        self.assertEqual(data["orders"][0]["status"], "unknown")
        self.assertEqual(data["mode"], "paper")

    def test_health_is_never_live(self):
        with urlopen(self.base + "/api/health") as response:
            data = json.load(response)
        self.assertFalse(data["live_enabled"])


if __name__ == "__main__":
    unittest.main()
