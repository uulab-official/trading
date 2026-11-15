"""Release CLI boundaries: one result, private failures and real signal intent."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/paper_release.py"
DIGEST = "a" * 64


def load_cli():
    if not SCRIPT.is_file():
        raise AssertionError("The verified PAPER release CLI is not implemented")
    spec = importlib.util.spec_from_file_location("paper_release_cli_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def successful(operation):
    return {
        "schema_version": 1,
        "source": "paper_release",
        "operation": operation,
        "status": {"build": "built", "check": "verified_archive",
                   "extract": "extracted", "verify": "verified_tree"}[operation],
        "exit_code": 0,
        "archive_sha256": DIGEST,
        "manifest_sha256": "b" * 64,
        "execution_fingerprint": "c" * 64,
        "file_count": 55,
        "payload_bytes": 12345,
        "boundaries": {"live_execution": False, "service_activation": False,
                       "interpreter_packaged": False, "data_readiness": "not_assessed"},
    }


@contextmanager
def release_boundary(callback):
    # Only the filesystem API is doubled here. The parser, signal handlers,
    # serialization, stdout and public error policy are the real CLI behavior.
    package = types.ModuleType("trading_bot")
    module = types.ModuleType("trading_bot.paper_release")

    class ReleaseUnavailable(ValueError):
        pass

    class ReleaseStorageError(OSError):
        pass

    class ReleaseInterrupted(Exception):
        def __init__(self, signum):
            self.signum = signum

    module.ReleaseUnavailable = ReleaseUnavailable
    module.ReleaseStorageError = ReleaseStorageError
    module.ReleaseInterrupted = ReleaseInterrupted
    module.build_release = lambda output, *, stop=None: callback(
        "build", module, stop, {"output": output})
    module.check_release = lambda archive, *, expected_sha256, stop=None: callback(
        "check", module, stop, {"archive": archive, "sha256": expected_sha256})
    module.extract_release = lambda archive, directory, *, expected_sha256, stop=None: callback(
        "extract", module, stop,
        {"archive": archive, "directory": directory, "sha256": expected_sha256})
    module.verify_release = lambda directory, *, expected_sha256, stop=None: callback(
        "verify", module, stop, {"directory": directory, "sha256": expected_sha256})
    package.paper_release = module
    with patch.dict(sys.modules, {"trading_bot": package, "trading_bot.paper_release": module}):
        yield


class PaperReleaseCliTests(unittest.TestCase):
    def invoke(self, args, callback):
        cli = load_cli()
        before = {value: signal.getsignal(value) for value in (signal.SIGINT, signal.SIGTERM)}
        with release_boundary(callback), redirect_stdout(io.StringIO()) as stdout, \
                redirect_stderr(io.StringIO()) as stderr:
            code = cli.main(args)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertNotIn("SECRET", stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(before, {value: signal.getsignal(value) for value in before})
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["exit_code"], code)
        self.assertEqual(value["source"], "paper_release")
        return code, value, stderr.getvalue()

    def test_invalid_arguments_never_enter_an_operation(self):
        cases = [[], ["--help"], ["build"], ["build", "--out", "target"],
                 ["build", "--output", "one", "--output", "two"],
                 ["build", "--output", "target", "--force"],
                 ["check", "archive"], ["verify", "directory"],
                 ["extract", "archive", "--sha256", DIGEST],
                 ["run", "directory"], ["check", "archive", "--sha256", "A" * 64],
                 ["check", "archive", "--sha256", "a" * 63],
                 ["check", "archive", "--sha256", DIGEST, "--sha256", DIGEST],
                 ["extract", "archive", "--sha256", DIGEST, "--directory", "a", "--directory", "b"],
                 ["verify", "directory", "--sha256", DIGEST, "extra"],
                 ["check", "archive", "--sha256", DIGEST, "--help"]]

        def forbidden(*args):
            self.fail("Invalid CLI input reached filesystem operations")

        for args in cases:
            with self.subTest(args=args):
                code, report, _ = self.invoke(args, forbidden)
                self.assertEqual(code, 2)
                self.assertEqual(report["error"], "paper_release_arguments_invalid")

    def test_exact_command_dispatch_preserves_paths_and_external_digest(self):
        cases = [
            (["build", "--output", "new.tar"], "build", {"output": "new.tar"}),
            (["check", "archive.tar", "--sha256", DIGEST], "check",
             {"archive": "archive.tar", "sha256": DIGEST}),
            (["extract", "archive.tar", "--sha256", DIGEST, "--directory", "new-tree"], "extract",
             {"archive": "archive.tar", "sha256": DIGEST, "directory": "new-tree"}),
            (["verify", "tree", "--sha256", DIGEST], "verify",
             {"directory": "tree", "sha256": DIGEST}),
        ]
        for args, operation, expected in cases:
            with self.subTest(operation=operation):
                def perform(actual, errors, stop, fields):
                    self.assertEqual(actual, operation)
                    self.assertEqual(fields, expected)
                    self.assertIs(type(stop()), int)
                    self.assertEqual(stop(), 0)
                    return successful(actual)

                code, report, _ = self.invoke(args, perform)
                self.assertEqual(code, 0)
                self.assertEqual(report["status"], successful(operation)["status"])
                self.assertFalse(report["boundaries"]["live_execution"])

    def test_operation_failures_have_fixed_private_codes(self):
        for name, expected in (("ReleaseUnavailable", 2), ("ReleaseStorageError", 4),
                               ("unexpected", 4), ("interrupted", 143)):
            with self.subTest(name=name):
                def perform(operation, errors, stop, fields):
                    if name == "interrupted":
                        raise errors.ReleaseInterrupted(15)
                    if name == "unexpected":
                        raise RuntimeError("SECRET diagnostic path")
                    raise getattr(errors, name)("SECRET diagnostic path")

                code, report, _ = self.invoke(["build", "--output", "SECRET-target"], perform)
                self.assertEqual(code, expected)
                self.assertIn("error", report)

    def test_actual_signals_keep_first_numeric_intent_and_restore_handlers(self):
        for first, second in ((signal.SIGINT, signal.SIGTERM), (signal.SIGTERM, signal.SIGINT)):
            with self.subTest(first=first):
                def perform(operation, errors, stop, fields):
                    os.kill(os.getpid(), first)
                    os.kill(os.getpid(), second)
                    self.assertIs(type(stop()), int)
                    self.assertEqual(stop(), int(first))
                    raise errors.ReleaseInterrupted(stop())

                code, report, _ = self.invoke(["build", "--output", "new.tar"], perform)
                self.assertEqual(code, 128 + int(first))
                self.assertEqual(report["status"], "interrupted")

    def test_storage_uncertainty_takes_priority_over_actual_signal(self):
        def perform(operation, errors, stop, fields):
            os.kill(os.getpid(), signal.SIGTERM)
            raise errors.ReleaseStorageError("SECRET output may exist")

        code, report, _ = self.invoke(["build", "--output", "new.tar"], perform)
        self.assertEqual(code, 4)
        self.assertEqual(report["error"], "paper_release_storage_unconfirmed")

    def test_malformed_or_excessive_results_cannot_be_reported_as_success(self):
        values = [dict(successful("build"), exit_code=True),
                  dict(successful("build"), archive_sha256=float("nan")),
                  dict(successful("build"), unexpected="SECRET" * 10000)]
        for value in values:
            with self.subTest(kind=type(value.get("archive_sha256")).__name__):
                code, report, _ = self.invoke(["build", "--output", "target"],
                    lambda *args: value)
                self.assertEqual(code, 4)
                self.assertEqual(report["status"], "failed")

    def test_signal_during_json_encoding_cannot_erase_storage_uncertainty(self):
        cli = load_cli()
        original = json.dumps
        signalled = []

        def encode(value, **kwargs):
            if not signalled:
                signalled.append(True)
                os.kill(os.getpid(), signal.SIGINT)
            return original(value, **kwargs)

        def perform(operation, errors, stop, fields):
            raise errors.ReleaseStorageError("SECRET")

        with release_boundary(perform), redirect_stdout(io.StringIO()) as output, \
                redirect_stderr(io.StringIO()), patch.object(cli.json, "dumps", encode):
            code = cli.main(["build", "--output", "target"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(output.getvalue())["exit_code"], 4)

    def test_broken_and_full_stdout_are_private_delivery_failures(self):
        load_cli()  # RED is an absent command, not an unrelated subprocess error.
        for kind in ("broken", "full"):
            with self.subTest(kind=kind):
                if kind == "full":
                    fd = os.open("/dev/full", os.O_WRONLY)
                else:
                    reader, fd = os.pipe()
                    os.close(reader)
                try:
                    process = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), "--help"],
                        stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.PIPE, timeout=10)
                finally:
                    os.close(fd)
                self.assertEqual(process.returncode, 4, process.stderr)
                self.assertEqual(process.stderr, b"paper_release_output_unconfirmed\n")

    def test_subprocess_argument_error_is_one_json_without_traceback(self):
        load_cli()
        process = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), "--secret"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertEqual(json.loads(process.stdout)["error"], "paper_release_arguments_invalid")
        self.assertEqual(process.stderr, b"")

    def test_deep_unavailable_path_is_preoutput_refusal_in_actual_cli(self):
        archive = '/' + 'a/' * 1100 + 'archive.tar'
        process = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCRIPT), 'check', archive,
             '--sha256', '0' * 64], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(len(process.stdout.splitlines()), 1)
        self.assertEqual(json.loads(process.stdout)['error'], 'paper_release_unavailable')
        self.assertEqual(process.stderr, b'')


if __name__ == "__main__":
    unittest.main()
