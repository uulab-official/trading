"""Strict operator CLI, real signal handling and inert local unit publication."""
import asyncio
import configparser
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import select
import shlex
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/paper_service.py"
UNIT_MODULE = ROOT / "apps/server/trading_bot/paper_service_unit.py"


def load_cli():
    if not SCRIPT.exists():
        raise AssertionError("The paper service CLI has not been implemented")
    spec = importlib.util.spec_from_file_location("paper_service_cli_subject", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_unit():
    if not UNIT_MODULE.exists():
        raise AssertionError("The inactive paper service unit renderer has not been implemented")
    load_cli()
    from trading_bot import paper_service_unit
    return paper_service_unit


def result(operation, code=0):
    return {
        "schema_version": 1,
        "source": "paper_service",
        "operation": operation,
        "mode": "paper",
        "live_enabled": False,
        "status": "configured" if operation == "configure" else "observed",
        "exit_code": code,
        "service_id": "a" * 32,
        "attempt_id": None,
        "attempt_number": None,
        "children": {},
        "boundaries": {
            "data_readiness": "not_assessed",
            "live_execution": False,
            "automatic_restart": False,
        },
    }


@contextmanager
def runtime_double(callback):
    # These APIs are being implemented concurrently. Replace only that boundary;
    # exercise the actual parser, dispatch, error mapping and OS signal handlers.
    package = types.ModuleType("trading_bot")
    runtime = types.ModuleType("trading_bot.paper_service")
    errors = types.ModuleType("trading_bot.paper_service_state")
    units = types.ModuleType("trading_bot.paper_service_unit")

    class ServiceUnavailable(ValueError):
        pass

    class ServiceRefused(ValueError):
        def __init__(self, reason):
            self.reason = reason
            super().__init__("SECRET exception details")

    class ServiceStorageError(OSError):
        pass

    class ServiceInterrupted(Exception):
        def __init__(self, signum):
            self.signum = signum

    errors.ServiceUnavailable = ServiceUnavailable
    errors.ServiceRefused = ServiceRefused
    errors.ServiceStorageError = ServiceStorageError
    errors.ServiceInterrupted = ServiceInterrupted

    async def configure(account, directory, *, startup_seconds=180,
                        heartbeat_seconds=30, stop_seconds=15, stop=None):
        value = callback("configure", stop, errors, {
            "account": account, "directory": directory,
            "startup_seconds": startup_seconds,
            "heartbeat_seconds": heartbeat_seconds, "stop_seconds": stop_seconds,
        })
        return await value if inspect.isawaitable(value) else value

    def run(directory, *, stop=None):
        return callback("run", stop, errors, {"directory": directory})

    def probe(directory, *, timeout=1.0):
        return callback("check", None, errors, {"directory": directory, "timeout": timeout})

    def unit(directory, output):
        return callback("unit", None, errors, {"directory": directory, "output": output})

    runtime.configure_service = configure
    runtime.run_service = run
    runtime.probe_service = probe
    units.render_systemd_unit = unit
    package.paper_service = runtime
    package.paper_service_state = errors
    package.paper_service_unit = units
    with patch.dict(sys.modules, {
        "trading_bot": package, "trading_bot.paper_service": runtime,
        "trading_bot.paper_service_state": errors,
        "trading_bot.paper_service_unit": units,
    }):
        yield


class PaperServiceCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def call_double(self, args, callback):
        cli = load_cli()
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        with runtime_double(callback), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err:
            code = cli.main(args)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertNotIn("SECRET", out.getvalue() + err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        self.assertEqual(previous, {s: signal.getsignal(s) for s in previous})
        report = json.loads(out.getvalue())
        self.assertEqual(report["exit_code"], code)
        return code, report, err.getvalue()

    def test_failed_stdout_is_private_uncertainty_after_dispatch_or_invalid_arguments(self):
        program = r'''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'tests'))
from test_paper_service_cli import load_cli, runtime_double, result
from paper_service_fixtures import network_forbidden
code = int(sys.argv[2])
marker = Path(sys.argv[3])
def finished(operation, stop, errors, options):
    marker.write_text('returned_after_cleanup')
    return dict(result(operation, code), cleanup_confirmed=True)
with network_forbidden(), runtime_double(finished):
    raise SystemExit(load_cli().main(['--help'] if code == 2 else ['run', 'SECRET-service']))
'''
        for target in ('full', 'broken_pipe'):
            for code in (2, 3, 4, 143):
                with self.subTest(target=target, native_code=code):
                    marker = self.root / f'{target}-{code}'
                    if target == 'full':
                        descriptor = os.open('/dev/full', os.O_WRONLY)
                    else:
                        reader, descriptor = os.pipe()
                        os.close(reader)
                    try:
                        completed = subprocess.run([sys.executable, '-I', '-B', '-u', '-c',
                            program, str(ROOT), str(code), str(marker)],
                            stdin=subprocess.DEVNULL, stdout=descriptor,
                            stderr=subprocess.PIPE, timeout=10, close_fds=True)
                    finally:
                        os.close(descriptor)
                    self.assertEqual(completed.returncode, 4, completed.stderr)
                    self.assertEqual(completed.stderr, b'paper_service_output_unconfirmed\n')
                    self.assertEqual(marker.exists(), code != 2)

    def test_strict_arguments_refuse_unknown_duplicate_and_destructive_options(self):
        def forbidden(*args):
            self.fail("Invalid arguments entered the service API")

        cases = [
            [], ["configure"], ["run"], ["check"], ["unit"],
            ["configure", "SECRET-account"], ["unit", "SECRET-service"],
            ["configure", "ACCOUNT", "--dir", "NEW"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--directory", "OTHER"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--capture", "SECRET"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--command", "SECRET"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--live"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--credentials", "SECRET"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--strategy", "rsi"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--auto-restart"],
            ["configure", "ACCOUNT", "--directory", "NEW", "--heartbeat", "10"],
            ["run", "SECRET-service", "--daemon"],
            ["run", "SECRET-service", "--pid-file", "SECRET"],
            ["run", "SECRET-service", "--force"],
            ["run", "SECRET-service", "--output", "SECRET"],
            ["run", "SECRET-service", "EXTRA"],
            ["check", "SECRET-service", "--repair"],
            ["check", "SECRET-service", "--timeout", "10"],
            ["unit", "SERVICE", "--out", "NEW"],
            ["unit", "SERVICE", "--output", "NEW", "--output", "OTHER"],
            ["unit", "SERVICE", "--output", "NEW", "--enable"],
            ["unit", "SERVICE", "--output", "NEW", "--start"],
            ["unit", "SERVICE", "--output", "NEW", "--install"],
            ["stop", "SECRET-service"], ["restart", "SECRET-service"],
            ["kill", "123"], ["--help"], ["run", "--help"],
        ]
        for args in cases:
            with self.subTest(args=args):
                code, report, stderr = self.call_double(args, forbidden)
                self.assertEqual(code, 2)
                self.assertEqual(report["error"], "paper_service_arguments_invalid")
                self.assertEqual(stderr, "")

    def test_timeout_ranges_are_checked_before_any_service_operation(self):
        def forbidden(*args):
            self.fail("Invalid timeout entered the service API")

        for name, values in {
            "startup-seconds": ("9", "901", "-10", "1.0", "nan", "True"),
            "heartbeat-seconds": ("4", "121", "0", "1.5", "inf"),
            "stop-seconds": ("0", "61", "-1", "False"),
        }.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    code, report, _ = self.call_double(
                        ["configure", "ACCOUNT", "--directory", "NEW", "--" + name, value],
                        forbidden)
                    self.assertEqual(code, 2)
                    self.assertEqual(report["error"], "paper_service_arguments_invalid")
            code, _, _ = self.call_double(
                ["configure", "ACCOUNT", "--directory", "NEW",
                 "--" + name, "15", "--" + name, "15"], forbidden)
            self.assertEqual(code, 2)

    def test_dispatch_preserves_complete_results_and_uses_only_fixed_api_parameters(self):
        def complete(operation, stop, errors, options):
            if operation in ("configure", "run"):
                self.assertIs(type(stop()), int)
                self.assertEqual(stop(), 0)
            print("core diagnostic")
            return dict(result(operation), received_options=options)

        cases = [
            (["configure", "ACCOUNT", "--directory", "NEW"], "configure", {
                "account": "ACCOUNT", "directory": "NEW", "startup_seconds": 180,
                "heartbeat_seconds": 30, "stop_seconds": 15}),
            (["configure", "ACCOUNT", "--directory", "NEW", "--startup-seconds", "10",
              "--heartbeat-seconds", "5", "--stop-seconds", "1"], "configure", {
                "account": "ACCOUNT", "directory": "NEW", "startup_seconds": 10,
                "heartbeat_seconds": 5, "stop_seconds": 1}),
            (["configure", "ACCOUNT", "--directory", "NEW", "--startup-seconds", "900",
              "--heartbeat-seconds", "120", "--stop-seconds", "60"], "configure", {
                "account": "ACCOUNT", "directory": "NEW", "startup_seconds": 900,
                "heartbeat_seconds": 120, "stop_seconds": 60}),
            (["run", "SERVICE"], "run", {"directory": "SERVICE"}),
            (["check", "SERVICE"], "check", {"directory": "SERVICE", "timeout": 1.0}),
            (["unit", "SERVICE", "--output", "NEW"], "unit", {
                "directory": "SERVICE", "output": "NEW"}),
        ]
        for args, operation, options in cases:
            with self.subTest(args=args):
                code, report, stderr = self.call_double(args, complete)
                self.assertEqual(code, 0)
                self.assertEqual(report, dict(result(operation), received_options=options))
                self.assertEqual(stderr, "core diagnostic\n")

    def test_stopped_probe_and_runtime_failure_codes_are_preserved(self):
        for operation, codes in (("check", (0, 1, 2)), ("run", (3, 4, 130, 143))):
            for code in codes:
                def complete(actual, stop, errors, options):
                    return result(actual, code)
                with self.subTest(operation=operation, code=code):
                    actual, report, _ = self.call_double([operation, "SERVICE"], complete)
                    self.assertEqual(actual, code)
                    self.assertEqual(report, result(operation, code))

    def test_unavailable_and_unexpected_errors_never_expose_exception_text(self):
        failures = (("unavailable", 2, "paper_service_unavailable"),
                    ("storage", 4, "paper_service_storage_failed"),
                    ("os", 4, "paper_service_storage_failed"),
                    ("unexpected", 4, "paper_service_failed"))
        for kind, expected, message in failures:
            def fail(operation, stop, errors, options):
                error = {
                    "unavailable": errors.ServiceUnavailable,
                    "storage": errors.ServiceStorageError,
                    "os": OSError, "unexpected": RuntimeError,
                }[kind]
                raise error("SECRET path, database and account details")
            with self.subTest(kind=kind):
                code, report, _ = self.call_double(
                    ["configure", "ACCOUNT", "--directory", "NEW"], fail)
                self.assertEqual(code, expected)
                self.assertEqual(report["error"], message)
                self.assertTrue(report["output_may_exist"])
                self.assertFalse(report["live_enabled"])

    def test_native_refusal_retains_only_a_bounded_safe_reason(self):
        for reason, expected in (("capture_blocked", "capture_blocked"),
                                 ("SECRET /private/account", "unavailable"),
                                 ("x" * 500, "unavailable")):
            def fail(operation, stop, errors, options):
                raise errors.ServiceRefused(reason)
            with self.subTest(reason=reason):
                code, report, _ = self.call_double(["run", "SERVICE"], fail)
                self.assertEqual(code, 3)
                self.assertEqual(report["reason"], expected)
                self.assertEqual(report["error"], "paper_service_refused")

    def test_typed_interruptions_preserve_exact_signal_and_possible_output(self):
        for signum in (2, 15):
            def fail(operation, stop, errors, options):
                raise errors.ServiceInterrupted(signum)
            with self.subTest(signum=signum):
                code, report, _ = self.call_double(
                    ["unit", "SERVICE", "--output", "NEW"], fail)
                self.assertEqual(code, 128 + signum)
                self.assertEqual(report["error"], "paper_service_interrupted")
                self.assertTrue(report["output_may_exist"])

    def test_invalid_runtime_exit_code_cannot_be_reported_as_success(self):
        for value in (True, 9, None, "0"):
            def complete(operation, stop, errors, options):
                return result(operation, value)
            with self.subTest(value=value):
                code, report, _ = self.call_double(["run", "SERVICE"], complete)
                self.assertEqual(code, 4)
                self.assertEqual(report["error"], "paper_service_failed")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_signals_are_numeric_intent_and_handlers_are_restored(self):
        self._signals("interrupt")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_late_signal_changes_success_to_interruption(self):
        self._signals("late")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_signal_cannot_erase_refusal_exception(self):
        self._signals("refusal")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_signal_cannot_erase_storage_exception(self):
        self._signals("storage")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_real_signal_cannot_erase_runtime_refusal_or_uncertainty_report(self):
        self._signals("report3")
        self._signals("report4")

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_signal_during_final_json_output_preserves_refusal_and_storage_exit(self):
        script = r'''
import os,runpy,signal,socket,sys
def forbidden(*args,**kwargs):raise AssertionError('network forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
helpers=runpy.run_path(sys.argv[1]);cli=helpers['load_cli']()
expected=int(sys.argv[2]);signum=int(sys.argv[3]);stage=sys.argv[4];sent=[]
def complete(operation,stop,errors,options):return helpers['result'](operation,expected)
real_dumps=cli.json.dumps
def interrupt_serialization(value,*args,**kwargs):
 if stage=='serialize' and not sent:
  sent.append(True);os.kill(os.getpid(),signum)
 return real_dumps(value,*args,**kwargs)
cli.json.dumps=interrupt_serialization
real_stdout=sys.stdout
class InterruptFlush:
 def write(self,value):return real_stdout.write(value)
 def flush(self):
  if stage=='flush' and not sent:
   sent.append(True);os.kill(os.getpid(),signum)
  real_stdout.flush()
sys.stdout=InterruptFlush()
previous={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
with helpers['runtime_double'](complete):code=cli.main(['run','SERVICE'])
assert all(signal.getsignal(s)==old for s,old in previous.items())
raise SystemExit(code)
'''
        for expected in (3, 4):
            for signum in (2, 15):
                for stage in ("serialize", "flush"):
                    with self.subTest(expected=expected, signal=signum, stage=stage):
                        completed = subprocess.run(
                            [sys.executable, "-c", script, str(Path(__file__)),
                             str(expected), str(signum), stage],
                            capture_output=True, text=True, timeout=8)
                        self.assertEqual(completed.returncode, expected, completed.stderr)
                        self.assertEqual(len(completed.stdout.splitlines()), 1)
                        self.assertEqual(json.loads(completed.stdout), result("run", expected))
                        self.assertNotIn("Traceback", completed.stderr)

    def _signals(self, outcome):
        script = r'''
import json,runpy,signal,socket,sys,time
def forbidden(*args,**kwargs):raise AssertionError('network forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
helpers=runpy.run_path(sys.argv[1]);cli=helpers['load_cli']()
def work(operation,stop,errors,options):
 print('READY',file=sys.stderr,flush=True)
 while not stop():time.sleep(.005)
 assert type(stop()) is int and stop() in (2,15)
 if sys.argv[3]=='interrupt':raise errors.ServiceInterrupted(stop())
 if sys.argv[3]=='refusal':raise errors.ServiceRefused('capture_blocked')
 if sys.argv[3]=='storage':raise errors.ServiceStorageError('SECRET disk failure')
 code=int(sys.argv[3][-1]) if sys.argv[3].startswith('report') else 0
 return helpers['result'](operation,code)
previous={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
args=[sys.argv[2],'ACCOUNT']
if args[0]=='configure':args+=['--directory','NEW']
with helpers['runtime_double'](work):code=cli.main(args)
assert all(signal.getsignal(s)==old for s,old in previous.items())
raise SystemExit(code)
'''
        for operation in ("configure", "run"):
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(operation=operation, signal=int(signum), outcome=outcome):
                    process = subprocess.Popen(
                        [sys.executable, "-c", script, str(Path(__file__)), operation, outcome],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        ready, _, _ = select.select([process.stderr], [], [], 8)
                        self.assertTrue(ready, "service API did not reach its cancellation point")
                        self.assertEqual(process.stderr.readline().strip(), "READY")
                        process.send_signal(signum)
                        stdout, stderr = process.communicate(timeout=8)
                        expected = (3 if outcome in ("refusal", "report3") else
                                    4 if outcome in ("storage", "report4") else 128 + int(signum))
                        self.assertEqual(process.returncode, expected, stderr)
                        self.assertEqual(len(stdout.splitlines()), 1)
                        report = json.loads(stdout)
                        self.assertEqual(report["exit_code"], expected)
                        self.assertNotIn("SECRET", stdout + stderr)
                        self.assertNotIn("Traceback", stdout + stderr)
                        if outcome in ("report3", "report4"):
                            self.assertEqual(report, result(operation, expected))
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=3)


class PaperServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.module = load_unit()
        from test_market_capture import Clock, fixture
        from trading_bot.market_capture_store import capture_writer
        from trading_bot.paper_service_state import configure_service
        from trading_bot.paper_stream import run_stream

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.capture, self.account = self.root / "capture", self.root / "account"
        self.service = self.root / "service"
        for target in ("socket.create_connection", "socket.getaddrinfo",
                       "socket.socket.connect", "socket.socket.sendto"):
            guard = patch(target, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        clock = Clock(datetime.now(timezone.utc) - timedelta(minutes=2))
        config = dict(market="KRW-BTC", unit_minutes=1, bootstrap_bars=120,
                      retention_bars=5000, poll_seconds=15)
        with capture_writer(self.capture, config=config, now=clock()) as writer:
            writer.apply(fixture(clock, count=120), at=clock())
        started = asyncio.run(run_stream(self.account, capture=self.capture,
                                         max_polls=1, clock=clock))
        self.assertEqual(started["exit_code"], 0)
        with capture_writer(self.capture) as writer:
            writer.stop(clock())
        configured = asyncio.run(configure_service(self.account, self.service))
        self.assertEqual(configured["exit_code"], 0)

    def snapshot_inputs(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for directory in (self.account, self.capture, self.service)
                for path in directory.rglob("*") if path.is_file()}

    def test_fixed_unit_preserves_inputs_and_is_new_private_nonexecuting_file(self):
        before = self.snapshot_inputs()
        output = self.root / "paper-account.service"
        with patch("subprocess.Popen", side_effect=AssertionError("activation forbidden")):
            report = self.module.render_systemd_unit(self.service, output)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["operation"], "unit")
        self.assertFalse(report["activation_performed"])
        self.assertFalse(report["live_enabled"])
        self.assertEqual(report["boundaries"]["data_readiness"], "not_assessed")
        self.assertFalse(report["boundaries"]["live_execution"])
        self.assertEqual(before, self.snapshot_inputs())
        details = output.stat()
        self.assertEqual(stat.S_IMODE(details.st_mode), 0o600)
        self.assertEqual(details.st_uid, os.getuid())
        self.assertEqual(details.st_nlink, 1)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(output.read_text())
        service = dict(parser["Service"])
        self.assertEqual(shlex.split(service.pop("ExecStart")), [
            sys.executable, "-I", "-B", "-u", str(SCRIPT), "run", str(self.service),
        ])
        self.assertEqual(service, {
            "Type": "simple", "Restart": "no", "KillMode": "mixed",
            "KillSignal": "SIGTERM", "SendSIGKILL": "yes", "SuccessExitStatus": "130 143",
            "UMask": "0077", "NoNewPrivileges": "yes", "LimitCORE": "0",
            "TimeoutStopSec": "45",
        })
        self.assertNotIn("Install", parser)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "requires offline systemd verifier")
    def test_generated_unit_passes_actual_offline_systemd_analyze_verify(self):
        output = self.root / "paper-account.service"
        self.module.render_systemd_unit(self.service, output)
        verified = subprocess.run([shutil.which("systemd-analyze"), "verify", str(output)],
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_unit_reads_config_while_service_exclusive_owner_is_present(self):
        from trading_bot.paper_service_state import ServiceDirectory
        output = self.root / "active-account.service"
        with ServiceDirectory(self.service, exclusive=True):
            report = self.module.render_systemd_unit(self.service, output)
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(output.is_file())

    def test_manager_stop_budget_tracks_both_configured_child_graces(self):
        from trading_bot.paper_service_state import configure_service
        for grace, expected in ((1, 17), (60, 135)):
            with self.subTest(grace=grace):
                directory = self.root / ("service-stop-" + str(grace))
                configured = asyncio.run(configure_service(
                    self.account, directory, stop_seconds=grace))
                self.assertEqual(configured["exit_code"], 0)
                output = self.root / ("stop-" + str(grace) + ".service")
                report = self.module.render_systemd_unit(directory, output)
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(output.read_text())
                self.assertEqual(parser.getint("Service", "TimeoutStopSec"), expected)
                self.assertEqual(report["timeout_stop_seconds"], expected)

    def test_existing_file_and_symlink_are_never_clobbered(self):
        from trading_bot.paper_service_state import ServiceUnavailable
        output = self.root / "existing.service"
        output.write_bytes(b"existing private unit\n")
        output.chmod(0o600)
        before = (output.read_bytes(), output.stat().st_ino)
        alias = self.root / "alias.service"
        alias.symlink_to(output)
        for target in (output, alias):
            with self.subTest(target=target.name), self.assertRaises(ServiceUnavailable):
                self.module.render_systemd_unit(self.service, target)
            self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
        self.assertTrue(alias.is_symlink())

    def test_rerender_to_same_destination_requires_a_new_name(self):
        from trading_bot.paper_service_state import ServiceUnavailable
        output = self.root / "once.service"
        self.module.render_systemd_unit(self.service, output)
        before = (output.read_bytes(), output.stat().st_ino)
        with self.assertRaises(ServiceUnavailable):
            self.module.render_systemd_unit(self.service, output)
        self.assertEqual((output.read_bytes(), output.stat().st_ino), before)

    def test_paths_reject_relative_whitespace_nonascii_and_interpolation_characters(self):
        from trading_bot.paper_service_state import ServiceUnavailable
        for suffix in ("bad space", "bad%name", "bad$name", "bad;name", "bad\nname", "한글"):
            output = self.root / (suffix + ".service")
            with self.subTest(suffix=suffix), self.assertRaises(ServiceUnavailable):
                self.module.render_systemd_unit(self.service, output)
            self.assertFalse(output.exists())
        for directory, output in (("relative-service", self.root / "valid.service"),
                                  (self.service, "relative.service"),
                                  (str(self.service) + "\nSECRET", self.root / "valid.service")):
            with self.subTest(directory=directory, output=output), \
                    self.assertRaises(ServiceUnavailable):
                self.module.render_systemd_unit(directory, output)

    def test_output_cannot_add_members_to_service_or_native_source_directories(self):
        from trading_bot.paper_service_state import ServiceUnavailable
        before = self.snapshot_inputs()
        alias = self.root / "service-alias"
        alias.symlink_to(self.service, target_is_directory=True)
        for directory in (self.service, self.service / "attempts", self.account,
                          self.capture, alias):
            output = directory / "forbidden.service"
            with self.subTest(directory=directory), self.assertRaises(ServiceUnavailable):
                self.module.render_systemd_unit(self.service, output)
            self.assertFalse(output.exists())
        self.assertEqual(before, self.snapshot_inputs())

    def test_relocated_native_directory_cannot_become_a_unit_output_parent(self):
        from trading_bot.paper_service_state import ServiceUnavailable
        relocated = self.root / "relocated-account"
        self.account.rename(relocated)
        output = relocated / "forbidden.service"
        before = {path.name: path.read_bytes() for path in relocated.iterdir()}
        with self.assertRaises(ServiceUnavailable):
            self.module.render_systemd_unit(self.service, output)
        self.assertFalse(output.exists())
        self.assertEqual(before, {path.name: path.read_bytes() for path in relocated.iterdir()})

    def test_config_replacement_during_final_output_sync_preserves_uncertain_output(self):
        from trading_bot.paper_service_state import ServiceStorageError
        output = self.root / "uncertain.service"
        replacement = self.root / "replacement.json"
        replacement.write_bytes((self.service / "service.json").read_bytes())
        replacement.chmod(0o600)
        parent_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        real_fsync = os.fsync
        changed = []

        def replace_after_sync(fd):
            real_fsync(fd)
            descriptor = os.fstat(fd)
            if (not changed and stat.S_ISDIR(descriptor.st_mode)
                    and (descriptor.st_dev, descriptor.st_ino) == parent_identity):
                os.replace(replacement, self.service / "service.json")
                changed.append(True)

        with patch("trading_bot.local_report_io.os.fsync", side_effect=replace_after_sync), \
                self.assertRaises(ServiceStorageError):
            self.module.render_systemd_unit(self.service, output)
        self.assertEqual(changed, [True])
        self.assertTrue(output.is_file(), "uncertain published bytes must remain inspectable")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_storage_failure_is_typed_uncertainty_without_claiming_generation(self):
        from trading_bot.paper_service_state import ServiceStorageError
        with patch("trading_bot.local_report_io.os.fsync", side_effect=OSError("SECRET disk")), \
                self.assertRaises(ServiceStorageError):
            self.module.render_systemd_unit(self.service, self.root / "failed.service")

    def invoke(self, args):
        code = r'''
import runpy,socket,sys
def forbidden(*args,**kwargs):raise AssertionError('network forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=socket.socket.sendto=forbidden
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        completed = subprocess.run([sys.executable, "-c", code, str(SCRIPT), *args],
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(len(completed.stdout.splitlines()), 1, completed.stderr)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertNotIn("SECRET", completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["exit_code"], completed.returncode)
        return completed.returncode, report

    def test_actual_cli_configure_check_and_unit_remain_inert(self):
        before = self.snapshot_inputs()
        second = self.root / "second-service"
        code, configured = self.invoke(["configure", str(self.account), "--directory", str(second)])
        self.assertEqual(code, 0, configured)
        code, observed = self.invoke(["check", str(second)])
        self.assertEqual(code, 1, observed)
        self.assertEqual(observed["status"], "not_running")
        code, generated = self.invoke(["unit", str(second), "--output",
                                       str(self.root / "second-account.service")])
        self.assertEqual(code, 0, generated)
        self.assertFalse(generated["activation_performed"])
        self.assertEqual(list((second / "attempts").iterdir()), [])
        self.assertEqual(before, self.snapshot_inputs())

    def test_actual_cli_missing_inputs_are_private_and_never_publish(self):
        missing = self.root / "SECRET-missing"
        output = self.root / "SECRET-output"
        for args in (["configure", str(missing), "--directory", str(output)],
                     ["run", str(missing)], ["check", str(missing)],
                     ["unit", str(missing), "--output", str(output)]):
            with self.subTest(command=args[0]):
                code, report = self.invoke(args)
                self.assertEqual(code, 2, report)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
