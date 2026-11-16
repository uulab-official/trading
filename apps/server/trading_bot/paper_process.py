"""CLI-only cooperative shutdown for local historical paper operations.

One signal requests one cancellation. Repeated signals must not interrupt the
operation's own asynchronous halt/close cleanup. This is not a hard deadline,
remote kill switch, account-status assertion or automatic recovery mechanism.
"""
import asyncio
import signal
import sys
import threading
from typing import Any, Callable, Coroutine, TypeVar

_T = TypeVar('_T')


class PaperProcessInterrupted(Exception):
    """A handled stop was requested; inspect saved state, do not infer a halt."""

    def __init__(self, signum: int):
        super().__init__('paper_process_interrupted_check_directory')
        self.signal_name = signal.Signals(signum).name
        self.exit_code = 128 + int(signum)


def run_paper_operation(factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run one CLI coroutine with temporary SIGINT/SIGTERM ownership.

    Never raise directly in a Python signal handler: it may interrupt a storage
    transaction. Schedule cancellation onto the loop and wait for the original
    coroutine to unwind. A cleanup exception takes precedence over interruption.
    SIGKILL, process death, blocked synchronous work and a coroutine that ignores
    cancellation cannot be made safe by this wrapper. No implicit restart occurs.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('paper_process_requires_main_thread')
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError('paper_process_requires_top_level_loop')
    if not callable(factory):
        raise TypeError('paper_process_requires_coroutine_factory')

    received = None
    cancellation_sent = False
    loop = task = None
    previous = {}
    installed = []

    def cancel_once():
        nonlocal cancellation_sent
        if task is not None and not task.done() and not cancellation_sent:
            cancellation_sent = True
            task.cancel()

    def request_stop(signum, _frame):
        nonlocal received
        if received is None:
            received = signum
            if loop is not None:
                try:
                    loop.call_soon_threadsafe(cancel_once)
                except RuntimeError:
                    # asyncio.run may already have closed the loop. The outer
                    # received check still rejects success; never raise here.
                    pass
        # Further SIGINT/SIGTERM requests do not cancel the halt finalizer again.

    async def entry():
        nonlocal loop, task
        loop = asyncio.get_running_loop()
        if received is not None:
            raise PaperProcessInterrupted(received)
        task = asyncio.create_task(factory())
        try:
            result = await task
        except asyncio.CancelledError:
            if received is not None:
                raise PaperProcessInterrupted(received) from None
            raise
        # A coroutine may suppress cancellation or finish before cancel_once ran.
        if received is not None:
            raise PaperProcessInterrupted(received)
        return result

    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.getsignal(number)
            signal.signal(number, request_stop)
            installed.append(number)
        result = asyncio.run(entry())
        if received is not None:
            raise PaperProcessInterrupted(received)
        return result
    finally:
        # The original handlers also apply again when this CLI is called in tests.
        active_error = sys.exc_info()[1]
        restore_error = None
        for number in reversed(installed):
            try:
                signal.signal(number, previous[number])
            except Exception as error:
                if restore_error is None:
                    restore_error = error
        if restore_error is not None and (
                active_error is None or isinstance(active_error, PaperProcessInterrupted)):
            raise restore_error
