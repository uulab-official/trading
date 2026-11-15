"""Host preflight is bounded, inert and never a production readiness claim."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'paper_host_preflight.py'
spec = importlib.util.spec_from_file_location('paper_host_preflight', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HostPreflightTests(unittest.TestCase):
    def test_supported_host_observation(self):
        if sys.platform != 'linux':
            self.skipTest('Linux capability required')
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'private'
            root.mkdir(mode=0o700)
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(TOOL),
                                     '--directory', str(root)], capture_output=True,
                                    text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(set(report['checks'].values()), {'pass'})
            self.assertFalse(report['boundaries']['production_ready'])
            self.assertFalse(report['boundaries']['service_activated'])
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_directory_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            self.assertEqual(module.main(['--directory', parent + '/missing']), 2)

    def test_nonprivate_directory_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'public'
            root.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                module._private_root(str(root))

    def test_symlink_ancestor_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'private'
            root.mkdir(mode=0o700)
            (Path(parent) / 'alias').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                module._private_root(str(Path(parent) / 'alias'))

    def test_relative_and_parent_navigation_refused(self):
        for path in ('.', 'tmp/data', '/tmp/../tmp'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                module._private_root(path)

    def test_capability_failure_not_readiness(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'private'
            root.mkdir(mode=0o700)
            with mock.patch.object(module, '_check_socket', return_value=False):
                result = module.inspect(root)
            self.assertEqual(result['unix_socket'], 'fail')
            self.assertNotEqual(set(result.values()), {'pass'})

    def test_no_external_network_primitive(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / 'private'
            root.mkdir(mode=0o700)
            original = module.socket.socket
            def local_only(family, *args, **kwargs):
                if family != module.socket.AF_UNIX:
                    raise AssertionError('external networking forbidden')
                return original(family, *args, **kwargs)
            with mock.patch.object(module.socket, 'socket', side_effect=local_only):
                self.assertIn(module.inspect(root)['unix_socket'], ('pass', 'fail'))


if __name__ == '__main__':
    unittest.main()
