"""Independent passive status route preserves the four-source readiness API."""
import json
from pathlib import Path
import tempfile
import unittest

from test_dashboard_runtime import load_server, request, running_server
from test_market_capture import CONFIG, Clock, fixture


class CaptureApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.module = load_server()

    def test_unconfigured_and_missing_capture_are_sanitized_and_not_created(self):
        for path in (None, self.root/'SECRET-missing'):
            with self.subTest(path=path), running_server(self.module, market_capture_path=path) as server:
                code, _, raw = request(server, '/api/market-capture')
                self.assertEqual(code, 503)
                self.assertEqual(json.loads(raw), dict(error='market_capture_unavailable',
                    mode='paper', read_only=True, live_enabled=False))
                self.assertNotIn(b'SECRET', raw)
        self.assertFalse((self.root/'SECRET-missing').exists())

    def test_capture_read_is_passive_and_existing_readiness_contract_is_unchanged(self):
        from trading_bot.market_capture_store import capture_writer
        directory = self.root/'capture'
        clock = Clock()
        with capture_writer(directory, config=CONFIG, now=clock()) as writer:
            writer.attempt(clock())
            writer.apply(fixture(clock), at=clock())
        before = {p.name:p.read_bytes() for p in directory.iterdir()}
        with running_server(self.module, market_capture_path=directory) as server:
            code, headers, raw = request(server, '/api/market-capture')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw)['status'], 'stale')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            code, _, raw = request(server, '/api/readiness')
            self.assertEqual(code, 503)
            self.assertEqual(set(json.loads(raw)['sources']), {'journal','replay','research','paper_session'})
            self.assertEqual(request(server, '/api/market-capture', method='HEAD')[2], b'')
            self.assertEqual(request(server, '/api/market-capture?extra=1')[0], 400)
            self.assertEqual(request(server, '/api/market-capture', method='POST')[0], 405)
            self.assertEqual(request(server, '/api/market-capture', headers={'Origin':'https://evil.test'})[0], 403)
        self.assertEqual(before, {p.name:p.read_bytes() for p in directory.iterdir()})


if __name__ == '__main__':
    unittest.main()
