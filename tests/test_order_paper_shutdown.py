"""Cooperative shutdown: real process signals, real paper persistence, no broker."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))


def controller(test):
    spec = importlib.util.find_spec('trading_bot.paper_process')
    test.assertIsNotNone(spec, 'paper process shutdown controller is missing')
    return importlib.import_module('trading_bot.paper_process')


class ShutdownControllerTests(unittest.TestCase):
    def test_normal_result_and_handlers_restored(self):
        module = controller(self)
        old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        async def operation():
            await asyncio.sleep(0)
            return {'value': 12}
        self.assertEqual(module.run_paper_operation(operation), {'value': 12})
        self.assertEqual(old, {s: signal.getsignal(s) for s in old})

    def test_sigterm_cleans_up_then_reports_143(self):
        module = controller(self)
        events = []
        async def operation():
            try:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(0)
                events.append('cleaned')
        with self.assertRaises(module.PaperProcessInterrupted) as caught:
            module.run_paper_operation(operation)
        self.assertEqual(events, ['cleaned'])
        self.assertEqual(caught.exception.exit_code, 143)
        self.assertEqual(caught.exception.signal_name, 'SIGTERM')

    def test_sigint_is_130_not_success(self):
        module = controller(self)
        async def operation():
            signal.raise_signal(signal.SIGINT)
            await asyncio.sleep(10)
        with self.assertRaises(module.PaperProcessInterrupted) as caught:
            module.run_paper_operation(operation)
        self.assertEqual(caught.exception.exit_code, 130)

    def test_repeated_mixed_signals_do_not_interrupt_async_cleanup(self):
        module = controller(self)
        events = []
        async def operation():
            try:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(10)
            finally:
                for s in (signal.SIGINT, signal.SIGTERM, signal.SIGINT):
                    signal.raise_signal(s)
                    await asyncio.sleep(0)
                    events.append(s)
        with self.assertRaises(module.PaperProcessInterrupted) as caught:
            module.run_paper_operation(operation)
        self.assertEqual(len(events), 3)
        self.assertEqual(caught.exception.exit_code, 143)

    def test_suppressed_cancellation_cannot_report_success(self):
        module = controller(self)
        async def operation():
            try:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return 'not a success'
        with self.assertRaises(module.PaperProcessInterrupted):
            module.run_paper_operation(operation)

    def test_late_signal_before_synchronous_return_is_not_success(self):
        module = controller(self)
        async def operation():
            signal.raise_signal(signal.SIGTERM)
            return 42
        with self.assertRaises(module.PaperProcessInterrupted):
            module.run_paper_operation(operation)

    def test_signal_after_loop_close_is_still_an_interruption(self):
        module = controller(self)
        original = asyncio.run
        async def operation():
            return 42
        def late_signal(coroutine):
            result = original(coroutine)
            signal.raise_signal(signal.SIGTERM)
            return result
        with patch.object(asyncio, 'run', late_signal):
            with self.assertRaises(module.PaperProcessInterrupted):
                module.run_paper_operation(operation)

    def test_signal_does_not_raise_inside_synchronous_section(self):
        module = controller(self)
        events = []
        async def operation():
            signal.raise_signal(signal.SIGINT)
            events.append('sync-finished')
            await asyncio.sleep(0)
            events.append('must-not-run')
        with self.assertRaises(module.PaperProcessInterrupted):
            module.run_paper_operation(operation)
        self.assertEqual(events, ['sync-finished'])

    def test_cleanup_error_has_priority_over_signal(self):
        module = controller(self)
        async def operation():
            try:
                signal.raise_signal(signal.SIGTERM)
                await asyncio.sleep(10)
            finally:
                raise RuntimeError('durable_replay_stop_unconfirmed')
        with self.assertRaisesRegex(RuntimeError, 'durable_replay_stop_unconfirmed'):
            module.run_paper_operation(operation)

    def test_unsignalled_cancellation_is_preserved(self):
        module = controller(self)
        async def operation():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            module.run_paper_operation(operation)

    def test_factory_error_restores_prior_custom_handlers(self):
        module = controller(self)
        old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        def custom(*args):
            pass
        def bad():
            raise ValueError('factory failure')
        try:
            for s in old:
                signal.signal(s, custom)
            with self.assertRaises(ValueError):
                module.run_paper_operation(bad)
            self.assertTrue(all(signal.getsignal(s) is custom for s in old))
        finally:
            for s, handler in old.items():
                signal.signal(s, handler)

    def test_partial_handler_install_failure_does_not_start_operation(self):
        module = controller(self)
        original = signal.signal
        previous = signal.getsignal(signal.SIGINT)
        calls = []
        def install(s, handler):
            if s == signal.SIGTERM:
                raise OSError('install denied')
            return original(s, handler)
        with patch.object(signal, 'signal', install):
            with self.assertRaises(OSError):
                module.run_paper_operation(lambda: calls.append('started'))
        self.assertEqual(calls, [])
        self.assertEqual(signal.getsignal(signal.SIGINT), previous)

    def test_restore_error_does_not_leave_other_handler_installed(self):
        module = controller(self)
        original = signal.signal
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        async def operation():
            return 42
        def install(s, handler):
            if s == signal.SIGTERM and handler == before[s]:
                raise OSError('restore failure')
            return original(s, handler)
        try:
            with patch.object(signal, 'signal', install):
                with self.assertRaises(OSError):
                    module.run_paper_operation(operation)
            self.assertEqual(signal.getsignal(signal.SIGINT), before[signal.SIGINT])
        finally:
            for s, handler in before.items():
                original(s, handler)

    def test_signal_while_installing_handlers_prevents_factory(self):
        module = controller(self)
        original = signal.signal
        calls = []
        def install(s, handler):
            previous = original(s, handler)
            if s == signal.SIGTERM and callable(handler):
                signal.raise_signal(signal.SIGTERM)
            return previous
        with patch.object(signal, 'signal', install):
            with self.assertRaises(module.PaperProcessInterrupted):
                module.run_paper_operation(lambda: calls.append('started'))
        self.assertEqual(calls, [])

    def test_worker_thread_refused_before_factory(self):
        module = controller(self)
        calls, errors = [], []
        def worker():
            try:
                module.run_paper_operation(lambda: calls.append('started'))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(calls, [])
        self.assertEqual(len(errors), 1)

    def test_nested_loop_refused_before_factory(self):
        module = controller(self)
        calls = []
        async def parent():
            with self.assertRaises(RuntimeError):
                module.run_paper_operation(lambda: calls.append('started'))
        asyncio.run(parent())
        self.assertEqual(calls, [])


# Patch only timing in a child; use the real CLI, original engine and SQLite.
CHILD = r'''
import asyncio, importlib.util, os, sys
from pathlib import Path
root, target, ready, stage, command = map(str, sys.argv[1:])
for part in ('packages/core','packages/data','packages/strategy','apps/server'):
    sys.path.insert(0,str(Path(root)/part))
from trading_core.paper_session import _PaperAdapter, PaperTradingSession
from trading_bot.paper_runner import PaperRunner
original_order = _PaperAdapter.create_order
async def delayed_order(self,*args,**kwargs):
    result = await original_order(self,*args,**kwargs)
    if stage in ('saved_fill','failed_halt','repeated'):
        Path(ready).touch()
        await asyncio.sleep(60)
    return result
_PaperAdapter.create_order = delayed_order
original_mode = PaperTradingSession.set_operating_mode
async def mode(self,value,**kwargs):
    if value == 'halted' and stage == 'failed_halt':
        raise OSError('PRIVATE_HALT_DETAIL')
    if value == 'halted' and stage == 'repeated':
        Path(ready+'.halt').touch()
        await asyncio.sleep(.3)
    return await original_mode(self,value,**kwargs)
PaperTradingSession.set_operating_mode = mode
if stage == 'before_strategy':
    original_run = PaperRunner._run
    async def delayed_run(self,*args,**kwargs):
        Path(ready).touch()
        await asyncio.sleep(60)
        return await original_run(self,*args,**kwargs)
    PaperRunner._run = delayed_run
name = 'paper_operate' if command == 'run' else 'paper_resume'
spec = importlib.util.spec_from_file_location('cli_under_test',Path(root)/'tools'/ (name+'.py'))
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
args = ['--demo','--resumable','--directory',target] if command=='run' else [target,'--reconcile-saved-fills']
raise SystemExit(cli.main(args))
'''


class ShutdownProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def wait_for(self, marker, proc):
        deadline = time.monotonic()+15
        while not marker.exists():
            if proc.poll() is not None:
                out, err = proc.communicate()
                self.fail(f'Child exited before marker: {proc.returncode}\n{out}\n{err}')
            if time.monotonic()>deadline:
                self.fail('Child readiness deadline expired')
            time.sleep(.01)

    def stop(self, stage='saved_fill', sig=signal.SIGTERM, command='run', target=None):
        target = target or self.root/'run'
        ready = self.root/f'ready-{command}'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen([sys.executable,'-B','-c',CHILD,str(ROOT),str(target),
            str(ready),stage,command],cwd=ROOT,env=env,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True)
        try:
            self.wait_for(ready,proc)
            proc.send_signal(sig)
            if stage == 'repeated':
                self.wait_for(Path(str(ready)+'.halt'),proc)
                proc.send_signal(signal.SIGINT)
                proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill();proc.communicate()
        return proc.returncode,out,err,target

    def observed(self,target):
        from trading_core.paper_session_observer import PaperSessionObserver
        return PaperSessionObserver(target/'account.sqlite').read()

    def test_sigterm_after_saved_fill_halts_without_losing_or_resubmitting(self):
        code,out,err,target = self.stop()
        self.assertEqual(code,143,err)
        report = json.loads(out)
        self.assertEqual(report['signal'],'SIGTERM')
        self.assertNotIn('PRIVATE_',out)
        observed=self.observed(target)
        self.assertEqual(observed['orders']['account_fills'],1)
        self.assertEqual(observed['orders']['unresolved'],1)
        self.assertEqual(observed['control']['mode'],'halted')
        self.assertFalse((target/'result.json').exists())
        # Exact original saved-fill recovery remains explicit and completes.
        completed=subprocess.run([sys.executable,'-B','tools/paper_resume.py',str(target),
            '--reconcile-saved-fills'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(completed.returncode,0,completed.stderr+completed.stdout)
        result=json.loads(completed.stdout)
        self.assertEqual(result['recovery']['existing_fills_skipped'],1)
        self.assertEqual(result['recovery']['saved_fills_reconciled'],1)
        self.assertEqual(result['status'],'completed_halted')

    def test_sigint_after_saved_fill_is_130_and_halted(self):
        code,out,err,target=self.stop(sig=signal.SIGINT)
        self.assertEqual(code,130,err)
        self.assertIn('signal',json.loads(out))
        self.assertEqual(json.loads(out)['signal'],'SIGINT')
        self.assertEqual(self.observed(target)['control']['mode'],'halted')

    def test_signal_before_strategy_has_no_fill_and_releases_owner(self):
        code,out,err,target=self.stop(stage='before_strategy')
        self.assertEqual(code,143,err)
        self.assertEqual(self.observed(target)['orders']['account_fills'],0)
        self.assertEqual(self.observed(target)['control']['mode'],'halted')
        from trading_core.paper_session import PaperTradingSession
        async def acquire():
            async with PaperTradingSession(target/'account.sqlite'):
                pass
        asyncio.run(acquire())

    def test_repeated_signals_preserve_async_halt(self):
        code,out,err,target=self.stop(stage='repeated')
        self.assertEqual(code,143,err)
        self.assertEqual(json.loads(out)['signal'],'SIGTERM')
        self.assertEqual(self.observed(target)['control']['mode'],'halted')

    def test_failed_halt_is_not_reported_as_clean_signal_exit(self):
        code,out,err,target=self.stop(stage='failed_halt')
        self.assertEqual(code,3,err)
        self.assertEqual(json.loads(out)['error'],'durable_replay_stop_unconfirmed')
        self.assertNotIn('PRIVATE_HALT_DETAIL',out)
        self.assertFalse((target/'result.json').exists())

    def test_signal_inside_actual_sqlite_commit_does_not_break_transaction(self):
        module = controller(self)
        from trading_core.paper_session import _SessionStore
        from trading_bot.durable_paper_run import run_durable_replay
        from test_order_durable_run import fixture
        commit = _SessionStore._commit
        events = []
        def interrupt_at_commit(store):
            payload = json.loads(store._db.execute(
                'SELECT payload FROM paper_session').fetchone()[0])
            if not events and payload['book']['trades']:
                signal.raise_signal(signal.SIGTERM)
                events.append('signal before commit')
                commit(store)
                events.append('commit returned')
            else:
                commit(store)
        target=self.root/'critical'
        with patch.object(_SessionStore,'_commit',interrupt_at_commit):
            with self.assertRaises(module.PaperProcessInterrupted):
                module.run_paper_operation(lambda: run_durable_replay(
                    fixture(),directory=target,resumable=True))
        self.assertEqual(events,['signal before commit','commit returned'])
        observed=self.observed(target)
        self.assertEqual(observed['orders']['account_fills'],1)
        self.assertEqual(observed['control']['mode'],'halted')
        self.assertFalse((target/'result.json').exists())

    def test_repeat_resume_after_signal_preserves_original_full_report(self):
        from trading_bot.paper_runner import PaperRunner, PaperRunConfig
        code,_,err,target=self.stop()
        self.assertEqual(code,143,err)
        result=subprocess.run([sys.executable,'-B','tools/paper_resume.py',str(target),
            '--reconcile-saved-fills'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        frozen=json.loads((target/'input.json').read_text())
        expected=asyncio.run(PaperRunner(PaperRunConfig(**frozen['config'])).run(frozen['candles']))
        self.assertEqual(json.loads(result.stdout)['replay'],expected)
        before=(target/'account.sqlite').read_bytes()
        repeated=subprocess.run([sys.executable,'-B','tools/paper_resume.py',str(target)],
            cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(repeated.returncode,2)
        self.assertEqual((target/'account.sqlite').read_bytes(),before)

    def test_resume_sigterm_also_halts_and_remains_incomplete(self):
        code,_,err,target=self.stop()
        self.assertEqual(code,143,err)
        code,out,err,target=self.stop(command='resume',target=target)
        self.assertEqual(code,143,err)
        self.assertEqual(json.loads(out)['signal'],'SIGTERM')
        self.assertEqual(self.observed(target)['control']['mode'],'halted')
        self.assertEqual(self.observed(target)['orders']['account_fills'],2)
        self.assertFalse((target/'result.json').exists())


if __name__=='__main__':
    unittest.main()
