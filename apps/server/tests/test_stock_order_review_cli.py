"""Actual offline review/report CLI and integrated recovery trial."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from trading_core.stock_order_journal import StockOrderJournal

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
CLI = ROOT / "tools/stock_order_review.py"
SMOKE = ROOT / "tools/stock_order_review_smoke.py"
FIXTURE = ROOT / "config/examples/synthetic_stock_order_events.json"
GUARD = """
import runpy, socket, sys
def forbidden(*args, **kwargs):
    raise AssertionError('Review trial forbids network and DNS')
socket.socket.connect = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class StockOrderReviewCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / "journal.sqlite"
        self.events = json.loads(FIXTURE.read_text())["events"]
        self.journal = StockOrderJournal.create(self.database, account_ref="demo-stock-orders")
        for event in self.events:
            self.journal.record(event)

    def run_cli(self, script, arguments):
        self.assertTrue(script.is_file(), "Expected implemented offline review entrypoint")
        environment = dict(os.environ)
        environment.update({
            "KIS_VTS_ACCESS_TOKEN": "EXAMPLE-SECRET-TOKEN-DO-NOT-PRINT",
            "KIS_VTS_APPKEY": "EXAMPLE-SECRET-APPKEY-DO-NOT-PRINT",
            "KIS_VTS_APPSECRET": "EXAMPLE-SECRET-APPSECRET-DO-NOT-PRINT",
            "KIS_VTS_CANO": "12345678", "KIS_VTS_ACNT_PRDT_CD": "01",
            "HTTPS_PROXY": "https://EXAMPLE-SECRET-PROXY.invalid",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", GUARD, str(script), *arguments],
            cwd=self.directory, env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertNotIn("EXAMPLE-SECRET", result.stdout + result.stderr)
        self.assertNotIn("12345678", result.stdout + result.stderr)
        return result

    def review(self, *extra, database=None, account="demo-stock-orders"):
        return self.run_cli(CLI, [
            "--mode", "paper", "--database", str(database or self.database),
            "--account-ref", account, *extra,
        ])

    def observations(self):
        rows = []
        for order in self.journal.snapshot()["orders"]:
            rows.append({
                "ord_dt": "20261120", "ord_gno_brno": order["broker_org_no"],
                "odno": order["broker_order_id"], "orgn_odno": "0000000000",
                "pdno": "005930", "sll_buy_dvsn_cd": "02" if order["side"] == "buy" else "01",
                "ord_dvsn_cd": "00", "ord_qty": str(order["quantity"]),
                "ord_unpr": order["limit_price"], "tot_ccld_qty": str(order["filled_quantity"]),
                "tot_ccld_amt": order["gross_krw"],
                "avg_prvs": "9940" if order["side"] == "buy" else "11040",
                "rmn_qty": "0", "cncl_cfrm_qty": "5" if order["side"] == "buy" else "0",
                "rjct_qty": "0", "cncl_yn": "Y" if order["side"] == "buy" else "N",
                "ord_tmd": "091000", "excg_id_dvsn_cd": "KRX",
            })
        return {
            "schema_version": 1, "mode": "paper", "live_orders_enabled": False,
            "provider": "kis_vts_readonly_order_snapshot", "market": "KRX", "currency": "KRW",
            "account_ref": "demo-stock-orders", "account_binding_verified": False,
            "inquiry_date": "2026-11-20", "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "pagination_complete": True, "pages_fetched": 1, "row_count": 2,
            "atomic_snapshot_verified": False, "economic_reconciliation_verified": False,
            "rows": rows,
        }

    def observation_file(self, value=None):
        path = self.directory / "observations.json"
        path.write_text(json.dumps(self.observations() if value is None else value))
        return path

    def test_actual_cli_writes_private_matching_json_and_korean_html(self):
        observations = self.observation_file()
        before = self.journal.snapshot()
        output, page = self.directory / "review.json", self.directory / "review.html"
        result = self.review("--observations", str(observations), "--output-json", str(output),
                             "--output-html", str(page))
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report, json.loads(output.read_text()))
        self.assertEqual("observations_equal", report["comparison_status"])
        self.assertFalse(report["needs_attention"])
        self.assertEqual(2, report["summary"]["matched_order_count"])
        self.assertEqual("5285", report["journal"]["cash_delta_krw"])
        for key in ("safe_to_submit", "account_binding_verified", "economic_reconciliation_verified",
                    "lifecycle_confirmation_verified", "live_orders_enabled"):
            self.assertIs(report[key], False)
        self.assertEqual(before, self.journal.snapshot())
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(page.stat().st_mode))
        parser = PageParser()
        parser.feed(page.read_text())
        self.assertIn("주문 점검", page.read_text())
        self.assertIn("체결 증명", page.read_text())
        self.assertTrue(any(tag == "html" and attrs.get("lang") == "ko" for tag, attrs in parser.tags))
        self.assertFalse(any(tag in ("script", "iframe", "img", "form", "link") for tag, _ in parser.tags))
        csp = next(attrs["content"] for tag, attrs in parser.tags
                   if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("form-action 'none'", csp)

    def test_difference_exit_three_retains_full_report_without_mutation(self):
        value = self.observations()
        value["rows"][0]["tot_ccld_qty"] = "4"
        value["rows"][0]["tot_ccld_amt"] = "39700"
        before = self.journal.snapshot()
        result = self.review("--observations", str(self.observation_file(value)))
        self.assertEqual(3, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["needs_attention"])
        fields = {item["field"] for item in report["orders"][0]["differences"]}
        self.assertEqual({"filled_quantity", "gross_krw"}, fields)
        self.assertEqual(before, self.journal.snapshot())

    def test_stale_and_future_observations_require_attention(self):
        for delta, expected in ((-3600, "stale"), (3600, "future")):
            with self.subTest(freshness=expected):
                value = self.observations()
                value["observed_at_utc"] = (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()
                result = self.review("--observations", str(self.observation_file(value)))
                self.assertEqual(3, result.returncode, result.stderr)
                self.assertEqual(expected, json.loads(result.stdout)["observation_summary"]["freshness"])

    def test_journal_only_pending_state_blocks_and_missing_database_is_not_created(self):
        pending = self.directory / "pending.sqlite"
        journal = StockOrderJournal.create(pending, account_ref="demo-stock-orders")
        journal.record(self.events[0])
        journal.record(self.events[1])
        result = self.review(database=pending)
        self.assertEqual(3, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("not_supplied", report["comparison_status"])
        self.assertTrue(report["journal_blocked"])
        self.assertEqual("uncertain", report["orders"][0]["local"]["status"])
        missing = self.directory / "absent-parent" / "missing.sqlite"
        output = self.directory / "must-not-create.json"
        result = self.review("--output-json", str(output), database=missing)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertFalse(missing.parent.exists())
        self.assertFalse(output.exists())

    def test_existing_html_preserved_and_new_json_not_published(self):
        output, page = self.directory / "new.json", self.directory / "existing.html"
        page.write_text("existing evidence")
        result = self.review("--output-json", str(output), "--output-html", str(page))
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())
        self.assertEqual("existing evidence", page.read_text())

    def test_source_sqlite_sidecars_and_cross_output_families_are_refused(self):
        before = self.database.read_bytes()
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                destination = Path(str(self.database) + suffix)
                self.assertFalse(destination.exists())
                result = self.review("--output-html", str(destination))
                self.assertEqual(2, result.returncode)
                self.assertFalse(destination.exists())
        self.assertEqual(before, self.database.read_bytes())
        output = self.directory / "review.json"
        result = self.review("--output-json", str(output), "--output-html", str(output) + "-wal")
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + "-wal").exists())

    def test_duplicate_output_aliases_and_dangling_links_preserve_targets(self):
        directory_alias = self.directory / "alias"
        directory_alias.symlink_to(self.directory, target_is_directory=True)
        output = self.directory / "result.json"
        result = self.review("--output-json", str(output), "--output-html", str(directory_alias / output.name))
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())
        missing = self.directory / "missing-target"
        dangling = self.directory / "dangling.html"
        dangling.symlink_to(missing)
        result = self.review("--output-html", str(dangling))
        self.assertEqual(2, result.returncode)
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(missing.exists())

    def test_strict_observation_input_refuses_duplicates_nonfinite_and_oversize(self):
        valid = json.dumps(self.observations())
        for body in (valid[:-1] + ',"mode":"paper"}', '{"row_count":NaN}',
                     '{"row_count":1e999}', '{"row_count":' + "1" * 1000 + '}',
                     " " * 2_000_001):
            with self.subTest(length=len(body)):
                source = self.directory / "bad.json"
                source.write_text(body)
                result = self.review("--observations", str(source))
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
        self.assertEqual(14, self.journal.snapshot()["event_count"])

    def test_invalid_mode_alias_age_and_input_symlink_fail_without_echo(self):
        result = self.run_cli(CLI, ["--mode", "EXAMPLE-SECRET-mode"])
        self.assertEqual(2, result.returncode)
        for extra in (("--max-age-seconds", "0"), ("--max-age-seconds", "86401")):
            with self.subTest(arguments=extra):
                self.assertEqual(2, self.review(*extra).returncode)
        self.assertEqual(2, self.review(account="EXAMPLE-SECRET-unknown").returncode)
        source = self.observation_file()
        alias = self.directory / "observation-link.json"
        alias.symlink_to(source)
        self.assertEqual(2, self.review("--observations", str(alias)).returncode)

    def test_renderer_escapes_all_caller_text_and_refuses_verified_flags(self):
        self.assertTrue(CLI.is_file(), "Expected implemented report renderer")
        from tools.stock_order_review import build_review, render_review_html
        report = build_review(self.database, account_ref="demo-stock-orders")
        forged = deepcopy(report)
        forged["safe_to_submit"] = True
        with self.assertRaises(ValueError):
            render_review_html(forged)
        payload = '<script>alert("example")</script><img src=x onerror=alert(1)>'
        report["orders"][0]["local"]["client_order_id"] = payload
        page = render_review_html(report)
        self.assertNotIn(payload, page)
        self.assertIn("&lt;script&gt;", page)
        parser = PageParser()
        parser.feed(page)
        self.assertFalse(any(tag in ("script", "img") for tag, _ in parser.tags))
        self.assertFalse(any(name.startswith("on") for _, attrs in parser.tags for name in attrs))

    def test_zero_scope_report_explicitly_shows_no_comparison_targets(self):
        from tools.stock_order_review import build_review, render_review_html
        clock = datetime(2026, 11, 21, 1, tzinfo=timezone.utc)
        observations = self.observations()
        observations.update(inquiry_date="2026-11-21", observed_at_utc=clock.isoformat(), rows=[], row_count=0)
        report = build_review(self.database, account_ref="demo-stock-orders", now=clock,
                              observations=self.observation_file(observations))
        self.assertEqual(0, report["summary"]["scoped_order_count"])
        self.assertEqual(0, report["summary"]["matched_order_count"])
        page = render_review_html(report)
        self.assertIn("비교 대상 없음", page)
        self.assertIn("범위 내 주문 0개", page)
        self.assertIn("연결된 조회 0건", page)
        self.assertNotIn("비교한 관측값 일치", page)

    def test_multi_output_failure_and_replacement_preserve_evidence(self):
        self.assertTrue(CLI.is_file(), "Expected implemented private report writer")
        from tools.stock_order_review import write_reports
        first, second = self.directory / "first.json", self.directory / "second.html"
        with patch("os.fsync", side_effect=[None, OSError("private write failure")]):
            with self.assertRaises(OSError):
                write_reports([(first, "{}"), (second, "<html></html>")])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

        def replace(_descriptor):
            first.unlink()
            first.write_text("replacement evidence")

        with patch("os.fsync", side_effect=replace):
            with self.assertRaises(ValueError):
                write_reports([(first, "{}")])
        self.assertEqual("replacement evidence", first.read_text())

    def test_default_integrated_trial_verifies_comparison_and_recovery(self):
        before = set(self.directory.iterdir())
        result = self.run_cli(SMOKE, [])
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("synthetic_stock_order_review_verified", report["status"])
        self.assertEqual(14, report["events"])
        self.assertEqual(4, report["executions"])
        self.assertEqual("5285", report["cash_delta_krw"])
        self.assertEqual(0, report["actual_broker_requests"])
        self.assertEqual(1, report["fake_http_requests"])
        self.assertTrue(all(report["checks"].values()))
        self.assertFalse(report["artifacts_retained"])
        self.assertEqual(before, set(self.directory.iterdir()))

    def test_retained_trial_reports_and_backup_replay_are_consistent(self):
        output = self.directory / "integrated-demo"
        result = self.run_cli(SMOKE, ["--output-dir", str(output)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0o700, stat.S_IMODE(output.stat().st_mode))
        review = json.loads((output / "review.json").read_text())
        difference = json.loads((output / "review-difference.json").read_text())
        self.assertEqual("observations_equal", review["comparison_status"])
        self.assertEqual("needs_review", difference["comparison_status"])
        restored = StockOrderJournal.open_existing(output / "restored.sqlite",
                                                   account_ref="demo-stock-orders", read_only=True)
        self.assertEqual(review["journal"], restored.snapshot())
        self.assertEqual({"journal.sqlite", "manifest.json"},
                         {item.name for item in (output / "backup").iterdir()})
        for name in ("review.json", "review.html", "review-difference.json", "synthetic-observations.json", "smoke.json"):
            path = output / name
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertNotIn("EXAMPLE-SECRET", path.read_text())
        self.assertIn("합성 데이터", (output / "review.html").read_text())
        self.assertEqual(2, self.run_cli(SMOKE, ["--output-dir", str(output)]).returncode)
        self.assertEqual(review, json.loads((output / "review.json").read_text()))


if __name__ == "__main__":
    unittest.main()
