"""Actual JSON CLI and OS signal lifecycle; synthetic provider only."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_market_capture import ROOT, CONFIG, Clock, provider


def load_cli():
    spec = importlib.util.spec_from_file_location('capture_cli_test', ROOT/'tools/market_capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaptureCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = load_cli().main(args)
        return code, json.loads(output.getvalue())

    def test_invalid_arguments_emit_one_fixed_json_before_network_or_files(self):
        for args in [[], ['resume'], ['run','--directory',str(self.root/'new')],
                     ['check',str(self.root/'secret-missing')], ['resume',str(self.root/'secret-missing')],
                     ['export',str(self.root/'secret-missing'),'--output',str(self.root/'out.json')],
                     ['run','--market','KRW-BTC','--directory',str(self.root/'new'),'--max-polls','0']]:
            with self.subTest(args=args), patch('socket.create_connection', side_effect=AssertionError('network')):
                code, data = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertNotIn(str(self.root), json.dumps(data))
        self.assertFalse((self.root/'new').exists())

    def test_stopped_check_and_export_work_in_fresh_python_process(self):
        from trading_bot.market_capture import collect_capture
        directory = self.root/'capture'
        clock = Clock()
        collect_capture(directory, config=CONFIG, max_polls=1,
                        clock=clock, client=provider(clock), sleep=clock.sleep)
        before = {p.name:p.read_bytes() for p in directory.iterdir()}
        result = subprocess.run([sys.executable, str(ROOT/'tools/market_capture.py'), 'check', str(directory)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'stopped')
        output = self.root/'historical.json'
        result = subprocess.run([sys.executable, str(ROOT/'tools/market_capture.py'), 'export',str(directory),
            '--output',str(output),'--count','2'], capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['bar_count'], 2)
        self.assertEqual(before, {p.name:p.read_bytes() for p in directory.iterdir()})
        code, data = self.invoke(['export',str(directory),'--output',str(output)])
        self.assertEqual(code, 4)
        self.assertEqual(data['error'], 'market_capture_storage_failed')

    def test_real_signals_emit_single_json_restore_handlers_and_reject_late_data(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=int(signum)):
                directory = self.root/str(int(signum))
                script = r'''
import importlib.util,json,signal,sys,time
from datetime import datetime,timezone
from pathlib import Path
root=Path(sys.argv[1])
for part in ('apps/server','packages/data','tests'):sys.path.insert(0,str(root/part))
from trading_bot import market_capture as capture
from test_market_capture import fixture
spec=importlib.util.spec_from_file_location('capture_cli_signal',root/'tools/market_capture.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
class Client:
 def __init__(self,**kwargs):pass
 def download(self,*args,**kwargs):
  print('READY',file=sys.stderr,flush=True)
  time.sleep(.3)
  return fixture(lambda:datetime.now(timezone.utc),count=3,end=kwargs['end'])
capture.UpbitHistoryClient=Client
previous={x:signal.getsignal(x) for x in (signal.SIGINT,signal.SIGTERM)}
code=cli.main(['run','--market','KRW-BTC','--history','3','--directory',sys.argv[2]])
assert all(signal.getsignal(x)==v for x,v in previous.items())
raise SystemExit(code)
'''
                process = subprocess.Popen([sys.executable,'-c',script,str(ROOT),str(directory)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    ready, _, _ = select.select([process.stderr], [], [], 8)
                    self.assertTrue(ready, 'child never entered acquisition')
                    line = process.stderr.readline()
                    self.assertEqual(line.strip(), 'READY', line)
                    os.kill(process.pid, signum)
                    stdout, stderr = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 128+int(signum), stderr)
                    report = json.loads(stdout)
                    self.assertEqual(report['operation'], 'collect')
                    self.assertIs(report['read_only'], False)
                    self.assertEqual(report['observation']['window']['bars'], 0)
                    self.assertEqual(report['observation']['status'], 'stopped')
                finally:
                    if process.poll() is None:
                        process.kill(); process.communicate()


if __name__ == '__main__':
    unittest.main()
