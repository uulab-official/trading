"""Real loopback HTTP checks; no engine or credentials are loaded by the reader."""
import importlib.util
import http.client
from pathlib import Path
import tempfile
import threading
import unittest
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
from trading_bot.walk_forward_research import run_research
from trading_bot.research_report import write_report

class ResearchAPITests(unittest.TestCase):

    def setUp(self):
        spec = importlib.util.spec_from_file_location('research_api_server', ROOT / 'apps/dashboard/server.py')
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.assertIn('research_path', self.m.create_server.__code__.co_varnames)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'report.json'
        self.report = run_research(dataset(), 'a' * 64, candidates=CANDIDATES, settings=SETTINGS, evaluator=lambda *a: metrics())
        write_report(self.path, self.report)
        self.server = self.m.create_server(port=0, research_path=self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path='/api/research', method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return (response.status, dict(response.getheaders()), response.read())
        finally:
            conn.close()

    def test_report_route_and_csp_no_journal_required(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), self.report)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request('/api/health')[0], 503)

    def test_absent_and_corrupt_are_unavailable_not_zero(self):
        for text in ('broken', '{"schema_version":99}'):
            self.path.write_text(text)
            status, _, body = self.request()
            self.assertEqual(status, 503)
            self.assertNotIn(b'mean_test_return_pct', body)
        self.path.unlink()
        self.assertEqual(self.request()[0], 503)

    def test_no_file_parameter_or_external_origin_or_write(self):
        before = self.path.read_bytes()
        self.assertEqual(self.request('/api/research?path=/etc/passwd')[0], 400)
        self.assertEqual(self.request(headers={'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.request(headers={'Host': 'example.com'})[0], 403)
        self.assertEqual(self.request(method='POST')[0], 405)
        self.assertEqual(self.path.read_bytes(), before)

    def test_assets_and_head_are_readonly(self):
        for route in ('/research.html', '/research.mjs', '/research.css'):
            self.assertEqual(self.request(route)[0], 200)
        status, headers, body = self.request(method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertEqual(self.request('/../research_report.py')[0], 404)
if __name__ == '__main__':
    unittest.main()
