"""The all-paper entrypoint composes real ledgers, audits and retained reports."""

import importlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
CLI = ROOT / "tools/paper_lab.py"
CLOCK = datetime(2026, 11, 21, 12, 30, tzinfo=timezone.utc)


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PaperLabTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)

    def lab(self):
        self.assertTrue(CLI.is_file(), "Combined paper lab is not implemented")
        return importlib.import_module("tools.paper_lab")

    def test_retained_lab_has_three_audited_books_and_recovery_evidence(self):
        output = self.root / "run"
        with patch.dict(
            os.environ,
            {"TOSS_ACCESS_TOKEN": "DO-NOT-PRINT-SECRET", "KIS_VTS_CANO": "DO-NOT-PRINT-ACCOUNT"},
        ):
            result = self.lab().run_lab(output, now=CLOCK)
        self.assertEqual("synthetic_paper_lab_verified", result["status"])
        self.assertEqual("paper", result["mode"])
        self.assertEqual("2026-11-21T12:30:00+00:00", result["generated_at_utc"])
        self.assertIs(result["artifacts_retained"], True)
        self.assertEqual(0, result["actual_broker_requests"])
        for flag in ("live_orders_enabled", "safe_to_submit", "account_binding_verified"):
            self.assertIs(result[flag], False)
        self.assertIsNone(result["cross_portfolio_total"])
        self.assertEqual(3, result["portfolio_count"])
        self.assertTrue(result["checks"])
        self.assertTrue(all(value is True for value in result["checks"].values()))
        self.assertEqual(result, json.loads((output / "summary.json").read_text()))

        portfolios = json.loads((output / "portfolios.json").read_text())
        self.assertIsNone(portfolios["cross_portfolio_total"])
        cards = portfolios["portfolios"]
        self.assertEqual(["crypto", "stock", "stock"], [item["type"] for item in cards])
        self.assertEqual([90, 55, 55], [item["processed_bars"] for item in cards])
        self.assertEqual(["KRW", "USD"], [item["quote_currency"] for item in cards[1:]])
        self.assertEqual(["005930", "DEMO"], [item["symbol"] for item in cards[1:]])
        for name in ("crypto", "stock-krw", "stock-usd"):
            self.assertTrue((output / name / "backup/manifest.json").is_file())
            self.assertTrue((output / name / "report.html").is_file())
            self.assertTrue((output / name / "audit.json").is_file())
        self.assertTrue((output / "order-review/restored.sqlite").is_file())
        review = json.loads((output / "order-review/review-difference.json").read_text())
        self.assertEqual("needs_review", review["comparison_status"])
        changed = {item["field"] for row in review["orders"] for item in row["differences"]}
        self.assertEqual({"filled_quantity", "gross_krw"}, changed)
        lifecycle = json.loads((output / "order-lifecycle/report.json").read_text())
        self.assertEqual(14, lifecycle["summary"]["recorded_events"])
        self.assertEqual(4, lifecycle["summary"]["executions"])
        self.assertTrue(all(value is True for value in lifecycle["summary"]["checks"].values()))
        for path in output.rglob("*"):
            if path.is_file() and path.suffix in (".json", ".html"):
                self.assertNotIn("DO-NOT-PRINT", path.read_text())
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(output.stat().st_mode))

    def test_index_links_resolve_locally_and_has_no_executable_resources(self):
        output = self.root / "run"
        self.lab().run_lab(output, now=CLOCK)
        page = (output / "index.html").read_text()
        parsed = Document()
        parsed.feed(page)
        links = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
        self.assertGreaterEqual(len(links), 8)
        for link in links:
            self.assertNotIn(":", link)
            self.assertFalse(link.startswith(("/", "..")))
            self.assertTrue((output / link).is_file(), link)
        self.assertFalse(
            any(tag in ("script", "iframe", "img", "form", "link") for tag, _ in parsed.tags)
        )
        self.assertTrue(
            any(tag == "html" and attrs.get("lang") == "ko" for tag, attrs in parsed.tags)
        )
        csp = next(
            attrs["content"]
            for tag, attrs in parsed.tags
            if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
        )
        self.assertIn("default-src 'none'", csp)
        self.assertIn("form-action 'none'", csp)
        self.assertIn("합성", page)
        self.assertIn("2026-11-21 21:30:00 KST", page)
        self.assertIn("인증", page)
        for report_page in output.rglob("*.html"):
            contents = report_page.read_text()
            self.assertTrue(
                "합성" in contents or "synthetic" in contents.lower(),
                "Standalone synthetic context missing from " + report_page.name,
            )

    def test_default_is_temporary_and_restores_runtime_network_functions(self):
        lab = self.lab()
        connect, resolve = socket.socket.connect, socket.getaddrinfo
        before = set(self.root.iterdir())
        result = lab.run_lab(now=CLOCK)
        self.assertIs(result["artifacts_retained"], False)
        self.assertTrue(
            all(
                component.get("artifacts_retained", False) is False
                for component in result["components"].values()
            )
        )
        self.assertEqual(before, set(self.root.iterdir()))
        self.assertIs(socket.socket.connect, connect)
        self.assertIs(socket.getaddrinfo, resolve)

    def test_existing_file_directory_and_dangling_symlink_are_preserved(self):
        lab = self.lab()
        existing_file = self.root / "file"
        existing_file.write_text("original")
        existing_directory = self.root / "directory"
        existing_directory.mkdir()
        (existing_directory / "original").write_text("keep")
        link = self.root / "link"
        link.symlink_to(self.root / "absent-target")
        for path in (existing_file, existing_directory, link):
            with self.subTest(path=path.name), self.assertRaises((ValueError, OSError)):
                lab.run_lab(path, now=CLOCK)
        self.assertEqual("original", existing_file.read_text())
        self.assertEqual(["original"], [path.name for path in existing_directory.iterdir()])
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / "absent-target").exists())

    def test_bad_clock_is_refused_before_destination_creation(self):
        for clock in ("today", datetime(2026, 11, 21), True):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                self.lab().run_lab(self.root / "new", now=clock)
            self.assertFalse((self.root / "new").exists())

    def test_failed_component_keeps_evidence_but_has_no_success_markers(self):
        lab = self.lab()
        output = self.root / "failed"
        connect, resolve = socket.socket.connect, socket.getaddrinfo
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(lab, "run_stock_smoke", side_effect=ValueError("PRIVATE-ERROR-VALUE")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = lab.main(["--output-dir", str(output)])
        self.assertEqual(2, code)
        self.assertEqual("", stdout.getvalue())
        self.assertNotIn("PRIVATE-ERROR-VALUE", stderr.getvalue())
        self.assertTrue((output / "crypto/checkpoint.json").is_file())
        self.assertFalse((output / "summary.json").exists())
        self.assertFalse((output / "index.html").exists())
        self.assertIs(socket.socket.connect, connect)
        self.assertIs(socket.getaddrinfo, resolve)

    def test_attempted_dns_from_component_is_refused_and_guard_is_restored(self):
        lab = self.lab()
        resolve = socket.getaddrinfo

        def unexpected_network(*args, **kwargs):
            return socket.getaddrinfo("network-must-never-run.invalid", 443)

        with patch.object(lab, "run_stock_smoke", side_effect=unexpected_network):
            with self.assertRaises((ValueError, RuntimeError)):
                lab.run_lab(self.root / "failed", now=CLOCK)
        self.assertIs(socket.getaddrinfo, resolve)
        self.assertFalse((self.root / "failed/summary.json").exists())

    def test_reverse_dns_is_guarded_and_restored_without_calling_a_resolver(self):
        lab = self.lab()

        def harmless_resolver(*args, **kwargs):
            return ("unexpected-local-stub", "443")

        with patch.object(socket, "getnameinfo", harmless_resolver):
            with lab._without_network() as attempted:
                with self.assertRaises(RuntimeError):
                    socket.getnameinfo(("127.0.0.1", 443), 0)
                self.assertEqual(1, attempted[0])
            self.assertIs(socket.getnameinfo, harmless_resolver)

    def test_caught_network_attempt_during_render_cannot_publish_success(self):
        lab = self.lab()
        render = lab.render_index

        def attempted_but_caught(summary):
            try:
                socket.getaddrinfo("network-must-never-run.invalid", 443)
            except RuntimeError:
                pass
            return render(summary)

        with patch.object(lab, "render_index", side_effect=attempted_but_caught):
            with self.assertRaises(ValueError):
                lab.run_lab(self.root / "failed", now=CLOCK)
        self.assertFalse((self.root / "failed/summary.json").exists())
        self.assertFalse((self.root / "failed/index.html").exists())

    def test_late_report_failure_removes_both_top_level_success_markers(self):
        lab = self.lab()
        output = self.root / "failed"
        real_write = lab.write_reports
        real_fsync = os.fsync

        def fail_last_report(outputs):
            if any(Path(path).name == "index.html" for path, unused in outputs):
                flushed = [0]

                def failed_final_flush(descriptor):
                    flushed[0] += 1
                    if flushed[0] == len(outputs):
                        raise OSError("Injected final index flush failure")
                    return real_fsync(descriptor)

                with patch("tools.stock_order_review.os.fsync", side_effect=failed_final_flush):
                    return real_write(outputs)
            return real_write(outputs)

        with patch.object(lab, "write_reports", side_effect=fail_last_report):
            with self.assertRaises(OSError):
                lab.run_lab(output, now=CLOCK)
        self.assertTrue((output / "order-review/restored.sqlite").is_file())
        self.assertFalse((output / "summary.json").exists())
        self.assertFalse((output / "index.html").exists())

    def test_report_close_failure_cleans_success_markers_without_reclosing_fds(self):
        lab = self.lab()
        real_write, real_close = lab.write_reports, os.close
        for failed_name in ("broker-readiness.json", "index.html"):
            output = self.root / failed_name.replace(".", "-")
            closed = []

            def final_write(outputs):
                if not any(Path(path).name == "index.html" for path, unused in outputs):
                    return real_write(outputs)
                failed_identity = [None]

                def close_then_report_failure(descriptor):
                    identity = os.fstat(descriptor)
                    if failed_identity[0] is None:
                        failed_identity[0] = (output / failed_name).stat()
                    failure_identity = failed_identity[0]
                    fail = (identity.st_dev, identity.st_ino) == (
                        failure_identity.st_dev,
                        failure_identity.st_ino,
                    )
                    self.assertNotIn(descriptor, closed, "A failed close was retried")
                    closed.append(descriptor)
                    real_close(descriptor)
                    if fail:
                        raise OSError("Injected close error with PRIVATE-DIAGNOSTIC")

                with patch(
                    "tools.stock_order_review.os.close", side_effect=close_then_report_failure
                ):
                    return real_write(outputs)

            with (
                self.subTest(failed_name=failed_name),
                patch.object(lab, "write_reports", side_effect=final_write),
            ):
                with self.assertRaises(OSError):
                    lab.run_lab(output, now=CLOCK)
                self.assertEqual(5, len(closed), "Unclosed report descriptors remain")
                self.assertFalse((output / "summary.json").exists())
                self.assertFalse((output / "index.html").exists())

    def test_index_escapes_values_and_refuses_nonpaper_success_claim(self):
        lab = self.lab()
        report = lab.run_lab(now=CLOCK)
        changed = deepcopy(report)
        changed["components"]["crypto"]["symbol"] = "<script>alert('bad')</script>"
        page = lab.render_index(changed)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        for key, value in (
            ("live_orders_enabled", True),
            ("safe_to_submit", True),
            ("status", "failed"),
            ("cross_portfolio_total", "123"),
        ):
            changed = deepcopy(report)
            changed[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                lab.render_index(changed)

    def test_actual_cli_from_another_directory_and_sanitized_bad_option(self):
        self.lab()
        result = subprocess.run(
            [sys.executable, "-B", str(CLI)],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("synthetic_paper_lab_verified", json.loads(result.stdout)["status"])
        failed = subprocess.run(
            [sys.executable, "-B", str(CLI), "--PRIVATE-ARG"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(2, failed.returncode)
        self.assertNotIn("PRIVATE-ARG", failed.stdout + failed.stderr)
        self.assertEqual([], list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
