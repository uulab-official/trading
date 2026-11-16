"""HTTP/SQLite integration tests; all requests stay on loopback."""
import importlib.util
import http.client
import json
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_quality", ROOT / "apps/dashboard/server.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class DashboardOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper.sqlite"
        self.make_db(self.path)
        class TestHandler(module.Handler):
            journal_path = self.path
            def log_message(self, *_):
                pass
        self.handler = TestHandler
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def make_db(self, path):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY,status TEXT,reserved TEXT,updated_at TEXT,payload TEXT)")
            rows = [("a", "unknown", "12", "2026-10-31 01:00:00", '{"currency":"KRW","symbol":"SYNTHETIC"}'),
                    ("b", "accepted", "20", "2026-10-31 02:00:00", '{"currency":"USDT","symbol":"TEST"}'),
                    ("c", "cancelled", "0", "2026-10-31 03:00:00", '{"currency":"KRW","token":"never expose this"}')]
            db.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", rows)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self, route="/api/snapshot", *, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, route, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw)
        finally:
            conn.close()

    def test_health_verifies_database_not_file_existence(self):
        bad = Path(self.tmp.name) / "not-a-database"
        bad.write_text("not sqlite")
        self.handler.journal_path = bad
        status, _, body = self.request("/api/health")
        self.assertEqual(status, 503)
        self.assertFalse(body["journal_connected"])
        self.assertFalse(body["live_enabled"])
        self.assertNotIn(str(bad), json.dumps(body))

    def test_health_rejects_wrong_schema(self):
        bad = Path(self.tmp.name) / "wrong.sqlite"
        with closing(sqlite3.connect(bad)) as db, db:
            db.execute("CREATE TABLE unrelated(x)")
        self.handler.journal_path = bad
        status, _, body = self.request("/api/health")
        self.assertEqual(status, 503)
        self.assertFalse(body["journal_connected"])

    def test_missing_journal_is_not_created(self):
        missing = Path(self.tmp.name) / "missing.sqlite"
        self.handler.journal_path = missing
        self.assertEqual(self.request("/api/orders")[0], 503)
        self.assertFalse(missing.exists())

    def test_uri_special_characters_are_not_query_parameters(self):
        path = Path(self.tmp.name) / "한글 #what?mode=rw.sqlite"
        self.make_db(path)
        self.handler.journal_path = path
        status, _, body = self.request("/api/orders")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["orders"]), 3)

    def test_snapshot_is_actual_state_with_currency_separation(self):
        status, _, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "paper")
        self.assertFalse(body["live_enabled"])
        self.assertFalse(body["accounting_available"])
        self.assertEqual(body["summary"]["total_orders"], 3)
        self.assertEqual(body["summary"]["active_orders"], 2)
        self.assertEqual(body["summary"]["needs_reconciliation"], 1)
        self.assertEqual(body["summary"]["reserved_by_currency"], {"KRW": "12", "USDT": "20"})
        self.assertNotIn("never expose this", json.dumps(body))
        self.assertNotIn("payload", json.dumps(body))
        self.assertNotIn("equity", body)

    def test_filters_and_pagination_do_not_change_whole_journal_summary(self):
        status, _, body = self.request("/api/snapshot?limit=1&offset=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["orders"]), 1)
        self.assertEqual(body["orders"][0]["client_id"], "b")
        self.assertEqual(body["pagination"]["total"], 3)
        self.assertTrue(body["pagination"]["has_more"])
        status, _, body = self.request("/api/snapshot?status=unknown")
        self.assertEqual(status, 200)
        self.assertEqual([o["client_id"] for o in body["orders"]], ["a"])
        self.assertEqual(body["summary"]["total_orders"], 3)

    def test_empty_query_works_with_python310_strict_parser(self):
        from unittest.mock import patch
        actual_parser = module.parse_qs
        def python310_parser(query, **kwargs):
            # CPython 3.10 rejects an empty string under strict_parsing=True.
            if query == "" and kwargs.get("strict_parsing"):
                raise ValueError("bad query field: ''")
            return actual_parser(query, **kwargs)
        with patch.object(module, "parse_qs", side_effect=python310_parser):
            for route in ("/api/health", "/api/orders", "/api/snapshot", "/api/events"):
                with self.subTest(route=route):
                    self.assertEqual(self.request(route)[0], 200)
            self.assertEqual(self.request("/api/snapshot?limit=1")[0], 200)
            self.assertEqual(self.request("/api/snapshot?broken")[0], 400)

    def test_invalid_query_is_rejected(self):
        for query in ["limit=0", "limit=101", "limit=no", "offset=-1", "status=oops", "limit=1&limit=2", "path=/tmp/no"]:
            with self.subTest(query=query):
                self.assertEqual(self.request("/api/snapshot?" + query)[0], 400)

    def test_bad_host_is_rejected(self):
        self.assertEqual(self.request("/api/health", headers={"Host": "attacker.example"})[0], 403)

    def test_cross_origin_and_fetch_site_rejected(self):
        for headers in [{"Origin": "https://attacker.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/orders", headers=headers)[0], 403)

    def test_same_origin_allowed_without_cors(self):
        status, headers, body = self.request("/api/orders", headers={"Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))

    def test_all_write_methods_disabled(self):
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"]:
            with self.subTest(method=method):
                status, headers, _ = self.request("/api/orders", method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_corrupt_amount_causes_unavailable_not_fake_summary(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET reserved='NaN' WHERE client_id='a'")
        self.assertEqual(self.request("/api/health")[0], 503)
        self.assertEqual(self.request()[0], 503)

    def test_no_mutation_by_reads(self):
        before = self.path.read_bytes()
        self.assertEqual(self.request("/api/orders")[0], 200)
        self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_minimal_table_supported_without_fake_audit(self):
        path = Path(self.tmp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE orders(client_id TEXT,status TEXT,reserved TEXT,updated_at TEXT)")
            db.execute("INSERT INTO orders VALUES('a','unknown','12','2026-01-01')")
        self.handler.journal_path = path
        status, _, body = self.request()
        self.assertEqual(status, 200)
        self.assertFalse(body["audit_available"])
        self.assertEqual(body["summary"]["reserved_by_currency"], {"UNSPECIFIED": "12"})

    def test_mixed_timestamp_formats_sort_chronologically(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET updated_at='2026-10-31T01:00:00Z' WHERE client_id='a'")
            db.execute("UPDATE orders SET updated_at='2026-10-31 23:00:00' WHERE client_id='b'")
        status, _, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["orders"][0]["client_id"], "b")

    def test_invalid_timestamp_is_not_healthy(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET updated_at='bad time' WHERE client_id='a'")
        self.assertEqual(self.request("/api/health")[0], 503)

    def test_server_instances_do_not_share_configuration(self):
        self.assertTrue(hasattr(module, "create_server"))
        with module.create_server(self.path, 0) as first, module.create_server(None, 0) as second:
            self.assertEqual(first.RequestHandlerClass.journal_path, self.path)
            self.assertIsNone(second.RequestHandlerClass.journal_path)
            self.assertEqual(first.server_address[0], "127.0.0.1")

    def test_unknown_new_schema_fails_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE order_journal_meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO order_journal_meta VALUES('schema_version','999')")
        self.assertEqual(self.request("/api/health")[0], 503)


if __name__ == "__main__":
    unittest.main()
