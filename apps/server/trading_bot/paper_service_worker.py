"""Guard one native PAPER owner before importing or entering its runtime."""
from contextlib import redirect_stdout
import os
import signal
import sys
import threading
import time

from .paper_service_protocol import ProtocolError, encode_frame


_CAPTURE_TERMINAL = frozenset({"rate_blocked", "request_rejected", "cooldown_required",
    "data_rejected", "history_conflict", "catchup_limit", "clock_regression"})
_STREAM_TERMINAL = frozenset({"source_identity_changed", "source_history_conflict",
    "source_regression", "retention_lost", "source_blocked", "clock_regression",
    "capacity", "engine_rejected"})


def arm_parent_death(parent_pid):
    """Require the expected Linux parent before and after verified SIGKILL arming."""
    if (sys.platform != "linux" or type(parent_pid) is not int
            or not 1 <= parent_pid <= 2**31 - 1 or os.getppid() != parent_pid):
        raise ProtocolError("paper_service_parent_unavailable")
    # Trading imports wait for this verified guard. Normal workers do not fork,
    # exec or change credentials afterward.
    import ctypes
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                         ctypes.c_ulong, ctypes.c_ulong]
        if prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise ProtocolError("paper_service_parent_unavailable")
        actual = ctypes.c_int()
        if (prctl(2, ctypes.addressof(actual), 0, 0, 0) != 0  # PR_GET_PDEATHSIG
                or actual.value != int(signal.SIGKILL)
                or os.getppid() != parent_pid):
            raise ProtocolError("paper_service_parent_unavailable")
    except (AttributeError, OSError, TypeError, ValueError):
        raise ProtocolError("paper_service_parent_unavailable") from None


def _run_native(role, directory, stop, sleep):
    """Fixed native resume only; the function boundary is a trusted test seam."""
    if role == "capture":
        from .market_capture import collect_capture
        return collect_capture(directory, stop=stop, sleep=sleep)
    import asyncio
    from .paper_stream import run_stream
    return asyncio.run(run_stream(directory, stop=stop, sleep=sleep))


class _Frames:
    def __init__(self, role, attempt):
        self.role, self.attempt = role, attempt
        self.sequence = 0
        self.owned = False
        self.last_progress = None

    def emit(self, event, **fields):
        view = memoryview(encode_frame(event, self.attempt, self.role, self.sequence, **fields))
        while view:
            try:
                written = os.write(1, view)
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError("paper_service_worker_output_unconfirmed")
            view = view[written:]
        self.sequence += 1

    def progress(self):
        now = time.monotonic()
        if self.last_progress is None or now - self.last_progress >= 1.0:
            self.emit("progress")
            self.last_progress = time.monotonic()

    def sleep(self, seconds):
        if not self.owned:
            # Native runtimes call this hook from inside their acquired writer
            # context. Stream resume has also completed full replay and a poll.
            self.emit("owned")
            self.owned = True
        time.sleep(seconds)


def _failure(code, reason):
    return dict(exit_code=code, terminal=False, state=None, reason=reason)


def _outcome(role, result):
    if type(result) is not dict or "exit_code" not in result or "observation" not in result:
        raise ProtocolError("paper_service_native_result_invalid")
    observation = result["observation"]
    state = reason = None
    terminal = False
    if observation is not None:
        if type(observation) is not dict:
            raise ProtocolError("paper_service_native_result_invalid")
        state = observation.get("status")
        control = observation.get("capture" if role == "capture" else "control")
        if type(control) is not dict or "phase" not in control or "last_error" not in control:
            raise ProtocolError("paper_service_native_result_invalid")
        phase, reason = control["phase"], control["last_error"]
        if type(phase) is not str or (reason is not None and type(reason) is not str):
            raise ProtocolError("paper_service_native_result_invalid")
        terminal = (state == "clock_error" or phase == "blocked"
            or (role == "capture" and phase == "fetching")
            or reason in (_CAPTURE_TERMINAL if role == "capture" else _STREAM_TERMINAL))
    outcome = dict(exit_code=result["exit_code"], terminal=terminal, state=state, reason=reason)
    # The same strict wire schema validates native values before any result write.
    encode_frame("result", "0" * 32, role, 0, **outcome)
    return outcome


def worker_main(role, directory, attempt, parent_pid):
    """Operate one guarded owner; stdout contains only bounded protocol frames."""
    previous, received = {}, []

    def on_signal(signum, _frame):
        # First numeric intent only: no persistence, output or locks in handlers.
        if not received:
            received.append(int(signum))

    try:
        if (threading.current_thread() is not threading.main_thread()
                or type(directory) is not str or not 0 < len(directory) <= 4096
                or "\x00" in directory):
            return 2
        try:
            encode_frame("guarded", attempt, role, 0)
            for number in (signal.SIGINT, signal.SIGTERM):
                previous[number] = signal.signal(number, on_signal)
            arm_parent_death(parent_pid)
        except Exception:
            return 2

        frames = _Frames(role, attempt)
        frames.emit("guarded")

        def stop():
            frames.progress()
            return received[0] if received else 0

        with redirect_stdout(sys.stderr):
            try:
                result = _run_native(role, directory, stop, frames.sleep)
            except ValueError:
                outcome = _failure(2, "worker_unavailable")
            except OSError:
                outcome = _failure(4, "worker_storage_failed")
            except KeyboardInterrupt:
                outcome = _failure(128 + (received[0] if received else int(signal.SIGINT)),
                                   "worker_interrupted")
            except BaseException:
                outcome = _failure(4, "worker_failed")
            else:
                try:
                    outcome = _outcome(role, result)
                except (ValueError, TypeError, KeyError):
                    outcome = _failure(4, "worker_result_invalid")
        if received and outcome["exit_code"] in {0, 130, 143}:
            outcome["exit_code"] = 128 + received[0]
        frames.emit("result", **outcome)
        return outcome["exit_code"]
    except (OSError, ValueError, TypeError):
        # A broken protocol sink cannot receive a truthful final frame.
        return 4
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
