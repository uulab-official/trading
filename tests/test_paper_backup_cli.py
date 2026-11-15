"""Backup CLI boundaries and real subprocess signals; no provider access."""
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/paper_backup.py"


def load_cli():
    if not SCRIPT.exists():
        raise AssertionError("The paper backup CLI has not been implemented")
    spec = importlib.util.spec_from_file_location("paper_backup_cli_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(operation):
    return {
        "schema_version": 1,
        "source": "paper_backup_operation",
        "operation": operation,
        "mode": "paper",
        "live_enabled": False,
        "read_only": operation == "check",
        "status": "verified_restore" if operation == "verify-restore" else "verified_backup",
        "exit_code": 0,
        "backup_id": "a" * 32,
        "stream_id": "b" * 32,
        "capture_id": "c" * 32,
        "events_verified": 2,
        "engine_sha256": "1" * 64,
        "event_head_sha256": "2" * 64,
        "payloads": {
            "account-database.bin": {"size": 4096, "sha256": "3" * 64},
            "capture-database.bin": {"size": 4096, "sha256": "4" * 64},
        },
        "boundaries": {
            "verification": "full_archived_replay",
            "execution_authorized": False,
            "source_rebinding": False,
            "rollback_protection": False,
            "provider_authenticity": "not_authenticated",
        },
    }


@contextmanager
def runtime_double(callback):
    # Only the concurrently implemented core boundary is replaced. The parser,
    # dispatch, signal installation/restoration and stdout handling remain real.
    package = types.ModuleType("trading_bot")
    runtime = types.ModuleType("trading_bot.paper_backup")
    errors = types.ModuleType("trading_bot.paper_backup_io")

    class BackupUnavailable(ValueError):
        pass

    class BackupStorageError(OSError):
        pass

    class BackupInterrupted(Exception):
        def __init__(self, signum):
            self.signum = signum

    errors.BackupUnavailable = BackupUnavailable
    errors.BackupStorageError = BackupStorageError
    errors.BackupInterrupted = BackupInterrupted

    async def create(account_directory, output, *, stop):
        if account_directory != "ACCOUNT" or output != "NEW":
            raise AssertionError("Incorrect create paths")
        return await callback("create", stop, errors)

    async def check(directory, *, stop):
        if directory != "BUNDLE":
            raise AssertionError("Incorrect check path")
        return await callback("check", stop, errors)

    async def restore(directory, output, *, stop):
        if directory != "BUNDLE" or output != "NEW":
            raise AssertionError("Incorrect restore paths")
        return await callback("verify-restore", stop, errors)

    runtime.create_backup = create
    runtime.check_backup = check
    runtime.verify_restore = restore
    package.paper_backup = runtime
    package.paper_backup_io = errors
    modules = {"trading_bot": package, "trading_bot.paper_backup": runtime,
               "trading_bot.paper_backup_io": errors}
    with patch.dict(sys.modules, modules):
        yield


class PaperBackupCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, args):
        command = [sys.executable, "-c", r'''
import runpy,socket,sys
def forbidden(*args,**kwargs):raise AssertionError('network forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
''', str(SCRIPT), *args]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertTrue(completed.stdout.startswith("{"), "CLI did not emit a JSON result")
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertNotIn("SECRET", completed.stdout + completed.stderr)
        return completed.returncode, json.loads(completed.stdout)

    def call_double(self, args, callback):
        cli = load_cli()
        with runtime_double(callback), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err:
            code = cli.main(args)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertNotIn("SECRET", out.getvalue() + err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_invalid_arguments_are_private_and_never_create_output(self):
        source = str(self.root / "SECRET-source")
        destination = str(self.root / "SECRET-output")
        cases = [
            [], ["create"], ["check"], ["verify-restore"],
            ["create", source], ["verify-restore", source],
            ["create", source, "--out", destination],
            ["create", source, "--output", destination, "--output", "other"],
            ["create", source, "--output", destination, "--live"],
            ["create", source, "--output", destination, "--credentials", "SECRET"],
            ["create", source, "--output", destination, "--rebind"],
            ["check", source, "--output", destination],
            ["check", source, "--repair"],
            ["verify-restore", source, "--output", destination, "--start"],
            ["verify-restore", source, "--output", destination, "--force"],
            ["verify-restore", source, "--output", destination, "--source", "other"],
            ["restore", source, "--output", destination],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, report = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(report, {"error": "paper_backup_arguments_invalid"})
        self.assertFalse(Path(source).exists())
        self.assertFalse(Path(destination).exists())

    def test_dispatch_preserves_full_result_and_only_emits_one_json(self):
        async def complete(operation, stop, errors):
            self.assertIs(type(stop()), int)
            self.assertEqual(stop(), 0)
            print("core diagnostic")
            return result(operation)

        cases = [(["create", "ACCOUNT", "--output", "NEW"], "create"),
                 (["check", "BUNDLE"], "check"),
                 (["verify-restore", "BUNDLE", "--output", "NEW"], "verify-restore")]
        for args, operation in cases:
            with self.subTest(operation=operation):
                code, report, stderr = self.call_double(args, complete)
                self.assertEqual(code, 0)
                self.assertEqual(report, result(operation))
                self.assertEqual(stderr, "core diagnostic\n")

    def test_unavailable_errors_hide_paths_and_keep_possible_output(self):
        async def unavailable(operation, stop, errors):
            raise errors.BackupUnavailable("SECRET path and account information")

        for args, may_exist in [(["check", "BUNDLE"], False),
                                (["create", "ACCOUNT", "--output", "NEW"], True),
                                (["verify-restore", "BUNDLE", "--output", "NEW"], True)]:
            with self.subTest(args=args):
                code, report, _ = self.call_double(args, unavailable)
                self.assertEqual(code, 2)
                self.assertEqual(report, {"error": "paper_backup_unavailable",
                                          "output_may_exist": may_exist})

    def test_storage_and_unexpected_errors_are_private_uncertainty(self):
        for failure in (OSError("SECRET disk"), RuntimeError("SECRET unexpected")):
            async def fail(operation, stop, errors):
                raise failure
            code, report, _ = self.call_double(["create", "ACCOUNT", "--output", "NEW"], fail)
            self.assertEqual(code, 4)
            self.assertEqual(report, {"error": "paper_backup_storage_failed"
                                     if isinstance(failure, OSError) else "paper_backup_failed",
                                     "output_may_exist": True})

    def test_typed_interruption_reports_signal_and_possible_output(self):
        for signum in (2, 15):
            async def interrupt(operation, stop, errors):
                raise errors.BackupInterrupted(signum)
            code, report, _ = self.call_double(
                ["verify-restore", "BUNDLE", "--output", "NEW"], interrupt)
            self.assertEqual(code, 128 + signum)
            self.assertEqual(report, {"error": "paper_backup_interrupted",
                                      "output_may_exist": True})

    def test_real_missing_input_is_private_and_never_publishes(self):
        missing = str(self.root / "SECRET-missing")
        output = str(self.root / "SECRET-output")
        for args, may_exist in [(["check", missing], False),
                                (["create", missing, "--output", output], True),
                                (["verify-restore", missing, "--output", output], True)]:
            with self.subTest(args=args):
                code, report = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(report, {"error": "paper_backup_unavailable",
                                          "output_may_exist": may_exist})
        self.assertFalse(Path(output).exists())

    def test_real_create_check_and_restore_keep_one_archived_prefix(self):
        # Actual SQLite owners and original engine; only the public HTTP boundary
        # in the established fixture helper is synthetic. No backup core double.
        load_cli()
        from test_market_capture import Clock, fixture
        from trading_bot.market_capture_store import capture_writer
        from trading_bot.paper_stream import run_stream

        capture, account = self.root / "capture", self.root / "account"
        bundle, recovery = self.root / "bundle", self.root / "recovery"
        clock = Clock(datetime.now(timezone.utc) - timedelta(minutes=2))
        capture_config = dict(market="KRW-BTC", unit_minutes=1,
                              bootstrap_bars=120, retention_bars=5000, poll_seconds=15)
        targets = ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo")
        network_patches = [patch(name, side_effect=AssertionError("network forbidden"))
                           for name in targets]
        for guard in network_patches:
            guard.start()
            self.addCleanup(guard.stop)
        with capture_writer(capture, config=capture_config, now=clock()) as writer:
            writer.apply(fixture(clock, count=120), at=clock())
        started = asyncio.run(run_stream(account, capture=capture, max_polls=1, clock=clock))
        self.assertEqual(started["exit_code"], 0)
        clock.now += timedelta(minutes=2)
        with capture_writer(capture) as writer:
            writer.apply(fixture(clock, count=3), at=clock())
        advanced = asyncio.run(run_stream(account, max_polls=1, clock=clock))
        self.assertEqual(advanced["exit_code"], 0)
        self.assertEqual(advanced["observation"]["cursor"]["bars_processed"], 2)
        with capture_writer(capture) as writer:
            writer.stop(clock())
        original = {str(path): path.read_bytes()
                    for directory in (capture, account) for path in directory.iterdir()}

        code, created = self.invoke(["create", str(account), "--output", str(bundle)])
        self.assertEqual(code, 0, created)
        self.assertEqual(created["status"], "verified_backup")
        self.assertEqual(created["operation"], "create")
        self.assertFalse(created["read_only"])
        self.assertEqual(created["events_verified"], 2)
        self.assertEqual(created["stream_id"], advanced["observation"]["stream_id"])
        self.assertFalse(created["boundaries"]["execution_authorized"])
        self.assertFalse(created["boundaries"]["source_rebinding"])
        saved_bundle = {path.name: path.read_bytes() for path in bundle.iterdir()}

        code, checked = self.invoke(["check", str(bundle)])
        self.assertEqual(code, 0, checked)
        self.assertTrue(checked["read_only"])
        self.assertEqual(checked["operation"], "check")
        for key in ("backup_id", "stream_id", "capture_id", "events_verified",
                    "engine_sha256", "event_head_sha256", "payloads"):
            self.assertEqual(checked[key], created[key])

        code, restored = self.invoke(["verify-restore", str(bundle), "--output", str(recovery)])
        self.assertEqual(code, 0, restored)
        self.assertEqual(restored["status"], "verified_restore")
        self.assertEqual(restored["operation"], "verify-restore")
        self.assertFalse(restored["read_only"])
        for key in ("backup_id", "stream_id", "capture_id", "events_verified",
                    "engine_sha256", "event_head_sha256", "payloads"):
            self.assertEqual(restored[key], created[key])
        self.assertEqual(saved_bundle, {path.name: path.read_bytes() for path in bundle.iterdir()})
        self.assertEqual(saved_bundle, {path.name: path.read_bytes() for path in recovery.iterdir()})
        self.assertEqual(set(saved_bundle), {"backup.json", ".backup.lock",
                                            "account-database.bin", "capture-database.bin"})
        self.assertFalse((recovery / "stream.sqlite3").exists())
        self.assertFalse((recovery / "capture.sqlite3").exists())
        self.assertEqual(original, {str(path): path.read_bytes()
                                   for directory in (capture, account) for path in directory.iterdir()})
        for directory in (bundle, recovery):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for path in directory.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        # A saved bundle is self-contained evidence, even after original paths
        # disappear. Removing these disposable fixtures never authorizes resume.
        shutil.rmtree(account)
        shutil.rmtree(capture)
        code, independent = self.invoke(["check", str(recovery)])
        self.assertEqual(code, 0, independent)
        self.assertEqual(independent["backup_id"], created["backup_id"])
        self.assertEqual(independent["events_verified"], 2)

    @unittest.skipUnless(os.name == "posix", "requires POSIX process signals")
    def test_real_signals_reach_core_and_handlers_are_restored(self):
        self._signal_cases("interrupt")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process signals")
    def test_signal_cannot_hide_storage_uncertainty(self):
        self._signal_cases("storage")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process signals")
    def test_signal_received_at_core_completion_is_not_reported_as_success(self):
        self._signal_cases("complete")

    def _signal_cases(self, outcome):
        script = r'''
import asyncio,importlib.util,json,signal,sys,types
spec=importlib.util.spec_from_file_location('backup_signal_subject',sys.argv[1])
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
package=types.ModuleType('trading_bot')
runtime=types.ModuleType('trading_bot.paper_backup')
errors=types.ModuleType('trading_bot.paper_backup_io')
class BackupUnavailable(ValueError):pass
class BackupStorageError(OSError):pass
class BackupInterrupted(Exception):
 def __init__(self,signum):self.signum=signum
errors.BackupUnavailable=BackupUnavailable
errors.BackupStorageError=BackupStorageError
errors.BackupInterrupted=BackupInterrupted
package.paper_backup=runtime;package.paper_backup_io=errors
sys.modules.update({'trading_bot':package,'trading_bot.paper_backup':runtime,
 'trading_bot.paper_backup_io':errors})
async def wait_for_stop(*args,stop,**kwargs):
 print('READY',file=sys.stderr,flush=True)
 while not stop():await asyncio.sleep(.01)
 assert type(stop()) is int and stop() in (2,15)
 if sys.argv[3]=='storage':raise BackupStorageError('SECRET disk uncertainty')
 if sys.argv[3]=='interrupt':raise BackupInterrupted(stop())
 return {'exit_code':0,'status':'verified_backup'}
runtime.create_backup=runtime.check_backup=runtime.verify_restore=wait_for_stop
previous={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
args=[sys.argv[2],'BUNDLE']
if args[0]!='check':args+=['--output','NEW']
code=cli.main(args)
assert all(signal.getsignal(s)==handler for s,handler in previous.items())
raise SystemExit(code)
'''
        for operation in ("create", "check", "verify-restore"):
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(operation=operation, signum=int(signum), outcome=outcome):
                    process = subprocess.Popen(
                        [sys.executable, "-c", script, str(SCRIPT), operation, outcome],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        readable, _, _ = select.select([process.stderr], [], [], 8)
                        self.assertTrue(readable, "backup core did not reach its cancellation point")
                        self.assertEqual(process.stderr.readline().strip(), "READY")
                        process.send_signal(signum)
                        stdout, stderr = process.communicate(timeout=8)
                        self.assertEqual(process.returncode, 4 if outcome == "storage" else 128 + int(signum), stderr)
                        self.assertEqual(len(stdout.splitlines()), 1)
                        self.assertNotIn("SECRET", stdout + stderr)
                        self.assertEqual(json.loads(stdout), {
                            "error": "paper_backup_storage_failed" if outcome == "storage"
                            else "paper_backup_interrupted",
                            "output_may_exist": operation != "check",
                        })
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
