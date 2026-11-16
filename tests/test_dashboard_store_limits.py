"""Real SQLite resource limits for the read-only journal observer."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bounded_dashboard_store", ROOT / "apps/dashboard/store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class DashboardStoreLimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.sqlite"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY,status TEXT,reserved TEXT,updated_at TEXT,payload TEXT)")
            db.execute("CREATE TABLE order_events(id INTEGER PRIMARY KEY,client_id TEXT,kind TEXT,from_status TEXT,to_status TEXT,reserved TEXT,occurred_at TEXT)")

    def orders(self, count=1, payload='{"currency":"KRW","symbol":"TEST"}'):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", (
                (f"order-{i:05}", "accepted", "0.000000000000000001", "2026-11-14T00:00:00Z", payload)
                for i in range(count)))

    def unavailable(self, **kwargs):
        before = self.path.read_bytes()
        with self.assertRaises(store.JournalUnavailable) as caught:
            store.read_snapshot(self.path, **kwargs)
        self.assertEqual(str(caught.exception), "journal_unavailable")
        self.assertEqual(self.path.read_bytes(), before)

    def test_ten_thousand_rows_keep_exact_total_and_requested_page(self):
        self.orders(10_000)
        before = self.path.read_bytes()
        started = time.monotonic()
        result = store.read_snapshot(self.path, limit=2, offset=9998)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["summary"]["total_orders"], 10_000)
        self.assertEqual(result["summary"]["reserved_by_currency"], {"KRW": "1.0000E-14"})
        self.assertEqual([row["client_id"] for row in result["orders"]], ["order-09998", "order-09999"])
        self.assertEqual(result["pagination"]["total"], 10_000)
        self.assertFalse(result["pagination"]["has_more"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_small_page_does_not_retain_all_matching_order_projections(self):
        self.orders(5000)
        tracemalloc.start()
        try:
            result = store.read_snapshot(self.path, limit=1, offset=4999)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual([row["client_id"] for row in result["orders"]], ["order-04999"])
        self.assertEqual(result["pagination"]["total"], 5000)
        # Five thousand retained dictionaries take several MiB; a streamed
        # validation with one projected order stays comfortably below one MiB.
        self.assertLess(peak, 1024 * 1024)

    def test_surplus_order_is_rejected_even_outside_filter_and_page(self):
        self.orders(10_001)
        self.unavailable(limit=1, status="filled")

    def test_payload_byte_boundary_accepts_exact_limit_and_rejects_next_byte(self):
        prefix = '{"padding":"'
        suffix = '"}'
        payload = prefix + "x" * (16_384 - len(prefix + suffix)) + suffix
        self.orders(payload=payload)
        self.assertEqual(store.read_snapshot(self.path)["summary"]["total_orders"], 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET payload=?", (payload[:-2] + 'x"}',))
        self.unavailable()

    def test_payload_byte_limit_uses_utf8_even_in_utf16_database(self):
        self.path.unlink()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA encoding='UTF-16'")
            db.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY,status TEXT,reserved TEXT,updated_at TEXT,payload TEXT)")
        self.orders(payload='{"padding":"' + 'x' * (16_384 - 14) + '"}')
        self.assertEqual(store.read_snapshot(self.path)["summary"]["total_orders"], 1)

    def test_payload_bound_counts_utf8_bytes(self):
        self.orders(payload=json.dumps({"padding": "한" * 6000}, ensure_ascii=False))
        self.unavailable()

    def test_deep_json_is_generic_unavailable(self):
        self.orders(payload='{"nested":' + '[' * 12000 + '0' + ']' * 12000 + '}')
        self.unavailable()

    def test_in_budget_json_recursion_error_is_generic_unavailable(self):
        self.orders(payload='{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}')
        # Exercise the stdlib's Python decoder, whose recursion threshold is
        # lower than this runtime's C accelerator, with a payload below 16 KiB.
        decoder = json.JSONDecoder()
        decoder.scan_once = json.scanner.py_make_scanner(decoder)
        with patch.object(store.json, "loads", side_effect=decoder.decode):
            self.unavailable()

    def test_invalid_utf8_from_sqlite_is_generic_unavailable(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET payload=CAST(x'80' AS TEXT)")
        self.unavailable()

    def test_metadata_sixteen_entries_accepted_seventeen_rejected(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE order_journal_meta(key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO order_journal_meta VALUES(?,?)", [
                ("schema_version", "2"), ("source", "paper_journal")
            ] + [(f"extra-{i}", "ok") for i in range(14)])
        self.assertEqual(store.read_snapshot(self.path)["schema_version"], 2)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO order_journal_meta VALUES('surplus','no')")
        self.unavailable()

    def test_huge_metadata_field_is_rejected(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE order_journal_meta(key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO order_journal_meta VALUES(?,?)", [
                ("schema_version", "2"), ("source", "paper_journal"), ("extra", "x" * 10_000)])
        self.unavailable()

    def test_returned_events_are_limited_to_fifty(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany("INSERT INTO order_events VALUES(?,?,?,?,?,?,?)", (
                (i, "order-00000", "accepted", "submitting", "accepted", "1", "2026-11-14T00:00:00Z")
                for i in range(1, 61)))
        result = store.read_snapshot(self.path)
        self.assertEqual([event["id"] for event in result["events"]], list(range(60, 10, -1)))

    def test_huge_returned_event_text_rejects_whole_summary(self):
        self.orders()
        for field in ("kind", "occurred_at", "reserved", "client_id", "from_status", "to_status"):
            with self.subTest(field=field):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("DELETE FROM order_events")
                    db.execute("INSERT INTO order_events VALUES(1,'order-00000','accepted',NULL,'accepted','1','2026-11-14')")
                    db.execute(f"UPDATE order_events SET {field}=?", ("0" * 10_000,))
                self.unavailable()

    def test_event_identifier_cannot_return_unbounded_text_from_custom_schema(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE order_events")
            db.execute("CREATE TABLE order_events(id TEXT,client_id TEXT,kind TEXT,from_status TEXT,to_status TEXT,reserved TEXT,occurred_at TEXT)")
            db.execute("INSERT INTO order_events VALUES(?,'order-00000','accepted',NULL,'accepted','1','2026-11-14')", ("9" * 10_000,))
        self.unavailable()

    def test_event_ordering_validates_raw_newest_id_before_fifty_event_limit(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE order_events")
            # No declared affinity permits genuine integer IDs alongside a
            # malformed textual ID, which SQLite sorts ahead of integers DESC.
            db.execute("CREATE TABLE order_events(id,client_id TEXT,kind TEXT,from_status TEXT,to_status TEXT,reserved TEXT,occurred_at TEXT)")
            db.executemany("INSERT INTO order_events VALUES(?,'order-00000','accepted',NULL,'accepted','1','2026-11-14')", (
                (i,) for i in range(1, 51)))
            db.execute("INSERT INTO order_events VALUES(?,'order-00000','accepted',NULL,'accepted','1','2026-11-14')", ("9" * 10_000,))
        self.unavailable()

    def test_database_and_wal_sidecar_size_limit(self):
        self.orders()
        with self.subTest(file="database"):
            with self.path.open("ab") as target:
                target.truncate(64 * 1024 * 1024 + 1)
            self.unavailable()
        # A surplus WAL must be rejected even when SQLite would ignore its bytes.
        with self.path.open("r+b") as target:
            target.truncate(16_384)
        wal = Path(str(self.path) + "-wal")
        with wal.open("wb") as target:
            target.truncate(64 * 1024 * 1024 + 1)
        self.unavailable()
        self.assertEqual(wal.stat().st_size, 64 * 1024 * 1024 + 1)

    def test_exact_file_limit_is_accepted(self):
        self.orders()
        with self.path.open("ab") as target:
            target.truncate(64 * 1024 * 1024)
        result = store.read_snapshot(self.path)
        self.assertEqual(result["summary"]["total_orders"], 1)
        self.assertEqual(self.path.stat().st_size, 64 * 1024 * 1024)

    def test_shared_memory_sidecar_size_is_bounded(self):
        self.orders()
        sidecar = Path(str(self.path) + "-shm")
        with sidecar.open("wb") as target:
            target.truncate(64 * 1024 * 1024 + 1)
        self.unavailable()
        self.assertEqual(sidecar.stat().st_size, 64 * 1024 * 1024 + 1)

    def test_active_wal_journal_data_is_not_mutated(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO orders VALUES('a','accepted','12','2026-11-14','{}')")
            writer.commit()
            wal = Path(str(self.path) + "-wal")
            before = (self.path.read_bytes(), wal.read_bytes())
            result = store.read_snapshot(self.path)
            self.assertEqual(result["summary"]["reserved_by_currency"], {"UNSPECIFIED": "12"})
            self.assertEqual((self.path.read_bytes(), wal.read_bytes()), before)

    def test_vm_budget_rejects_expensive_event_scan(self):
        self.orders()
        with closing(sqlite3.connect(self.path)) as db, db:
            # An unindexed filtered scan exceeds one million VM operations
            # without exceeding the independent file or order limits.
            db.execute("CREATE TABLE order_journal_meta(key TEXT,value TEXT)")
            db.executemany("INSERT INTO order_journal_meta VALUES(?,?)", [
                ("schema_version", "2"), ("source", "paper_journal")])
            db.executemany("INSERT INTO order_events(client_id,kind,from_status,to_status,reserved,occurred_at) VALUES(?,?,?,?,?,?)", (
                ("other", "accepted", None, "accepted", "1", "2026-11-14") for _ in range(400_000)))
        self.unavailable(event_client_id="missing")

    def test_elapsed_budget_is_checked_during_python_validation(self):
        self.orders(4)
        real_loads = store.json.loads
        def slow_loads(value):
            time.sleep(0.28)
            return real_loads(value)
        # The real database/JSON decode still run; delay only Python validation
        # so a SQLite-only deadline cannot satisfy this contract.
        with patch.object(store.json, "loads", side_effect=slow_loads):
            self.unavailable()

    def test_invalid_row_outside_returned_page_is_still_validated(self):
        self.orders(2)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE orders SET reserved='NaN' WHERE client_id='order-00001'")
        self.unavailable(limit=1)


if __name__ == "__main__":
    unittest.main()
