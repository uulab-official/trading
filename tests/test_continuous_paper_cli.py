"""Continuous paper CLI contracts; subprocess tests never contact a provider."""
from pathlib import Path
from contextlib import redirect_stdout
from decimal import Decimal
from datetime import datetime, timezone
import asyncio
import importlib.util
import io
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/paper_stream.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("continuous_paper_cli_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_current_capture(directory):
    # Keep the original provider parser/provenance and real capture SQLite store.
    # Only the provider HTTP boundary is synthetic, as in capture integration tests.
    from test_market_capture import Clock, fixture
    from trading_bot.market_capture_store import capture_writer
    clock = Clock(datetime.now(timezone.utc))
    config = dict(market="KRW-BTC", unit_minutes=1, bootstrap_bars=120,
                  retention_bars=5000, poll_seconds=15)
    with capture_writer(directory, config=config, now=clock()) as writer:
        writer.attempt(clock())
        writer.apply(fixture(clock, count=120), at=clock())
    return clock


class ContinuousPaperCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, args, *, network_forbidden=False):
        command = [sys.executable, str(SCRIPT), *args]
        if network_forbidden:
            command = [sys.executable, "-c", r'''
import runpy,socket,sys
def forbidden(*args,**kwargs):raise AssertionError('provider traffic forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
''', str(SCRIPT), *args]
        result = subprocess.run(
            command,
            capture_output=True, text=True, timeout=10,
        )
        self.assertTrue(result.stdout.startswith("{"), "CLI did not produce a JSON result")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_invalid_arguments_are_one_private_json_without_creating_an_account(self):
        source = str(self.root / "SECRET-source")
        account = str(self.root / "SECRET-new")
        run = ["run", "--capture", source, "--directory", account]
        for args in (
            [], ["run"], ["resume"], ["check"], ["audit"],
            run + ["--live"], run + ["--mode", "live"],
            run + ["--max-polls", "0"], run + ["--max-polls", "10001"],
            run + ["--poll-seconds", "4"], run + ["--poll-seconds", "301"],
            run + ["--initial-balance", "NaN"],
            run + ["--fee-pct", "SECRET-invalid"],
            ["resume", account, "--capture", source],
            ["resume", account, "--strategy", "rsi"],
            ["resume", account, "--poll-seconds", "10"],
        ):
            with self.subTest(args=args):
                code, data = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(data, {"error": "paper_stream_arguments_invalid"})
        self.assertFalse(Path(account).exists())
        self.assertFalse(Path(source).exists())

    def test_missing_and_malformed_paths_are_private_without_side_effects(self):
        missing = str(self.root / "SECRET-missing")
        for args in (["check", missing], ["audit", missing], ["resume", missing],
                     ["run", "--capture", missing, "--directory", str(self.root / "new")]):
            with self.subTest(args=args):
                code, data = self.invoke(args, network_forbidden=True)
                self.assertEqual(code, 2)
                self.assertEqual(data, {"error": "paper_stream_unavailable"})
        cli = load_cli()
        with redirect_stdout(io.StringIO()) as output:
            code = cli.main(["check", "SECRET\x00private"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {"error": "paper_stream_unavailable"})
        self.assertFalse((self.root / "new").exists())

    def test_real_create_check_audit_and_resume_keep_one_account_and_source(self):
        source, account = self.root / "source", self.root / "account"
        create_current_capture(source)
        source_before = {p.name: p.read_bytes() for p in source.iterdir()}
        code, data = self.invoke([
            "run", "--capture", str(source), "--directory", str(account),
            "--strategy", "rsi", "--rsi-period", "21",
            "--initial-balance", "2000000", "--max-polls", "1",
        ], network_forbidden=True)
        self.assertEqual(code, 0, data)
        first = data["observation"]
        self.assertEqual(first["status"], "stopped")
        self.assertEqual(first["cursor"]["bars_processed"], 0)
        self.assertEqual(first["cursor"]["bootstrap_bars"], 120)
        self.assertEqual(first["account"]["fill_count"], 0)
        self.assertEqual(Decimal(first["account"]["cash"]), Decimal("2000000"))
        self.assertEqual(first["account"]["currency"], "KRW")
        self.assertIsNone(first["pending_signal"])
        self.assertEqual(first["configuration"]["strategy"], "rsi")
        self.assertEqual(first["configuration"]["rsi_period"], 21)
        account_before = {p.name: p.read_bytes() for p in account.iterdir()}

        code, checked = self.invoke(["check", str(account)], network_forbidden=True)
        self.assertEqual(code, 1, checked)
        self.assertEqual(checked["stream_id"], first["stream_id"])
        self.assertEqual(checked["integrity"]["verification"], "structural_head_only")
        code, audited = self.invoke(["audit", str(account)], network_forbidden=True)
        self.assertEqual(code, 0, audited)
        self.assertEqual(audited["status"], "verified_prefix")
        self.assertEqual(audited["events_verified"], 0)
        self.assertEqual(audited["stream_id"], first["stream_id"])
        self.assertEqual(account_before, {p.name: p.read_bytes() for p in account.iterdir()})

        code, resumed = self.invoke(["resume", str(account), "--max-polls", "1"],
                                    network_forbidden=True)
        self.assertEqual(code, 0, resumed)
        final = resumed["observation"]
        self.assertEqual(final["stream_id"], first["stream_id"])
        self.assertEqual(final["account"], first["account"])
        self.assertEqual(final["cursor"], first["cursor"])
        self.assertEqual(final["configuration"], first["configuration"])
        self.assertEqual(source_before, {p.name: p.read_bytes() for p in source.iterdir()})

        from trading_bot.paper_stream_store import stream_writer
        with stream_writer(account):
            code, busy = self.invoke(["audit", str(account)], network_forbidden=True)
        self.assertEqual(code, 2, busy)
        self.assertEqual(busy, {"error": "paper_stream_unavailable"})

    def test_dispatch_and_fixed_failure_json_are_isolated_wiring_contracts(self):
        # This double covers CLI ownership/typing/error boundaries only. Real
        # source/store/engine behavior is covered separately below and in runtime tests.
        cli = load_cli()
        package = types.ModuleType("trading_bot")
        runtime = types.ModuleType("trading_bot.paper_stream")
        package.paper_stream = runtime

        async def run(directory, **options):
            self.assertEqual(directory, "account")
            self.assertEqual(options["max_polls"], 2)
            self.assertEqual(options["stop"](), 0)
            if "capture" in options:
                self.assertEqual(options["capture"], "source")
                self.assertEqual(options["poll_seconds"], 10)
                self.assertEqual(options["config_options"], {
                    "strategy": "rsi", "initial_balance": Decimal("2000000"),
                    "rsi_period": 21,
                })
            else:
                self.assertEqual(set(options), {"max_polls", "stop"})
            return {"exit_code": 3, "observation": {"status": "waiting"}}

        async def audit(directory, **options):
            self.assertEqual(directory, "account")
            self.assertEqual(options["stop"](), 0)
            return {"exit_code": 0, "status": "verified_prefix"}

        runtime.run_stream = run
        runtime.audit_stream = audit
        runtime.stream_status = lambda directory: {"status": "current"}
        previous = {x: signal.getsignal(x) for x in (signal.SIGINT, signal.SIGTERM)}
        with patch.dict(sys.modules, {"trading_bot": package,
                "trading_bot.paper_stream": runtime}):
            cases = [
                (["run", "--capture", "source", "--directory", "account",
                  "--poll-seconds", "10", "--max-polls", "2", "--strategy", "rsi",
                  "--initial-balance", "2000000", "--rsi-period", "21"], 3, "run"),
                (["resume", "account", "--max-polls", "2"], 3, "resume"),
                (["audit", "account"], 0, "audit"),
            ]
            for args, expected, operation in cases:
                with self.subTest(args=args), redirect_stdout(io.StringIO()) as output:
                    code = cli.main(args)
                data = json.loads(output.getvalue())
                self.assertEqual(code, expected)
                self.assertEqual(data["operation"], operation)
                self.assertEqual(data["mode"], "paper")
                self.assertIs(data["live_enabled"], False)
                self.assertIs(data["read_only"], operation == "audit")
                self.assertEqual({x: signal.getsignal(x) for x in previous}, previous)

            for error, expected, text in [
                (ValueError("/SECRET/input"), 2, "paper_stream_unavailable"),
                (OSError("/SECRET/storage"), 4, "paper_stream_storage_failed"),
                (RuntimeError("/SECRET/private"), 4, "paper_stream_failed_check_stream"),
                (asyncio.CancelledError(), 130, "paper_stream_interrupted_check_stream"),
            ]:
                async def refused(*args, **kwargs):
                    raise error
                runtime.run_stream = refused
                with self.subTest(error=type(error).__name__), redirect_stdout(io.StringIO()) as output:
                    code = cli.main(["resume", "account"])
                self.assertEqual(code, expected)
                self.assertEqual(json.loads(output.getvalue()), {"error": text})
                self.assertEqual({x: signal.getsignal(x) for x in previous}, previous)

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_consumer_signal_stop_preserves_account_and_releases_writer(self):
        script = r'''
import importlib.util,signal,socket,sys,time
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server'):
 sys.path.insert(0,str(root/part))
def forbidden(*args,**kwargs):raise AssertionError('provider traffic forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
from trading_bot import paper_stream as runtime
actual_run=runtime.run_stream
announced=[]
def observable_sleep(seconds):
 if not announced:
  announced.append(True)
  print('READY',file=sys.stderr,flush=True)
 time.sleep(seconds)
async def run(directory,**options):
 return await actual_run(directory,sleep=observable_sleep,**options)
runtime.run_stream=run
spec=importlib.util.spec_from_file_location('stream_real_signal',root/'tools/paper_stream.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
previous={x:signal.getsignal(x) for x in (signal.SIGINT,signal.SIGTERM)}
code=cli.main(['run','--capture',sys.argv[2],'--directory',sys.argv[3]])
assert all(signal.getsignal(x)==v for x,v in previous.items())
raise SystemExit(code)
'''
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=int(signum)):
                source = self.root / ("source-" + str(int(signum)))
                account = self.root / ("account-" + str(int(signum)))
                create_current_capture(source)
                before = {p.name: p.read_bytes() for p in source.iterdir()}
                process = subprocess.Popen([
                    sys.executable, "-c", script, str(ROOT), str(source), str(account),
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    readable, _, _ = select.select([process.stderr], [], [], 10)
                    self.assertTrue(readable, "consumer did not enter its poll wait")
                    self.assertEqual(process.stderr.readline().strip(), "READY")
                    process.send_signal(signum)
                    out, err = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 128 + int(signum), err)
                    self.assertEqual(len(out.splitlines()), 1)
                    report = json.loads(out)["observation"]
                    self.assertEqual(report["status"], "stopped")
                    self.assertEqual(report["cursor"]["bars_processed"], 0)
                    self.assertEqual(report["account"]["fill_count"], 0)
                    self.assertIsNone(report["pending_signal"])
                    self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
                    code, audited = self.invoke(["audit", str(account)], network_forbidden=True)
                    self.assertEqual(code, 0, audited)
                    self.assertEqual(audited["events_verified"], 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_signal_numbers_reach_runtime_and_restore_handlers(self):
        # Isolated signal wiring: the fake async runtime waits without writing.
        script = r'''
import asyncio,importlib.util,json,signal,sys,types
spec=importlib.util.spec_from_file_location('stream_signal_subject',sys.argv[1])
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
package=types.ModuleType('trading_bot');runtime=types.ModuleType('trading_bot.paper_stream')
package.paper_stream=runtime
sys.modules['trading_bot']=package;sys.modules['trading_bot.paper_stream']=runtime
async def wait_for_stop(directory,*,stop,**kwargs):
 print('READY',file=sys.stderr,flush=True)
 while not stop():await asyncio.sleep(.01)
 assert type(stop()) is int
 return {'exit_code':128+stop(),'status':'interrupted'}
runtime.run_stream=runtime.audit_stream=wait_for_stop
previous={x:signal.getsignal(x) for x in (signal.SIGINT,signal.SIGTERM)}
code=cli.main([sys.argv[2],'account'])
assert all(signal.getsignal(x)==v for x,v in previous.items())
raise SystemExit(code)
'''
        for command in ("resume", "audit"):
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(command=command, signum=int(signum)):
                    process = subprocess.Popen(
                        [sys.executable, "-c", script, str(SCRIPT), command],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    try:
                        readable, _, _ = select.select([process.stderr], [], [], 8)
                        self.assertTrue(readable, "runtime did not enter its wait")
                        self.assertEqual(process.stderr.readline().strip(), "READY")
                        process.send_signal(signum)
                        out, err = process.communicate(timeout=8)
                        self.assertEqual(process.returncode, 128 + int(signum), err)
                        self.assertEqual(len(out.splitlines()), 1)
                        data = json.loads(out)
                        self.assertEqual(data["operation"], command)
                        self.assertEqual(data["status"], "interrupted")
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
