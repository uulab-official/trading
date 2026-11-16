"""Public import compatibility and credential-free observer CLI boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = {
    'exchange': ['ExchangeInterface', 'CCXTExchange'],
    'paper_exchange': ['PaperExchange'],
    'execution': ['create_execution_exchange', 'validate_execution_mode'],
    'order_manager': ['OrderManager', 'Order', 'OrderSide', 'OrderType', 'OrderStatus'],
    'risk_manager': ['RiskManager', 'RiskConfig'],
    'portfolio': ['Portfolio', 'Position'],
    'state_store': ['StateStore', 'SQLiteStateStore', 'BotState'],
    'config_manager': ['ConfigManager', 'load_config'],
    'telegram_notifier': ['TelegramNotifier', 'NotificationConfig', 'NullNotifier'],
}
PUBLIC = [name for names in EXPORTS.values() for name in names]
BOOTSTRAP = '''
import sys
from pathlib import Path
ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / 'packages/core'))
def no_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname'):
        raise AssertionError('network forbidden in import test')
sys.addaudithook(no_network)
'''


class CoreImportBoundaryTests(unittest.TestCase):
    def child(self, code, *args):
        # Do not inherit account keys, PYTHONPATH, user site or startup hooks.
        env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'TMPDIR')
               if k in os.environ}
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c',
             textwrap.dedent(BOOTSTRAP) + textwrap.dedent(code), str(ROOT), *map(str, args)],
            capture_output=True, text=True, timeout=20, env=env,
        )

    def assert_clean(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_plain_import_is_metadata_only(self):
        self.assert_clean(self.child('''
            class RejectSubmodules:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith('trading_core.'):
                        raise AssertionError('unrequested submodule: ' + fullname)
            sys.meta_path.insert(0, RejectSubmodules())
            import trading_core
            assert trading_core.__version__ == '0.1.0'
            assert not any(n.startswith('trading_core.') for n in sys.modules)
        '''))

    def test_dir_and_unknown_attribute_do_not_initialize_features(self):
        self.assert_clean(self.child('''
            import trading_core
            before = set(sys.modules)
            names = dir(trading_core)
            assert set(trading_core.__all__) <= set(names)
            assert names == sorted(set(names))
            try:
                trading_core.not_a_public_feature
            except AttributeError:
                pass
            else:
                raise AssertionError('unknown attribute accepted')
            assert set(sys.modules) == before
        '''))

    def test_public_names_and_version_are_unchanged(self):
        self.assert_clean(self.child(f'''
            import trading_core
            assert trading_core.__all__ == {PUBLIC!r}
            assert trading_core.__version__ == '0.1.0'
        '''))

    def test_every_public_export_resolves_original_object_and_caches(self):
        self.assert_clean(self.child(f'''
            from types import ModuleType
            import trading_core
            assert not any(n.startswith('trading_core.') for n in sys.modules)
            expected = {{}}
            for module, names in {EXPORTS!r}.items():
                stub = ModuleType('trading_core.' + module)
                for name in names:
                    value = object()
                    setattr(stub, name, value)
                    expected[name] = value
                sys.modules[stub.__name__] = stub
            namespace = {{}}
            exec('from trading_core import *', namespace)
            for name, value in expected.items():
                assert getattr(trading_core, name) is value
                assert namespace[name] is value
                assert trading_core.__dict__[name] is value
            assert {{k for k in namespace if k != '__builtins__'}} == set(expected)
        '''))

    def test_requested_dependency_error_propagates_and_can_retry(self):
        self.assert_clean(self.child('''
            from types import ModuleType
            import trading_core
            class MissingConfigDependency:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'trading_core.config_manager':
                        raise ModuleNotFoundError('missing configured dependency', name='yaml')
            blocker = MissingConfigDependency()
            sys.meta_path.insert(0, blocker)
            try:
                trading_core.ConfigManager
            except ModuleNotFoundError as error:
                assert error.name == 'yaml'
            else:
                raise AssertionError('missing dependency hidden')
            assert 'ConfigManager' not in trading_core.__dict__
            sys.meta_path.remove(blocker)
            stub = ModuleType('trading_core.config_manager')
            stub.ConfigManager = object()
            sys.modules[stub.__name__] = stub
            assert trading_core.ConfigManager is stub.ConfigManager
        '''))

    def test_explicit_optional_warning_is_not_globally_suppressed(self):
        result = self.child('''
            import importlib.util
            import logging
            import trading_core
            assert 'trading_core.telegram_notifier' not in sys.modules
            class NotifierLoader:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'trading_core.telegram_notifier':
                        return importlib.util.spec_from_loader(fullname, self)
                def create_module(self, spec):
                    return None
                def exec_module(self, module):
                    logging.getLogger(module.__name__).warning('explicit-notifier-warning')
                    module.TelegramNotifier = object()
            sys.meta_path.insert(0, NotifierLoader())
            trading_core.TelegramNotifier
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'explicit-notifier-warning\n')

    def test_optional_side_effect_is_not_triggered_by_package_import(self):
        # Isolate the real initializer with small export modules. This makes an
        # unwanted optional warning reproducible regardless of installed extras.
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'trading_core'
            package.mkdir()
            (package / '__init__.py').write_bytes(
                (ROOT / 'packages/core/trading_core/__init__.py').read_bytes())
            for module, names in EXPORTS.items():
                source = ''.join(name + ' = object()\n' for name in names)
                if module == 'telegram_notifier':
                    source += "import logging\nlogging.warning('optional-notifier-unavailable')\n"
                (package / (module + '.py')).write_text(source)
            self.assert_clean(self.child("""
                sys.path.insert(0, sys.argv[2])
                import trading_core
                assert trading_core.__version__ == '0.1.0'
            """, directory))

    def test_actual_order_exports_and_pickle_identity(self):
        self.assert_clean(self.child('''
            import pickle
            from trading_core import OrderManager, OrderSide, PaperExchange, ExchangeInterface
            from trading_core.order_manager import OrderManager as DirectManager
            from trading_core.paper_exchange import PaperExchange as DirectPaper
            assert OrderManager is DirectManager
            assert PaperExchange is DirectPaper
            assert issubclass(PaperExchange, ExchangeInterface)
            assert pickle.loads(pickle.dumps(OrderSide.BUY)) is OrderSide.BUY
            assert 'trading_core.config_manager' not in sys.modules
            assert 'trading_core.telegram_notifier' not in sys.modules
        '''))

    def test_concurrent_public_access_returns_same_class(self):
        self.assert_clean(self.child('''
            from concurrent.futures import ThreadPoolExecutor
            import trading_core
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda _: trading_core.OrderManager, range(20)))
            assert all(v is values[0] for v in values)
        '''))

    def test_actual_observer_import_needs_no_optional_packages(self):
        self.assert_clean(self.child('''
            class RejectOptional:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'yaml', 'telegram', 'ccxt', 'aiohttp', 'dotenv', 'pydantic'}:
                        raise AssertionError('optional dependency requested: ' + fullname)
            sys.meta_path.insert(0, RejectOptional())
            from trading_core.paper_session_observer import PaperSessionObserver
            assert callable(PaperSessionObserver)
            assert 'trading_core.config_manager' not in sys.modules
            assert 'trading_core.telegram_notifier' not in sys.modules
        '''))

    def cli(self, database):
        return self.child('''
            import runpy
            path = sys.argv[2]
            sys.argv = ['paper_session_status.py', '--database', path]
            runpy.run_path(str(ROOT / 'tools/paper_session_status.py'), run_name='__main__')
        ''', database)

    def test_actual_cli_missing_file_has_one_error_json_and_no_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-missing.sqlite'
            result = self.cli(path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '{"error": "paper_session_unavailable"}\n')
            self.assertEqual(json.loads(result.stderr), {'error': 'paper_session_unavailable'})
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertNotIn(directory, result.stderr)

    def test_actual_cli_corrupt_file_has_no_path_or_payload_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-corrupt.sqlite'
            raw = b'private-observation-not-json'
            path.write_bytes(raw)
            path.chmod(0o600)
            result = self.cli(path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(json.loads(result.stderr), {'error': 'paper_session_unavailable'})
            self.assertNotIn(directory, result.stderr)
            self.assertNotIn(raw.decode(), result.stderr)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_actual_session_cli_success_is_quiet_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paper.sqlite'
            self.assert_clean(self.child('''
                import asyncio
                from trading_core.paper_session import PaperTradingSession
                async def create():
                    async with PaperTradingSession(sys.argv[2], create=True,
                                                   initial_balance='1000', enable_control=True):
                        pass
                asyncio.run(create())
            ''', path))
            before = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            result = self.cli(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            report = json.loads(result.stdout)
            self.assertTrue(report['read_only'])
            self.assertFalse(report['live_enabled'])
            self.assertEqual(report['account']['cash'], '1000')
            self.assertEqual(report['control']['mode'], 'halted')
            self.assertEqual(report['orders']['total'], 0)
            self.assertEqual({p.name: p.read_bytes() for p in Path(directory).iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
