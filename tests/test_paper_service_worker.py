"""Bounded service frames and real guarded processes; provider traffic is forbidden."""
import importlib
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
for component in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, str(ROOT / component))

ATTEMPT = "a" * 32
SCRIPT = ROOT / "tools/paper_service_worker.py"

_CHILD = r'''
import os,signal,socket,sys,time
from pathlib import Path
root=Path(sys.argv[1])
for part in ('packages/core','packages/data','packages/strategy','apps/server','tests'):
 sys.path.insert(0,str(root/part))
def forbidden(*args,**kwargs):raise AssertionError('provider traffic forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
socket.socket.connect_ex=socket.socket.sendto=forbidden
from trading_bot import paper_service_worker as worker
exec(compile(sys.argv[2],'<service-worker-test>','exec'))
'''


def _read_until(process, event, *, timeout=8):
    output = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], .05)
        if ready:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                raise AssertionError("worker exited before " + event + ": " + repr(output))
            output += chunk
            for line in output.splitlines():
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get("event") == event:
                    return output
        if len(output) > 65536:
            raise AssertionError("worker output exceeded test budget")
    raise AssertionError("worker never reached " + event)


def _stop_child(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def _subreaper_drill(account, mode):
    """Isolated Linux harness: reap a supervisor and its adopted real worker."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise AssertionError("subreaper unavailable")
    supervisor_body = r'''
import ctypes,json
libc=ctypes.CDLL(None,use_errno=True)
parent=os.getppid()
assert libc.prctl(1,9,0,0,0)==0 and os.getppid()==parent
from test_paper_service_worker import _CHILD,_read_until
import subprocess
body=r"""
if sys.argv[4]=='before':
 original=worker.arm_parent_death
 def delayed(parent):
  os.write(2,b'BEFORE_GUARD\\n')
  time.sleep(.4)
  return original(parent)
 worker.arm_parent_death=delayed
if sys.argv[4]=='guarded':
 original_run=worker._run_native
 def delayed_run(*args):
  time.sleep(2)
  return original_run(*args)
 worker._run_native=delayed_run
raise SystemExit(worker.worker_main('stream',sys.argv[3],'a'*32,os.getppid()))
"""
child=subprocess.Popen([sys.executable,'-I','-B','-u','-c',_CHILD,str(root),body,
                       sys.argv[3],sys.argv[4]],stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
try:
 if sys.argv[4]=='before':
  import select
  ready,_,_=select.select([child.stderr],[],[],8)
  assert ready and os.read(child.stderr.fileno(),128)==b'BEFORE_GUARD\\n'
 else:
  _read_until(child,'guarded' if sys.argv[4]=='guarded' else 'owned')
 print(json.dumps({'pid':child.pid}),flush=True)
 while True:time.sleep(.1)
finally:
 if child.poll() is None:child.kill()
 child.wait(timeout=5)
'''
    process = subprocess.Popen([sys.executable, "-I", "-B", "-u", "-c", _CHILD,
        str(ROOT), supervisor_body, str(account), mode], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    adopted = None
    reaped = False
    try:
        ready, _, _ = select.select([process.stdout], [], [], 10)
        if not ready:
            raise AssertionError("supervisor did not report a worker")
        line = process.stdout.readline()
        if not line:
            raise AssertionError("supervisor failed: " + process.stderr.read(4096).decode())
        adopted = json.loads(line)["pid"]
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status = os.waitpid(adopted, os.WNOHANG)
            if pid:
                reaped = True
                outcome = os.waitstatus_to_exitcode(status)
                break
            time.sleep(.01)
        else:
            raise AssertionError("orphan worker did not terminate")
        descriptor = os.open(Path(account) / ".stream.lock", os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        database = Path(account) / "stream.sqlite3"
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        time.sleep(.15)
        stable = before == hashlib.sha256(database.read_bytes()).hexdigest()
        print(json.dumps(dict(supervisor=process.returncode, worker=outcome,
                              lock_released=True, stable=stable)), flush=True)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
        if adopted is not None and not reaped:
            try:
                os.kill(adopted, signal.SIGKILL)
                os.waitpid(adopted, 0)
            except ProcessLookupError:
                pass


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        try:
            self.protocol = importlib.import_module("trading_bot.paper_service_protocol")
        except ModuleNotFoundError:
            self.fail("the bounded service protocol has not been implemented")

    @staticmethod
    def raw(event="guarded", sequence=0, **changes):
        frame = dict(v=1, attempt=ATTEMPT, role="capture", sequence=sequence, event=event)
        frame.update(changes)
        return json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    def test_document_encoding_has_one_canonical_utf8_representation(self):
        body = {"z": [None, True, -7], "a": "한글"}
        raw = b'{"a":"\xed\x95\x9c\xea\xb8\x80","z":[null,true,-7]}'
        self.assertEqual(self.protocol.encode_document(body, 100), raw)
        self.assertEqual(self.protocol.decode_document(raw, 100), body)

    def test_document_refuses_ambiguous_or_noncanonical_json(self):
        for raw in (
            b'{"a":1,"a":2}', b'{"z":1,"a":2}', b'{"a": 1}', b'{}\n',
            b'{"a":1.0}', b'{"a":1e0}', b'{"a":NaN}', b'{"a":Infinity}',
            b'{"a":-0}', b'{"a":"\\u0061"}', b'\xef\xbb\xbf{}', b'\xff',
            b'{"a":9223372036854775808}', b'{"a":-9223372036854775809}',
            b'{"a":"\\ud800"}', b'[' * 70 + b'0' + b']' * 70,
        ):
            with self.subTest(raw=raw), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.decode_document(raw, 1000)

    def test_document_rejects_non_json_types_integer_overflow_and_size_overflow(self):
        for value in (1.0, float("nan"), (1, 2), {1: "a"}, {"a": object()},
                      2**63, -(2**63)-1, "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.encode_document(value, 1000)
        for limit in (-1, 0, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.encode_document({}, limit)
        self.assertEqual(self.protocol.decode_document(b"9223372036854775807", 19), 2**63-1)
        self.assertEqual(self.protocol.decode_document(b"-9223372036854775808", 20), -(2**63))
        self.assertEqual(self.protocol.encode_document({}, 2), b"{}")
        for call in (lambda: self.protocol.encode_document({}, 1),
                     lambda: self.protocol.decode_document(b"{}", 1),
                     lambda: self.protocol.decode_document(bytearray(b"{}"), 100)):
            with self.assertRaises(self.protocol.ProtocolError):
                call()

    def test_frames_stream_across_arbitrary_boundaries_with_exact_state(self):
        reader = self.protocol.FrameReader("capture", ATTEMPT)
        self.assertFalse(reader.guarded)
        self.assertFalse(reader.owned)
        self.assertIsNone(reader.result)
        self.assertEqual(reader.sequence, -1)
        frames = [self.raw(), self.raw("progress", 1), self.raw("owned", 2),
                  self.raw("progress", 3), self.raw("result", 4, exit_code=143,
                      terminal=False, state="stopped", reason=None)]
        observed = []
        for byte in b"".join(frames):
            observed.extend(reader.feed(bytes([byte])))
        reader.finish()
        self.assertEqual([item["event"] for item in observed],
                         ["guarded", "progress", "owned", "progress", "result"])
        self.assertTrue(reader.guarded)
        self.assertTrue(reader.owned)
        self.assertEqual(reader.sequence, 4)
        self.assertEqual(reader.result["exit_code"], 143)
        self.assertEqual(reader.result["state"], "stopped")

    def test_result_may_precede_ownership_for_a_native_startup_refusal(self):
        reader = self.protocol.FrameReader("capture", ATTEMPT)
        result = self.protocol.encode_frame("result", ATTEMPT, "capture", 1,
            exit_code=3, terminal=True, state="blocked", reason="rate_blocked")
        self.assertEqual(self.protocol.encode_frame("guarded", ATTEMPT, "capture", 0), self.raw())
        reader.feed(self.raw() + result)
        reader.finish()
        self.assertFalse(reader.owned)
        self.assertTrue(reader.result["terminal"])

    def test_frames_reject_wrong_identity_unknown_fields_and_noncanonical_types(self):
        for changes in ({"attempt": "b"*32}, {"role": "stream"}, {"v": True},
                        {"v": 2}, {"sequence": True}, {"sequence": -1},
                        {"sequence": 2**53}, {"event": "SECRET/private"},
                        {"path": "/SECRET/private"}, {"role": "CAPTURE"}):
            with self.subTest(changes=changes), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.FrameReader("capture", ATTEMPT).feed(self.raw(**changes))
        for role, attempt in (("other", ATTEMPT), ("stream", "A"*32),
                              ("capture", "a"*31), (False, ATTEMPT)):
            with self.subTest(role=role, attempt=attempt), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.FrameReader(role, attempt)

    def test_frames_reject_ordering_gaps_duplicate_ownership_and_postresult_bytes(self):
        cases = [
            [self.raw("owned", 0)], [self.raw("progress", 0)],
            [self.raw(), self.raw("guarded", 1)],
            [self.raw(), self.raw("owned", 2)],
            [self.raw(), self.raw("owned", 1), self.raw("owned", 2)],
            [self.raw(), self.raw("result", 1, exit_code=0, terminal=False,
                                 state="stopped", reason=None), self.raw("progress", 2)],
        ]
        for frames in cases:
            with self.subTest(frames=frames), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.FrameReader("capture", ATTEMPT).feed(b"".join(frames))

    def test_results_require_exact_fields_codes_boolean_and_safe_labels(self):
        good = dict(exit_code=0, terminal=False, state="stopped", reason=None)
        bad = [dict(good, exit_code=True), dict(good, exit_code=1), dict(good, terminal=0),
               dict(good, state="/SECRET/path"), dict(good, reason="a"*65),
               dict(good, reason="bad-label"), dict(good, extra=None)]
        for field in good:
            missing = good.copy()
            missing.pop(field)
            bad.append(missing)
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(self.protocol.ProtocolError):
                self.protocol.encode_frame("result", ATTEMPT, "capture", 1, **fields)
        with self.assertRaises(self.protocol.ProtocolError):
            self.protocol.encode_frame("owned", ATTEMPT, "capture", 1, **good)

    def test_common_version_cannot_be_overridden_through_result_fields(self):
        with self.assertRaises(self.protocol.ProtocolError):
            self.protocol.encode_frame("guarded", ATTEMPT, "capture", 0, v=1)
        with self.assertRaises(self.protocol.ProtocolError):
            self.protocol.encode_frame("result", ATTEMPT, "capture", 1,
                exit_code=0, terminal=False, state=None, reason=None, v=1)

    def test_incomplete_oversized_and_nonbytes_streams_never_finish_successfully(self):
        for data in (b"", self.raw(), self.raw() + b"{", self.raw() + b"\n"):
            with self.subTest(data=data), self.assertRaises(self.protocol.ProtocolError):
                reader = self.protocol.FrameReader("capture", ATTEMPT)
                reader.feed(data)
                reader.finish()
        reader = self.protocol.FrameReader("capture", ATTEMPT)
        reader.feed(b"x" * 2047)
        with self.assertRaises(self.protocol.ProtocolError):
            reader.feed(b"x")
        with self.assertRaises(self.protocol.ProtocolError):
            self.protocol.FrameReader("capture", ATTEMPT).feed(b"x" * 2048 + b"\n")
        with self.assertRaises(self.protocol.ProtocolError):
            self.protocol.FrameReader("capture", ATTEMPT).feed("{}\n")

    def test_parser_failure_is_latched_and_large_valid_batches_stay_incremental(self):
        reader = self.protocol.FrameReader("capture", ATTEMPT)
        with self.assertRaises(self.protocol.ProtocolError):
            reader.feed(b"{}\n")
        with self.assertRaises(self.protocol.ProtocolError):
            reader.feed(self.raw())
        reader = self.protocol.FrameReader("capture", ATTEMPT)
        data = self.raw() + b"".join(self.raw("progress", i) for i in range(1, 100))
        self.assertEqual(len(reader.feed(data)), 100)
        self.assertEqual(reader.sequence, 99)


@unittest.skipUnless(sys.platform == "linux", "requires Linux parent-death signals")
class WorkerTests(unittest.TestCase):
    def setUp(self):
        try:
            self.worker = importlib.import_module("trading_bot.paper_service_worker")
        except ModuleNotFoundError:
            self.fail("the guarded service worker has not been implemented")
        self.protocol = importlib.import_module("trading_bot.paper_service_protocol")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def command(self, body, *arguments):
        return [sys.executable, "-I", "-B", "-u", "-c", _CHILD,
                str(ROOT), body, *map(str, arguments)]

    def invoke(self, body, *arguments):
        result = subprocess.run(self.command(body, *arguments), stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=15)
        self.assertNotIn(b"Traceback", result.stdout + result.stderr)
        self.assertNotIn(b"SECRET", result.stdout)
        return result

    def frames(self, result, *, role="stream"):
        reader = self.protocol.FrameReader(role, ATTEMPT)
        frames = reader.feed(result.stdout)
        reader.finish()
        self.assertEqual(reader.result["exit_code"], result.returncode)
        return frames

    def pair(self):
        from test_continuous_paper_cli import create_current_capture
        from trading_bot.paper_stream import run_stream
        source, account = self.root / "source", self.root / "account"
        create_current_capture(source)
        result = asyncio.run(run_stream(account, capture=source, max_polls=1))
        self.assertEqual(result["exit_code"], 0)
        return source, account

    def test_parent_mismatch_exits_before_native_import_or_operation(self):
        marker = self.root / "SECRET-never-created"
        result = self.invoke(r'''
assert 'trading_bot.paper_stream' not in sys.modules
def native(*args):Path(sys.argv[3]).write_text('unsafe');raise AssertionError('runtime entered')
worker._run_native=native
code=worker.worker_main('stream',sys.argv[3],'a'*32,os.getppid()+1)
assert 'trading_bot.paper_stream' not in sys.modules
raise SystemExit(code)
''', marker)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_parent_is_rechecked_after_kernel_guard_was_armed(self):
        marker = self.root / "SECRET-never-created"
        result = self.invoke(r'''
import ctypes
parent=os.getppid()
calls=[]
def parent_id():
 calls.append(True)
 return parent if len(calls)==1 else parent+1
worker.os.getppid=parent_id
def native(*args):Path(sys.argv[3]).write_text('unsafe')
worker._run_native=native
code=worker.worker_main('stream',sys.argv[3],'a'*32,parent)
observed=ctypes.c_int()
assert ctypes.CDLL(None).prctl(2,ctypes.byref(observed),0,0,0)==0
assert observed.value==signal.SIGKILL and len(calls)==2
raise SystemExit(code)
''', marker)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_unexpected_guard_setup_failure_is_private_and_never_enters_runtime(self):
        result = self.invoke(r'''
import ctypes
def unavailable(*args,**kwargs):raise RuntimeError('/SECRET/guard')
ctypes.CDLL=unavailable
def native(*args):raise AssertionError('native runtime entered')
worker._run_native=native
raise SystemExit(worker.worker_main('stream','unused','a'*32,os.getppid()))
''')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_guarded_frame_precedes_native_entry_and_sleep_preserves_duration(self):
        result = self.invoke(r'''
import ctypes,json
previous={n:signal.getsignal(n) for n in (signal.SIGINT,signal.SIGTERM)}
def native(role,directory,stop,sleep):
 assert role=='stream' and directory=='unused'
 observed=ctypes.c_int()
 assert ctypes.CDLL(None).prctl(2,ctypes.byref(observed),0,0,0)==0 and observed.value==9
 for _ in range(1000):assert stop()==0
 print('PRIVATE native stdout')
 start=time.monotonic();sleep(.12);elapsed=time.monotonic()-start
 assert elapsed>=.105 and elapsed<1
 sleep(.02)
 return {'exit_code':0,'observation':{'status':'stopped','control':{'phase':'stopped','last_error':None}}}
worker._run_native=native
code=worker.worker_main('stream','unused','a'*32,os.getppid())
assert all(signal.getsignal(n)==handler for n,handler in previous.items())
raise SystemExit(code)
''')
        self.assertEqual(result.returncode, 0)
        frames = self.frames(result)
        self.assertEqual([item["event"] for item in frames], ["guarded", "progress", "owned", "result"])
        self.assertIn(b"PRIVATE native stdout", result.stderr)
        self.assertNotIn(b"PRIVATE", result.stdout)

    def test_progress_is_rate_limited_without_adding_owned_before_sleep(self):
        result = self.invoke(r'''
def native(role,directory,stop,sleep):
 until=time.monotonic()+1.1
 while time.monotonic()<until:
  assert stop()==0
  time.sleep(.001)
 return {'exit_code':0,'observation':None}
worker._run_native=native
raise SystemExit(worker.worker_main('stream','unused','a'*32,os.getppid()))
''')
        frames = self.frames(result)
        self.assertEqual([item["event"] for item in frames], ["guarded", "progress", "progress", "result"])

    def test_signals_record_first_intent_restore_handlers_and_do_not_hide_storage_failure(self):
        for signum, fail in ((signal.SIGINT, False), (signal.SIGTERM, False),
                             (signal.SIGTERM, True)):
            with self.subTest(signum=signum, fail=fail):
                result = self.invoke(r'''
previous={n:signal.getsignal(n) for n in (signal.SIGINT,signal.SIGTERM)}
number=int(sys.argv[3]);fail=sys.argv[4]=='True'
def native(role,directory,stop,sleep):
 assert stop()==0
 sleep(.001)
 os.kill(os.getpid(),number)
 os.kill(os.getpid(),signal.SIGINT if number==signal.SIGTERM else signal.SIGTERM)
 assert stop()==number
 if fail:raise OSError('/SECRET/storage')
 return {'exit_code':128+number,'observation':{'status':'stopped','control':{'phase':'stopped','last_error':None}}}
worker._run_native=native
code=worker.worker_main('stream','unused','a'*32,os.getppid())
assert all(signal.getsignal(n)==handler for n,handler in previous.items())
raise SystemExit(code)
''', int(signum), fail)
                self.assertEqual(result.returncode, 4 if fail else 128 + int(signum))
                self.frames(result)

    def test_terminal_observations_remain_terminal_on_signal_results(self):
        cases = [
            ("capture", "catching_up", "fetching", None, True),
            ("capture", "blocked", "blocked", "transport_unavailable", True),
            ("capture", "stopped", "stopped", "rate_blocked", True),
            ("capture", "clock_error", "running", None, True),
            ("capture", "retrying", "retrying", "rate_limited", False),
            ("stream", "stopped", "stopped", "capacity", True),
            ("stream", "blocked", "blocked", "engine_rejected", True),
            ("stream", "clock_error", "running", None, True),
            ("stream", "waiting", "waiting", "source_not_current", False),
        ]
        for role, state, phase, reason, terminal in cases:
            with self.subTest(role=role, phase=phase, reason=reason):
                result = self.invoke(r'''
import json
role,state,phase,reason=json.loads(sys.argv[3])
def native(role,directory,stop,sleep):
 os.kill(os.getpid(),signal.SIGTERM)
 assert stop()==15
 return {'exit_code':143,'observation':{'status':state,
  ('capture' if role=='capture' else 'control'):{'phase':phase,'last_error':reason}}}
worker._run_native=native
raise SystemExit(worker.worker_main(role,'unused','a'*32,os.getppid()))
''', json.dumps([role, state, phase, reason]))
                final = self.frames(result, role=role)[-1]
                self.assertEqual(final["terminal"], terminal)
                self.assertEqual(final["state"], state)
                self.assertEqual(final["reason"], reason)
                self.assertEqual(final["exit_code"], 143)

    def test_native_failures_and_invalid_outcomes_have_fixed_private_frames(self):
        for kind, expected in (("value", 2), ("os", 4), ("runtime", 4), ("malformed", 4)):
            with self.subTest(kind=kind):
                result = self.invoke(r'''
def native(role,directory,stop,sleep):
 kind=sys.argv[3]
 if kind=='malformed':return {'exit_code':True,'observation':None}
 error={'value':ValueError,'os':OSError,'runtime':RuntimeError}[kind]
 raise error('/SECRET/private')
worker._run_native=native
raise SystemExit(worker.worker_main('stream','unused','a'*32,os.getppid()))
''', kind)
                self.assertEqual(result.returncode, expected)
                self.assertFalse(self.frames(result)[-1]["terminal"])

    def test_real_stream_worker_enters_owned_loop_then_signal_releases_native_lock(self):
        source, account = self.pair()
        from trading_bot.paper_stream_store import StreamUnavailable, stream_writer
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(number=number):
                process = subprocess.Popen(self.command(r'''
raise SystemExit(worker.worker_main('stream',sys.argv[3],'a'*32,os.getppid()))
''', account), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, start_new_session=True)
                try:
                    initial = _read_until(process, "owned")
                    with self.assertRaises(StreamUnavailable):
                        with stream_writer(account):
                            pass
                    process.send_signal(number)
                    stdout, stderr = process.communicate(timeout=8)
                    outcome = subprocess.CompletedProcess([], process.returncode, initial + stdout, stderr)
                    frames = self.frames(outcome)
                    self.assertEqual(outcome.returncode, 128 + int(number))
                    self.assertFalse(frames[-1]["terminal"])
                    self.assertEqual(frames[-1]["state"], "stopped")
                    with stream_writer(account) as owner:
                        self.assertEqual(owner.read()["engine"]["next_index"], 0)
                finally:
                    _stop_child(process)

    def test_real_capture_worker_uses_native_sleep_ownership_and_stops_without_provider(self):
        from test_continuous_paper_cli import create_current_capture
        from trading_bot.market_capture_store import CaptureUnavailable, capture_writer
        source = self.root / "capture"
        create_current_capture(source)
        process = subprocess.Popen(self.command(r'''
original=worker._run_native
def synthetic(role,directory,stop,sleep):
 from trading_bot import market_capture
 from test_market_capture import provider
 market_capture.UpbitHistoryClient=lambda **options:provider(options['clock'])
 return original(role,directory,stop,sleep)
worker._run_native=synthetic
raise SystemExit(worker.worker_main('capture',sys.argv[3],'a'*32,os.getppid()))
''', source), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
        try:
            initial = _read_until(process, "owned")
            with self.assertRaises(CaptureUnavailable):
                with capture_writer(source):
                    pass
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            result = subprocess.CompletedProcess([], process.returncode, initial + stdout, stderr)
            final = self.frames(result, role="capture")[-1]
            self.assertEqual(result.returncode, 143)
            self.assertEqual(final["state"], "stopped")
            self.assertFalse(final["terminal"])
            with capture_writer(source) as owner:
                self.assertEqual(owner.read()["state"]["last_error"], None)
        finally:
            _stop_child(process)

    def test_actual_parent_death_before_guard_and_after_owned_leaves_no_orphan_owner(self):
        for mode in ("before", "guarded", "after"):
            with self.subTest(mode=mode):
                case = self.root / mode
                case.mkdir()
                original_root, self.root = self.root, case
                source, account = self.pair()
                self.root = original_root
                before = (account / "stream.sqlite3").read_bytes()
                outcome = self.invoke(r'''
from test_paper_service_worker import _subreaper_drill
_subreaper_drill(Path(sys.argv[3]),sys.argv[4])
''', account, mode)
                self.assertEqual(outcome.returncode, 0, outcome.stderr.decode())
                report = json.loads(outcome.stdout)
                self.assertEqual(report["supervisor"], -signal.SIGKILL)
                self.assertEqual(report["worker"], 2 if mode == "before" else -signal.SIGKILL)
                self.assertTrue(report["lock_released"])
                self.assertTrue(report["stable"])
                if mode in {"before", "guarded"}:
                    self.assertEqual((account / "stream.sqlite3").read_bytes(), before)

    def test_real_terminal_capture_and_stream_refusals_survive_the_signal_result(self):
        source, account = self.pair()
        from trading_bot.market_capture_store import capture_writer
        from trading_bot.paper_stream_store import stream_writer
        from datetime import datetime, timezone
        with capture_writer(source) as owner:
            owner.fail("rate_blocked", at=datetime.now(timezone.utc))
        with stream_writer(account) as owner:
            owner.control("blocked", "capacity", at=datetime.now(timezone.utc))
        for role, path, reason in (("capture", source, "rate_blocked"),
                                   ("stream", account, "capacity")):
            with self.subTest(role=role):
                result = self.invoke(r'''
original=worker._run_native
def interrupted(role,directory,stop,sleep):
 calls=[]
 def signalling_stop():
  calls.append(True)
  if len(calls)==(1 if role=='capture' else 2):os.kill(os.getpid(),signal.SIGTERM)
  return stop()
 return original(role,directory,signalling_stop,sleep)
worker._run_native=interrupted
raise SystemExit(worker.worker_main(sys.argv[3],sys.argv[4],'a'*32,os.getppid()))
''', role, path)
                final = self.frames(result, role=role)[-1]
                self.assertEqual(final["exit_code"], 143)
                self.assertEqual(final["state"], "blocked")
                self.assertEqual(final["reason"], reason)
                self.assertTrue(final["terminal"])

    def test_fixed_cli_rejects_extra_arguments_and_never_prints_private_parser_errors(self):
        for arguments in ([], ["--help"], ["--role", "other"],
                          ["--role", "stream", "--directory", "SECRET-path", "--attempt", ATTEMPT,
                           "--parent-pid", "1", "--module", "os"]):
            with self.subTest(arguments=arguments):
                result = self.invoke(r'''
import json,runpy
sys.argv=[str(root/'tools/paper_service_worker.py'),*json.loads(sys.argv[3])]
runpy.run_path(sys.argv[0],run_name='__main__')
''', json.dumps(arguments))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"SECRET", result.stderr)

    def test_fixed_cli_with_valid_arguments_uses_guard_and_native_resume_only(self):
        result = self.invoke(r'''
import runpy
sys.argv=[str(root/'tools/paper_service_worker.py'),'--role','stream',
 '--directory',sys.argv[3],'--attempt','a'*32,'--parent-pid',str(os.getppid())]
runpy.run_path(sys.argv[0],run_name='__main__')
''', self.root / "SECRET-missing")
        self.assertEqual(result.returncode, 2)
        frames = self.frames(result)
        self.assertEqual(frames[0]["event"], "guarded")
        self.assertFalse(any(item["event"] == "owned" for item in frames))


if __name__ == "__main__":
    unittest.main()
