"""Local checkpoint exclusion, cancellation and crash-recovery contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import trading_bot.paper_runner as runner_module
from trading_bot.paper_runner import PaperRunner
from test_paper_runner import rows, scripted

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = rows([(100, 100), (110, 120), (130, 130), (125, 125)])


def runner():
    return PaperRunner(strategy_factory=scripted({0: "buy", 2: "sell"}))


class PaperCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Checkpoint tests forbid network"))
            guard.start()
            self.addCleanup(guard.stop)

    def lock(self):
        self.assertTrue(hasattr(runner_module, "locked_checkpoint"),
                        "Paper checkpoint runs require exclusive writer ownership")
        return runner_module.locked_checkpoint

    async def test_concurrent_writer_cannot_roll_checkpoint_progress_backward(self):
        entered, release = threading.Event(), threading.Event()
        original = runner_module._atomic_checkpoint
        main_thread = threading.current_thread()

        def delayed_write(path, state):
            if threading.current_thread() is not main_thread and state["next_index"] == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Paused checkpoint writer timed out")
            return original(path, state)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            with ThreadPoolExecutor(max_workers=1) as pool, \
                 patch.object(runner_module, "_atomic_checkpoint", side_effect=delayed_write):
                first = pool.submit(asyncio.run, runner().run(FIXTURE, checkpoint_path=path, stop_after=1))
                try:
                    self.assertTrue(entered.wait(5), "First writer must hold its checkpoint")
                    with self.assertRaisesRegex(RuntimeError, "already in use"):
                        await runner().run(FIXTURE, checkpoint_path=path)
                    self.assertFalse(path.exists(), "Rejected writer cannot create a checkpoint")
                finally:
                    release.set()
                    first.result(timeout=5)
            self.assertEqual(json.loads(path.read_text())["next_index"], 1)
            expected = await runner().run(FIXTURE)
            resumed = await runner().run(FIXTURE, checkpoint_path=path)
            self.assertEqual(resumed, expected)
            self.assertEqual(json.loads(path.read_text())["next_index"], 4)

    async def test_alias_cannot_bypass_lock_or_construct_exchange(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            alias = Path(directory) / "alias.json"
            alias.symlink_to(path)
            with self.lock()(path), patch.object(runner_module, "PaperExchange", side_effect=AssertionError("Rejected writer must not start exchange")):
                with self.assertRaisesRegex(RuntimeError, "already in use"):
                    await runner().run(FIXTURE, checkpoint_path=alias)
            result = await runner().run(FIXTURE, checkpoint_path=alias)
            self.assertEqual(result["bars_processed"], 4)
            self.assertTrue(alias.is_symlink(), "Atomic saves must preserve the supplied alias")
            self.assertEqual(json.loads(path.read_text())["next_index"], 4)

    async def test_failed_write_preserves_saved_state_and_releases_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            await runner().run(FIXTURE, checkpoint_path=path, stop_after=1)
            before = path.read_bytes()
            with patch.object(runner_module, "_atomic_checkpoint", side_effect=OSError("simulated write failure")):
                with self.assertRaisesRegex(OSError, "write failure"):
                    await runner().run(FIXTURE, checkpoint_path=path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(await runner().run(FIXTURE, checkpoint_path=path), await runner().run(FIXTURE))

    async def test_cancellation_releases_lock_before_next_resume(self):
        entered = asyncio.Event()
        original = runner_module.PaperExchange.connect

        async def paused_connect(exchange):
            await original(exchange)
            entered.set()
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            with patch.object(runner_module.PaperExchange, "connect", paused_connect):
                task = asyncio.create_task(runner().run(FIXTURE, checkpoint_path=path))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=5)
                    with self.assertRaisesRegex(RuntimeError, "already in use"):
                        with self.lock()(path):
                            self.fail("Cancelled run still owns the checkpoint until it exits")
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            self.assertEqual(await runner().run(FIXTURE, checkpoint_path=path), await runner().run(FIXTURE))

    def test_process_death_releases_persistent_lock_file(self):
        lock = self.lock()
        script = """from unittest.mock import patch
import sys
with patch('socket.socket.connect',side_effect=AssertionError('No network')), patch('socket.create_connection',side_effect=AssertionError('No network')), patch('socket.getaddrinfo',side_effect=AssertionError('No DNS')):
 sys.path.insert(0,sys.argv[1])
 from trading_bot.paper_checkpoint import locked_checkpoint
 with locked_checkpoint(sys.argv[2]):
  print('locked',flush=True)
  sys.stdin.readline()
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            process = subprocess.Popen([sys.executable, "-u", "-c", script, str(ROOT / "apps/server"), str(path)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env={})
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0], "Child must acquire its lock")
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaisesRegex(RuntimeError, "already in use"):
                    with lock(path):
                        self.fail("The child owns this checkpoint")
                process.terminate()
                process.communicate(timeout=5)
                with lock(path) as canonical:
                    self.assertEqual(canonical, path.resolve())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)
            self.assertTrue(path.with_name(".paper.json.lock").exists(), "The shared lock inode must remain")

    async def test_symlink_lock_file_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            target = Path(directory) / "unrelated.txt"
            target.write_text("unchanged local sentinel")
            path.with_name(".paper.json.lock").symlink_to(target)
            with self.assertRaises(OSError):
                await runner().run(FIXTURE, checkpoint_path=path)
            self.assertEqual(target.read_text(), "unchanged local sentinel")
            self.assertFalse(path.exists())

    async def test_unsupported_locking_fails_before_checkpoint_or_exchange(self):
        self.lock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new" / "paper.json"
            with patch.dict(sys.modules, {"fcntl": None}), \
                 patch.object(runner_module, "PaperExchange", side_effect=AssertionError("No exchange startup")):
                with self.assertRaisesRegex(RuntimeError, "POSIX"):
                    await runner().run(FIXTURE, checkpoint_path=path)
            self.assertFalse(path.parent.exists())

    async def test_memory_replay_needs_no_lock_file_or_posix_dependency(self):
        with patch.dict(sys.modules, {"fcntl": None}), patch("os.open", side_effect=AssertionError("Memory replay needs no file lock")):
            result = await runner().run(FIXTURE)
        self.assertEqual(result["bars_processed"], 4)


if __name__ == "__main__":
    unittest.main()
