"""Actual localhost HTTP and CLI; no broker or credentials."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.paper_session import PaperTradingSession


class ObserverRouteContract(unittest.TestCase):
    def test_server_declares_opt_in_source(self):
        self.assertIn('--paper-session', (ROOT/'apps/dashboard/server.py').read_text())


class ObserverAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'private-account.sqlite'
        self.session = PaperTradingSession(self.path, create=True, initial_balance='1000', enable_control=True)
        await self.session.__aenter__()
        spec = importlib.util.spec_from_file_location('session_observer_server', ROOT/'apps/dashboard/server.py')
        self.module = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.server = self.module.create_server(None, 0, paper_session_path=self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    async def asyncTearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
        await self.session.close();self.temp.cleanup()

    def get(self, path='/api/paper-session', **kwargs):
        try:
            with urlopen(Request(self.base+path, **kwargs), timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as response:
            return response.code, response.read(), response.headers

    async def test_read_with_active_owner_and_security_headers(self):
        before = self.path.read_bytes()
        status, raw, headers = self.get()
        self.assertEqual(status, 200)
        data=json.loads(raw)
        self.assertEqual(data['control']['mode'], 'halted')
        self.assertTrue(data['read_only']);self.assertFalse(data['live_enabled'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn(str(self.path).encode(), raw)

    async def test_query_injection_cross_origin_and_all_writes_are_denied(self):
        before=self.path.read_bytes()
        for suffix in ('?path=/etc/passwd','?limit=100','?x=1&x=2','?x'):
            self.assertEqual(self.get('/api/paper-session'+suffix)[0],400)
        self.assertEqual(self.get(headers={'Origin':'https://untrusted.invalid'})[0],403)
        self.assertEqual(self.get(headers={'Host':'untrusted.invalid'})[0],403)
        self.assertEqual(self.get(headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE'):
            self.assertEqual(self.get(method=method,data=b'{}')[0],405)
        self.assertEqual(before,self.path.read_bytes())

    async def test_missing_source_is_unavailable_not_empty_account(self):
        self.server.RequestHandlerClass.paper_session_observer = None
        status,body,_=self.get()
        self.assertEqual(status,503)
        self.assertNotIn('account',json.loads(body))

    async def test_mode_updates_are_visible_without_server_restart(self):
        first=json.loads(self.get()[1])
        await self.session.set_operating_mode('active',expected_generation=0,reason='operator')
        second=json.loads(self.get()[1])
        self.assertEqual(second['control']['mode'],'active')
        self.assertGreater(int(second['revision']),int(first['revision']))
        self.assertEqual(second['observation']['process_state'],'unobserved')

    async def test_head_has_no_body_or_source_write(self):
        before=self.path.read_bytes()
        status,body,_=self.get(method='HEAD')
        self.assertEqual((status,body),(200,b''))
        self.assertEqual(before,self.path.read_bytes())

    async def test_static_assets_only_and_no_write_control(self):
        for route in ('/paper-account.html','/paper-account.css','/paper-account.mjs'):
            status,body,_=self.get(route)
            self.assertEqual(status,200);self.assertGreater(len(body),100)
        self.assertEqual(self.get('/api/paper-session/control')[0],404)
        self.assertEqual(self.get('/../private-account.sqlite')[0],404)
        self.assertEqual(self.get('/paper-account.html?path=secret')[0],400)

    async def test_observer_busy_recovers_and_other_sources_remain_separate(self):
        observer=self.server.RequestHandlerClass.paper_session_observer
        observer._gate.acquire()
        try:self.assertEqual(self.get()[0],503)
        finally:observer._gate.release()
        self.assertEqual(self.get()[0],200)
        self.assertEqual(self.get('/api/research')[0],503)
        self.assertEqual(self.get('/api/snapshot')[0],503)
        self.assertEqual(self.get()[0],200)

    async def test_cli_reports_same_identity_without_changing_source(self):
        before=self.path.read_bytes()
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_session_status.py'),
            '--database',str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        actual=json.loads(result.stdout);expected=json.loads(self.get()[1])
        for key in ('account_id','digest','revision','control','account','orders'):
            self.assertEqual(actual[key],expected[key])
        self.assertEqual(before,self.path.read_bytes())

    async def test_cli_missing_file_fails_without_creation_or_path_leak(self):
        missing=self.path.parent/'missing.sqlite'
        result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/paper_session_status.py'),
            '--database',str(missing)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_session_unavailable'})
        self.assertFalse(missing.exists())


if __name__=='__main__':unittest.main()
