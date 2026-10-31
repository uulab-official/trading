"""Demo -> durable journal -> local HTTP API, with no exchange/network dependency."""
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from decimal import Decimal as D

ROOT = Path(__file__).resolve().parents[1]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PaperIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'demo.sqlite'
        self.demo = ROOT / 'tools/paper_operations_demo.py'

    def tearDown(self):
        self.tmp.cleanup()

    def run_demo(self):
        return subprocess.run([sys.executable, str(self.demo), '--journal', str(self.path)],
                              capture_output=True, text=True, timeout=10)

    def test_demo_refuses_overwrite(self):
        self.assertTrue(self.demo.exists(), 'explicit synthetic journal demo required')
        first = self.run_demo()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.path.read_bytes()
        second = self.run_demo()
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_demo_journal_restart_and_api_agree(self):
        self.assertTrue(self.demo.exists(), 'explicit synthetic journal demo required')
        result = self.run_demo()
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = load('integration_journal', 'packages/core/order_journal.py')
        server_module = load('integration_dashboard', 'apps/dashboard/server.py')
        with journal.OrderJournal(str(self.path)) as writer:
            self.assertEqual(writer.reserved_total(), D('280000'))
            self.assertEqual(len(writer.pending_reconciliation()), 2)
        with server_module.create_server(self.path, 0) as server:
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
            thread.start()
            def read():
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    conn.request('GET', '/api/snapshot')
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    return json.loads(response.read())
                finally:
                    conn.close()
            try:
                state = read()
                self.assertEqual(state['source'], 'synthetic_demo')
                self.assertEqual(state['summary']['total_orders'], 6)
                self.assertEqual(state['summary']['active_orders'], 4)
                self.assertEqual(state['summary']['reserved_by_currency'], {'KRW':'280000'})
                self.assertEqual(state['summary']['needs_reconciliation'], 2)
                self.assertTrue(state['audit_available'])
                self.assertGreater(len(state['events']), 6)
                with journal.OrderJournal(str(self.path)) as writer:
                    writer.confirm_cancelled('demo-003')
                refreshed = read()
                self.assertEqual(refreshed['summary']['needs_reconciliation'], 1)
                self.assertEqual(refreshed['summary']['reserved_by_currency'], {'KRW':'220000'})
            finally:
                server.shutdown()
                thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
