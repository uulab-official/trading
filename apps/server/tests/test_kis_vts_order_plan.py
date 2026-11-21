"""Offline CLI isolation, exclusive output, permissions, and secret suppression."""

import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/kis_vts_order_plan.py"
sys.path.insert(0, str(ROOT))


class KisVtsOrderPlanCliTests(unittest.TestCase):
    def setUp(self):
        try:
            self.cli = importlib.import_module("tools.kis_vts_order_plan")
        except ModuleNotFoundError as error:
            if error.name != "tools.kis_vts_order_plan":
                raise
            self.fail("The offline KIS VTS order-plan CLI is not implemented")
        temporary = tempfile.TemporaryDirectory(prefix="offline kis plan ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "new plan.json"
        self.arguments = [
            "--mode",
            "paper",
            "--client-order-id",
            "local-stock-001",
            "--symbol",
            "005930",
            "--side",
            "buy",
            "--quantity",
            "2",
            "--limit-price",
            "70100",
            "--max-notional",
            "140200",
            "--output",
            str(self.target),
        ]

    def invoke(self, arguments=None):
        stdout, stderr = StringIO(), StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            patch("socket.socket.connect", side_effect=AssertionError("No socket")),
            patch("socket.create_connection", side_effect=AssertionError("No connection")),
            patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")),
        ):
            code = self.cli.main(self.arguments if arguments is None else arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def changed(self, option, value):
        arguments = self.arguments.copy()
        arguments[arguments.index(option) + 1] = value
        return arguments

    def test_new_plan_is_private_and_does_not_modify_existing_evidence(self):
        existing = {}
        for name in ("stock-ledger.json", "intent-journal.sqlite", "strategy-cursor.json"):
            path = self.directory / name
            path.write_bytes(b"existing evidence must stay byte-for-byte unchanged\n")
            existing[path] = path.read_bytes()
        old_umask = os.umask(0)
        try:
            code, stdout, stderr = self.invoke()
        finally:
            os.umask(old_umask)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("Offline", stdout)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        result = json.loads(self.target.read_text())
        self.assertEqual(result["mode"], "paper")
        self.assertIs(result["submitted"], False)
        self.assertEqual(result["order"]["notional"], "140200")
        self.assertEqual(set(self.directory.iterdir()), {*existing, self.target})
        for path, content in existing.items():
            self.assertEqual(path.read_bytes(), content)

    def test_actual_cli_runs_with_sockets_dns_and_site_packages_unavailable(self):
        guard = (
            "import runpy,socket,sys\n"
            "def forbidden(*args,**kwargs):\n"
            " raise AssertionError('Offline CLI forbids network')\n"
            "socket.socket.connect=forbidden\n"
            "socket.create_connection=forbidden\n"
            "socket.getaddrinfo=forbidden\n"
            "sys.argv=sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0],run_name='__main__')\n"
        )
        secret = "SYNTHETIC_SECRET_MUST_NOT_APPEAR"
        environment = {
            "PATH": os.defpath,
            "KIS_VTS_ACCESS_TOKEN": secret + "_TOKEN",
            "KIS_VTS_APPKEY": secret + "_KEY",
            "KIS_VTS_APPSECRET": secret + "_APPSECRET",
            "KIS_VTS_CANO": secret + "_ACCOUNT",
            "KIS_VTS_ACNT_PRDT_CD": secret + "_PRODUCT",
            "HTTP_PROXY": "http://invalid.invalid:8888",
            "HTTPS_PROXY": "http://invalid.invalid:8888",
        }
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-c", guard, str(SCRIPT), *self.arguments],
            cwd=self.directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        saved = self.target.read_text()
        self.assertNotIn(secret, saved + result.stdout + result.stderr)
        self.assertIn("${KIS_VTS_CANO}", saved)
        self.assertIs(json.loads(saved)["submitted"], False)

    def test_collision_preserves_existing_bytes_and_permissions(self):
        self.target.write_bytes(b"existing order evidence\n")
        self.target.chmod(0o640)
        before = self.target.stat()
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(self.target.read_bytes(), b"existing order evidence\n")
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(self.target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_existing_and_dangling_output_symlinks_are_preserved(self):
        destination = self.directory / "existing.json"
        destination.write_bytes(b"never replace this evidence")
        for target in (destination, self.directory / "missing.json"):
            with self.subTest(existing=target.exists()):
                self.target.symlink_to(target)
                code, stdout, stderr = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn("Traceback", stderr)
                self.assertTrue(self.target.is_symlink())
                self.assertEqual(self.target.readlink(), target)
                self.assertEqual(destination.read_bytes(), b"never replace this evidence")
                self.assertFalse((self.directory / "missing.json").exists())
                self.target.unlink()

    def test_invalid_inputs_do_not_create_a_plan(self):
        cases = (
            ("--mode", "live"),
            ("--symbol", "005930\n"),
            ("--side", "short"),
            ("--quantity", "0"),
            ("--quantity", "1.5"),
            ("--quantity", "True"),
            ("--quantity", "9" * 100000),
            ("--limit-price", "01"),
            ("--limit-price", "NaN"),
            ("--max-notional", "140199"),
            ("--client-order-id", "invalid\nclient"),
        )
        for option, value in cases:
            with self.subTest(option=option, value_length=len(value)):
                code, stdout, stderr = self.invoke(self.changed(option, value))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_paper_mode_and_budget_must_be_explicit(self):
        for option in ("--mode", "--max-notional", "--client-order-id", "--output"):
            arguments = self.arguments.copy()
            index = arguments.index(option)
            del arguments[index : index + 2]
            with self.subTest(option=option):
                code, stdout, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn("Traceback", stderr)
                self.assertFalse(self.target.exists())

    def test_argument_errors_do_not_echo_hostile_values_or_secret_paths(self):
        secret = "FAKE_SECRET_DO_NOT_ECHO"
        cases = (
            self.changed("--mode", secret),
            self.changed("--quantity", secret),
            self.arguments + ["--" + secret],
            self.changed("--output", str(self.directory / secret / "plan.json")),
        )
        for arguments in cases:
            with self.subTest(argument_count=len(arguments)):
                code, stdout, stderr = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(secret, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_missing_parent_directory_is_not_created_on_failure(self):
        code, stdout, stderr = self.invoke(
            self.changed("--output", str(self.directory / "missing/plan.json"))
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)
        self.assertFalse((self.directory / "missing").exists())

    def test_failed_write_removes_only_the_new_partial_plan(self):
        with patch.object(self.cli.os, "fsync", side_effect=OSError("SECRET_DISK_ERROR")):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("SECRET_DISK_ERROR", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_failed_write_does_not_remove_a_replacement_created_by_another_writer(self):
        def replace_then_fail(_descriptor):
            self.target.unlink()
            self.target.write_bytes(b"replacement evidence owned by another writer")
            raise OSError("SECRET_DISK_ERROR")

        with patch.object(self.cli.os, "fsync", side_effect=replace_then_fail):
            code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("SECRET_DISK_ERROR", stderr)
        self.assertEqual(self.target.read_bytes(), b"replacement evidence owned by another writer")


if __name__ == "__main__":
    unittest.main()
