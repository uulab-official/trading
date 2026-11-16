import importlib.util
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from test_replay_report import load_report_module, result_fixture
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('replay_dashboard_server', ROOT/'apps/dashboard/server.py')
server_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(server_module)


class ReplayAPITests(unittest.TestCase):
    def setUp(self):
        self.assertIn('replay_path', inspect.signature(server_module.create_server).parameters,
                      'server must accept an independent replay source')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'report.json'
        report = load_report_module()
        value = report.build_report(result_fixture(), fixture_sha256='a'*64,
                                    first_bar_ms=0, last_bar_ms=7200000, mark_price='110')
        report.write_report(self.path, value)
        self.server = server_module.create_server(None, 0, replay_path=self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def get(self, path='/api/replay', **kwargs):
        try:
            with urlopen(Request(self.base+path, **kwargs), timeout=3) as response:
                return response.status, json.load(response), response.headers
        except HTTPError as response:
            return response.code, json.load(response), response.headers

    def test_report_works_without_admission_journal(self):
        status, data, headers = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data['fill_count'], 2)
        self.assertEqual(data['account']['currency'], 'USDT')
        self.assertFalse(data['live_enabled'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.get('/api/snapshot')[0], 503)

    def test_missing_corrupt_and_inconsistent_reports_are_unavailable(self):
        for content in ['{', '{}', '{"schema_version":999}']:
            self.path.write_text(content)
            status, data, _ = self.get()
            self.assertEqual(status, 503)
            self.assertEqual(data['error'], 'replay_unavailable')
            self.assertNotIn(str(self.path), json.dumps(data))
        self.path.unlink()
        self.assertEqual(self.get()[0], 503)

    def test_queries_cross_origin_and_mutations_are_denied(self):
        for path in ['/api/replay?path=/etc/passwd', '/api/replay?x=1', '/api/replay?limit=1']:
            self.assertEqual(self.get(path)[0], 400)
        self.assertEqual(self.get(headers={'Origin':'https://untrusted.invalid'})[0], 403)
        self.assertEqual(self.get(method='POST', data=b'{}')[0], 405)

    def test_head_does_not_return_body_or_write_report(self):
        before = self.path.read_bytes()
        with urlopen(Request(self.base+'/api/replay', method='HEAD')) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b'')
        self.assertEqual(before, self.path.read_bytes())

    def test_journal_backward_compatibility(self):
        dbpath = Path(self.tmp.name)/'journal.sqlite'
        with sqlite3.connect(dbpath) as db:
            db.execute('CREATE TABLE orders(client_id TEXT,status TEXT,reserved TEXT,updated_at TEXT)')
            db.execute("INSERT INTO orders VALUES('legacy','unknown','12','2026-01-01')")
        self.server.RequestHandlerClass.journal_path = dbpath
        status, data, _ = self.get('/api/orders')
        self.assertEqual(status, 200)
        self.assertEqual(data['orders'][0]['reserved'], '12')
        self.assertFalse(data['accounting_available'])


if __name__ == '__main__': unittest.main()
