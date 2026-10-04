"""Single-SIGINT recovery through a locally installed console script.

The only injected behavior is an OS signal at a known atomic-save boundary and
a different time.time value. The ledger, checkpoint writer and installed CLI are real.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "config/examples/offline_fixture.json"

# sitecustomize also reaches pip's build subprocess. Never fetch dependencies or
# allow network/DNS in the installation or any paper-engine child process.
CHILD_GUARD = r'''
import json
import os
from pathlib import Path
import signal
import sys
import time

def forbid_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                 "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg"):
        raise AssertionError("Installed paper CLI checks forbid network/DNS")

sys.addaudithook(forbid_network)
if "PAPER_TEST_CLOCK" in os.environ:
    time.time = lambda: float(os.environ["PAPER_TEST_CLOCK"])

target = int(os.environ.get("PAPER_TEST_INTERRUPT_AT", "0"))
phase = os.environ.get("PAPER_TEST_INTERRUPT_PHASE", "after")
signal_count = int(os.environ.get("PAPER_TEST_SIGNAL_COUNT", "1"))
original_replace = os.replace
triggered = False

def interrupt():
    for _ in range(signal_count):
        os.kill(os.getpid(), signal.SIGINT)

def replace_and_interrupt(source, destination):
    global triggered
    selected = (not triggered and str(source).endswith(".tmp")
                and json.loads(Path(source).read_text())["next_index"] == target)
    if selected and phase == "before":
        triggered = True
        interrupt()
    original_replace(source, destination)
    if selected and phase == "after":
        triggered = True
        interrupt()

if target:
    os.replace = replace_and_interrupt
if phase == "fixture":
    original_read_text = Path.read_text
    def read_fixture_and_interrupt(path, *args, **kwargs):
        contents = original_read_text(path, *args, **kwargs)
        if path.name == "offline_fixture.json":
            interrupt()
        return contents
    Path.read_text = read_fixture_and_interrupt
'''


@unittest.skipUnless(os.name == "posix", "Checkpoint locking and SIGINT require POSIX")
class InstalledPaperShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        guard = root / "guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(CHILD_GUARD)
        source = root / "server"
        source.mkdir()
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(ROOT / "apps/server" / name, source / name)
        shutil.copytree(ROOT / "apps/server/trading_bot", source / "trading_bot",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        installed = root / "installed"
        build_env = {"PATH": os.defpath, "PYTHONPATH": str(guard),
                     "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        build = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
             "--no-build-isolation", "--no-compile", "--target", str(installed), str(source)],
            cwd=root, env=build_env, text=True, capture_output=True, timeout=60,
        )
        if build.returncode:
            raise AssertionError(f"Local offline CLI installation failed:\n{build.stdout}\n{build.stderr}")
        cls.launcher = installed / "bin/trading-paper"
        if not cls.launcher.is_file():
            raise AssertionError("Installation must generate the actual trading-paper console script")
        cls.env = {"PATH": os.defpath, "PYTHONPATH": os.pathsep.join(map(str, (
            guard, installed, ROOT / "packages/core", ROOT / "packages/data",
            ROOT / "packages/strategy"))), "PAPER_TEST_CLOCK": "946684800"}

    def launch(self, checkpoint, *, interrupt_at=0, phase="after", clock="946684800",
               signal_count=1):
        env = {**self.env, "PAPER_TEST_INTERRUPT_AT": str(interrupt_at),
               "PAPER_TEST_INTERRUPT_PHASE": phase, "PAPER_TEST_CLOCK": clock,
               "PAPER_TEST_SIGNAL_COUNT": str(signal_count)}
        return subprocess.run(
            [str(self.launcher), str(FIXTURE), "--checkpoint", str(checkpoint)],
            cwd=self.directory.name, env=env, text=True, capture_output=True, timeout=10,
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Paper replay interrupted", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "fixture_complete")
        self.assertEqual(report["bars_processed"], 90)
        return report

    def test_first_sigint_stops_at_committed_bar_and_resumes_without_duplicate_fills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full.json"
            expected = self.assert_success(self.launch(full))
            # Before/after the fixture's buy, after its sell, and the last bar.
            for index in (1, 57, 58, 79, 90):
                with self.subTest(index=index):
                    checkpoint = root / f"stop-{index}.json"
                    interrupted = self.launch(checkpoint, interrupt_at=index)
                    self.assertEqual(interrupted.returncode, 130, interrupted.stderr)
                    self.assertEqual(interrupted.stdout, "")
                    self.assertIn("Paper replay interrupted", interrupted.stderr)
                    self.assertNotIn("Traceback", interrupted.stderr)
                    saved = json.loads(checkpoint.read_text())
                    self.assertEqual(saved["next_index"], index,
                                     "The first SIGINT must not process another bar")
                    self.assertFalse(list(root.glob(".paper-*.tmp")))
                    resumed = self.assert_success(self.launch(checkpoint, clock="4102444800"))
                    self.assertEqual(resumed, expected)
                    self.assertEqual(checkpoint.read_bytes(), full.read_bytes())
                    ids = [trade["clientOrderId"] for trade in resumed["trades"]]
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertEqual(len(ids), 2)

    def test_first_sigint_during_atomic_write_preserves_a_resumable_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full.json"
            expected = self.assert_success(self.launch(full))
            checkpoint = root / "interrupted.json"
            interrupted = self.launch(checkpoint, interrupt_at=58, phase="before")
            self.assertEqual(interrupted.returncode, 130, interrupted.stderr)
            self.assertEqual(interrupted.stdout, "")
            self.assertNotIn("Traceback", interrupted.stderr)
            # Python 3.10 raises KeyboardInterrupt immediately; 3.11+ requests
            # cancellation and lets the synchronous atomic save finish first.
            expected_index = 58 if sys.version_info >= (3, 11) else 57
            self.assertEqual(json.loads(checkpoint.read_text())["next_index"], expected_index)
            self.assertFalse(list(root.glob(".paper-*.tmp")))
            self.assertEqual(self.assert_success(self.launch(checkpoint)), expected)
            self.assertEqual(checkpoint.read_bytes(), full.read_bytes())


    def test_sigint_before_replay_saves_nothing_and_retry_starts_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "early.json"
            interrupted = self.launch(checkpoint, phase="fixture")
            self.assertEqual(interrupted.returncode, 130, interrupted.stderr)
            self.assertEqual(interrupted.stdout, "")
            self.assertIn("Paper replay interrupted", interrupted.stderr)
            self.assertNotIn("Traceback", interrupted.stderr)
            self.assertFalse(checkpoint.exists())
            self.assertEqual(self.assert_success(self.launch(checkpoint)),
                             self.assert_success(self.launch(root / "full.json")))

    def test_second_sigint_before_replace_preserves_previous_bar_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "forced.json"
            interrupted = self.launch(checkpoint, interrupt_at=58, phase="before", signal_count=2)
            self.assertEqual(interrupted.returncode, 130, interrupted.stderr)
            self.assertEqual(interrupted.stdout, "")
            self.assertIn("Paper replay interrupted", interrupted.stderr)
            self.assertNotIn("Traceback", interrupted.stderr)
            self.assertEqual(json.loads(checkpoint.read_text())["next_index"], 57)
            self.assertFalse(list(root.glob(".paper-*.tmp")))
            full = root / "full.json"
            self.assertEqual(self.assert_success(self.launch(checkpoint)),
                             self.assert_success(self.launch(full)))
            self.assertEqual(checkpoint.read_bytes(), full.read_bytes())


if __name__ == "__main__":
    unittest.main()
