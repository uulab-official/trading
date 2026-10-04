"""Real subprocess entry-point contracts with deterministic offline fixtures."""

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from trading_bot.paper_checkpoint import locked_checkpoint
from trading_bot.paper_runner import main

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "config/examples/offline_fixture.json"

# Execute the configured packaging target with the same sys.exit contract used
# by generated console scripts, or run the actual module. Guard the child too:
# mocks in the test process do not prohibit child-process network access.
LAUNCH = r'''
from importlib.metadata import EntryPoint
import os
import runpy
import signal
import sys
from unittest.mock import patch
root, mode, target, interrupt, *args = sys.argv[1:]
for path in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, os.path.join(root, path))
sys.argv = ["trading-paper", *args]
original_replace = os.replace

def interrupt_after_save(source, destination):
    original_replace(source, destination)
    # asyncio defers the first SIGINT as task cancellation; a second signal
    # exercises its immediate KeyboardInterrupt path after this atomic save.
    os.kill(os.getpid(), signal.SIGINT)
    os.kill(os.getpid(), signal.SIGINT)

with patch("socket.socket.connect", side_effect=AssertionError("No network")), \
     patch("socket.create_connection", side_effect=AssertionError("No network")), \
     patch("socket.getaddrinfo", side_effect=AssertionError("No DNS")), \
     patch("os.replace", interrupt_after_save if interrupt == "yes" else original_replace):
    if mode == "console":
        sys.exit(EntryPoint("trading-paper", target, "console_scripts").load()())
    runpy.run_module("trading_bot.paper_runner", run_name="__main__")
'''


class PaperCliTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("CLI tests forbid network"))
            guard.start()
            self.addCleanup(guard.stop)
        config = (ROOT / "apps/server/pyproject.toml").read_text()
        self.target = re.search(r'^trading-paper\s*=\s*"([^"]+)"', config, re.M).group(1)

    def launch(self, mode, *args, interrupt=False):
        return subprocess.run(
            [sys.executable, "-c", LAUNCH, str(ROOT), mode, self.target,
             "yes" if interrupt else "no", *map(str, args)],
            cwd=ROOT, env={}, text=True, capture_output=True, timeout=10,
        )

    def assert_expected_failure(self, result, code, message):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_console_and_module_success_resume_output_and_exit_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "paper.json"
            first = self.launch("console", FIXTURE, "--checkpoint", checkpoint)
            self.assertEqual(first.returncode, 0, first.stderr)
            saved = checkpoint.read_bytes()
            resumed = self.launch("module", FIXTURE, "--checkpoint", checkpoint)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(first.stdout, resumed.stdout)
            self.assertEqual(first.stderr, resumed.stderr)
            self.assertNotIn("{'mode'", first.stderr)
            result = json.loads(first.stdout)
            self.assertEqual(result["status"], "fixture_complete")
            self.assertEqual(result["bars_processed"], 90)
            self.assertEqual(checkpoint.read_bytes(), saved)
            with patch("sys.stdout"):
                self.assertEqual(main([str(FIXTURE), "--checkpoint", str(checkpoint)]), result)

    def test_invalid_fixture_shapes_fail_cleanly_without_saving(self):
        examples = ["{", "[]", '{}', '{"mode":"live","candles":[]}',
                    '{"symbol":42,"candles":[[1,1,1,1,1,1]]}',
                    '{"candles":"private-sentinel"}', '{"candles":[[1,2]]}',
                    '{"candles":[[0,100,90,100,100,1]]}',
                    json.dumps({'candles': [[10**20, 100, 100, 100, 100, 1]]}),
                    json.dumps({'candles': [[10**400, 100, 100, 100, 100, 1]]})]
        with tempfile.TemporaryDirectory() as directory:
            fixture, checkpoint = Path(directory) / "fixture.json", Path(directory) / "paper.json"
            for mode in ("console", "module"):
                for content in examples:
                    with self.subTest(mode=mode, content=content):
                        fixture.write_text(content)
                        result = self.launch(mode, fixture, "--checkpoint", checkpoint)
                        self.assert_expected_failure(result, 2, "Invalid paper input")
                        self.assertNotIn("private-sentinel", result.stderr)
                        self.assertFalse(checkpoint.exists())

    def test_invalid_numeric_option_and_help_have_standard_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "paper.json"
            for mode in ("console", "module"):
                result = self.launch(mode, FIXTURE, "--checkpoint", checkpoint, "--fee-pct", "private-sentinel")
                self.assert_expected_failure(result, 2, "decimal number")
                self.assertNotIn("private-sentinel", result.stderr)
                help_result = self.launch(mode, "--help")
                self.assertEqual(help_result.returncode, 0)
                self.assertIn("--checkpoint", help_result.stdout)
                self.assertFalse(checkpoint.exists())

    def test_corrupt_checkpoint_has_recovery_guidance_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "paper.json"
            for content in ("{", "[]", '{"version":"private-sentinel"}'):
                checkpoint.write_text(content)
                for mode in ("console", "module"):
                    result = self.launch(mode, FIXTURE, "--checkpoint", checkpoint)
                    self.assert_expected_failure(result, 2, "Invalid paper input")
                    self.assertIn("original", result.stderr)
                    self.assertIn("new checkpoint path", result.stderr)
                    self.assertNotIn("private-sentinel", result.stderr)
                    self.assertEqual(checkpoint.read_text(), content)

    def test_busy_checkpoint_has_retry_guidance_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "paper.json"
            self.assertEqual(self.launch("console", FIXTURE, "--checkpoint", checkpoint).returncode, 0)
            before = checkpoint.read_bytes()
            alias = Path(directory) / "alias.json"
            alias.symlink_to(checkpoint)
            with locked_checkpoint(checkpoint):
                for mode in ("console", "module"):
                    result = self.launch(mode, FIXTURE, "--checkpoint", alias)
                    self.assert_expected_failure(result, 3, "Checkpoint is in use")
                    self.assertIn("retry", result.stderr)
                    self.assertIn("Do not delete", result.stderr)
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertTrue(alias.is_symlink())

    def test_filesystem_error_reports_local_path_and_permission_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("console", "module"):
                result = self.launch(mode, Path(directory) / "missing.json", "--checkpoint", Path(directory) / "paper.json")
                self.assert_expected_failure(result, 4, "Local file access failed")
                self.assertIn("paths and permissions", result.stderr)

    def test_sigint_after_atomic_save_exits_130_and_resumes_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = self.launch("console", FIXTURE, "--checkpoint", Path(directory) / "full.json")
            for mode in ("console", "module"):
                checkpoint = Path(directory) / f"{mode}.json"
                result = self.launch(mode, FIXTURE, "--checkpoint", checkpoint, interrupt=True)
                self.assert_expected_failure(result, 130, "Paper replay interrupted")
                self.assertIn("rerun the same command", result.stderr)
                self.assertEqual(json.loads(checkpoint.read_text())["next_index"], 1)
                resumed = self.launch(mode, FIXTURE, "--checkpoint", checkpoint)
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(resumed.stdout, expected.stdout)

    def test_unexpected_programming_error_is_not_reclassified(self):
        from trading_bot import paper_runner
        self.assertTrue(hasattr(paper_runner, "cli"))
        with patch.object(paper_runner, "main", side_effect=RuntimeError("unexpected implementation error")):
            with self.assertRaisesRegex(RuntimeError, "unexpected implementation error"):
                paper_runner.cli([])
