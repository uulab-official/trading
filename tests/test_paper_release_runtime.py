"""Exercise the extracted source without site-packages or checkout imports.

The service tests and their synthetic transports remain outside the release.
Their project paths resolve through a separate harness to the extracted bytes.
Native workers retain their production -I -B argv; the independent import probe
loads the complete packaged Python closure with -I -S -B.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PARTS = ('packages/core', 'packages/data', 'packages/strategy', 'apps/server')
GUARD = r'''
import socket, sys
def deny_network(event, args):
 if event == 'socket.__new__' and args[1] in (socket.AF_INET, socket.AF_INET6):
  raise AssertionError('network forbidden in release verification')
 if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
  raise AssertionError('network forbidden in release verification')
sys.addaudithook(deny_network)
'''
CLI = GUARD + r'''
import runpy
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
BOOTSTRAP = GUARD + r'''
import importlib, json, os
from pathlib import Path
runtime, checkout = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
 sys.path.insert(0, str(runtime / part))
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
assert 'site' not in sys.modules
def provenance():
 observed = {}
 for name, module in tuple(sys.modules.items()):
  if name.split('.')[0] not in ('trading_core', 'trading_data', 'trading_strategy', 'trading_bot'):
   continue
  origin = getattr(module, '__file__', None)
  if origin:
   resolved = Path(origin).resolve()
   assert resolved.is_relative_to(runtime), (name, resolved)
   observed[name] = str(resolved.relative_to(runtime))
  else:
   for location in getattr(module, '__path__', ()):
    assert Path(location).resolve().is_relative_to(runtime), (name, location)
 for location in sys.path:
  assert not Path(location).resolve().is_relative_to(checkout), location
 return observed
'''


class PaperReleaseRuntimeTests(unittest.TestCase):
    @classmethod
    def run_cli(cls, script, *args, code=0, stdout=subprocess.PIPE):
        completed = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', CLI, str(script), *map(str, args)],
            cwd=cls.root, stdin=subprocess.DEVNULL, stdout=stdout,
            stderr=subprocess.PIPE, timeout=45)
        if completed.returncode != code:
            raise AssertionError(repr((completed.returncode, completed.stdout, completed.stderr)))
        if stdout != subprocess.PIPE:
            return completed
        if len(completed.stdout.splitlines()) != 1:
            raise AssertionError(repr((completed.stdout, completed.stderr)))
        return json.loads(completed.stdout)

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='paper-release-runtime-')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.archive = cls.root / 'runtime.tar'
        cls.runtime = cls.root / 'release'
        cls.release_cli = ROOT / 'tools/paper_release.py'
        cls.built = cls.run_cli(cls.release_cli, 'build', '--output', cls.archive)
        cls.digest = hashlib.sha256(cls.archive.read_bytes()).hexdigest()
        if cls.digest != cls.built['archive_sha256']:
            raise AssertionError('build digest disagrees with independent byte hash')
        cls.run_cli(cls.release_cli, 'extract', cls.archive, '--sha256', cls.digest,
                    '--directory', cls.runtime)
        cls.original = cls.root / 'original-drill'
        cls.original_report = cls.run_cli(ROOT / 'tools/paper_backup_drill.py',
                                         '--directory', cls.original)
        cls.harness = cls.root / 'harness'
        (cls.harness / 'tests').mkdir(parents=True)
        # Only these test helpers are copied. No production code comes from the
        # checkout, and no test helper is installed inside the release.
        helpers = [*ROOT.joinpath('tests').glob('test_paper_service*.py'),
                   ROOT / 'tests/paper_service_fixtures.py',
                   ROOT / 'tests/test_continuous_paper_cli.py',
                   ROOT / 'tests/test_market_capture.py']
        for source in helpers:
            shutil.copyfile(source, cls.harness / 'tests' / source.name)
        for component in ('packages', 'apps', 'tools'):
            (cls.harness / component).symlink_to(cls.runtime / component, target_is_directory=True)

    def probe(self, body, *arguments, timeout=45, runtime=None, env=None):
        completed = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', BOOTSTRAP + body,
             str(runtime or self.runtime), str(ROOT), *map(str, arguments)],
            cwd=self.root, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=timeout, env=dict(os.environ if env is None else env,
                                      PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(completed.returncode, 0, repr((completed.stdout, completed.stderr)))
        self.assertEqual(len(completed.stdout.splitlines()), 1, repr(completed.stdout))
        return json.loads(completed.stdout)

    def verify_clean_release(self):
        report = self.run_cli(self.release_cli, 'verify', self.runtime, '--sha256', self.digest)
        self.assertEqual(report['status'], 'verified_tree')
        self.assertFalse(list(self.runtime.rglob('__pycache__')))
        return report

    def test_canonical_artifact_roundtrip_and_all_entrypoints_use_only_extracted_code(self):
        other = self.root / 'second.tar'
        built = self.run_cli(self.release_cli, 'build', '--output', other)
        self.assertEqual(self.archive.read_bytes(), other.read_bytes())
        self.assertEqual(built, self.built)
        checked = self.run_cli(self.release_cli, 'check', self.archive, '--sha256', self.digest)
        self.assertEqual(checked['status'], 'verified_archive')
        with tarfile.open(self.archive, mode='r:') as archive:
            names = archive.getnames()
            self.assertEqual(len(names), 56)
            self.assertEqual(names[0], 'paper-release.json')
            self.assertTrue(all(member.isfile() and member.mode == 0o600
                                for member in archive.getmembers()))
        observed = self.probe(r'''
import runpy
from trading_bot.paper_release_format import RELEASE_FILES, ENTRYPOINTS
for relative in RELEASE_FILES:
 if not relative.endswith('.py') or relative.startswith('tools/'):
  continue
 pieces = relative.split('/')[2:]
 pieces[-1] = pieces[-1][:-3]
 if pieces[-1] == '__init__': pieces.pop()
 importlib.import_module('.'.join(pieces))
for entry in ENTRYPOINTS:
 runpy.run_path(str(runtime / entry), run_name='release_import_probe')
from trading_bot.paper_stream import _engine_fingerprint
print(json.dumps(dict(modules=provenance(), entrypoints=len(ENTRYPOINTS),
 fingerprint=_engine_fingerprint(), site_loaded='site' in sys.modules)))
''')
        self.assertGreaterEqual(len(observed['modules']), 42)
        self.assertEqual(observed['entrypoints'], 7)
        self.assertEqual(observed['fingerprint'], self.built['execution_fingerprint'])
        self.assertFalse(observed['site_loaded'])
        self.verify_clean_release()

    def test_extracted_runtime_audits_original_account_and_backups_without_byte_changes(self):
        before = {str(path.relative_to(self.original)): path.read_bytes()
                  for path in self.original.rglob('*') if path.is_file()}
        audit = self.run_cli(self.runtime / 'tools/paper_stream.py', 'audit',
                             self.original / 'account')
        self.assertEqual((audit['status'], audit['events_verified']), ('verified_prefix', 120))
        for name in ('backup', 'recovery'):
            checked = self.run_cli(self.runtime / 'tools/paper_backup.py', 'check',
                                   self.original / name)
            self.assertEqual(checked['status'], 'verified_backup')
            self.assertEqual(checked['boundaries']['verification'], 'full_archived_replay')
            self.assertEqual(checked['events_verified'], 120)
        after = {str(path.relative_to(self.original)): path.read_bytes()
                 for path in self.original.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.verify_clean_release()

    def test_extracted_native_drill_preserves_exact_economics_and_nonexecuting_recovery(self):
        result = self.run_cli(self.runtime / 'tools/paper_backup_drill.py', '--directory',
                              self.root / 'extracted-drill')
        self.assertEqual(result, self.original_report)
        self.assertEqual((result['consumed_bars'], result['events_verified'], result['fills']),
                         (120, 120, 18))
        for field in ('network_forbidden', 'original_engine_exact_match',
                      'original_input_unchanged', 'archived_snapshots_exact',
                      'archive_verified_without_originals', 'restored_payloads_exact',
                      'executable_layout_refused'):
            self.assertTrue(result[field], field)
        self.assertFalse(result['execution_authorized'])
        self.verify_clean_release()

    def test_missing_member_cannot_fall_back_to_checkout_or_site_packages(self):
        broken = self.root / 'incomplete-release'
        shutil.copytree(self.runtime, broken)
        (broken / 'packages/data/trading_data/upbit_history.py').unlink()
        report = self.run_cli(self.release_cli, 'verify', broken, '--sha256', self.digest, code=2)
        self.assertEqual(report['status'], 'failed')
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in PARTS))
        result = self.probe(r'''
try:
 importlib.import_module('trading_data.upbit_history')
except ModuleNotFoundError as error:
 assert error.name == 'trading_data.upbit_history', error.name
 print(json.dumps(dict(missing=True, isolated=bool(sys.flags.isolated))))
else:
 raise AssertionError('missing release source silently imported elsewhere')
''', runtime=broken, env=environment)
        self.assertTrue(result['missing'])
        self.assertTrue(result['isolated'])

    def test_existing_service_configuration_keeps_exact_runtime_path_binding(self):
        service = self.root / 'original-service'
        configured = self.run_cli(ROOT / 'tools/paper_service.py', 'configure',
                                  self.original / 'account', '--directory', service)
        self.assertEqual(configured['exit_code'], 0)
        before = {str(path.relative_to(service)): path.read_bytes()
                  for path in service.rglob('*') if path.is_file()}
        result = self.probe(r'''
from unittest.mock import patch
from trading_bot import paper_service
from trading_bot.paper_service_state import ServiceUnavailable, ServiceDirectory
directory = Path(sys.argv[3])
with ServiceDirectory(directory) as owner:
 assert owner.manifest['runtime']['repository'] == str(checkout)
 assert owner.manifest['runtime']['repository'] != str(runtime)
# This test isolates the path binding. Real socket capability and actual probes
# are separately required in the extracted service suite on Linux CI.
with patch.object(paper_service, '_check_platform'), \
     patch.object(paper_service, 'prepare_pair', side_effect=AssertionError('preflight entered')), \
     patch.object(paper_service, '_spawn_worker', side_effect=AssertionError('worker launched')):
 try: paper_service.run_service(directory)
 except ServiceUnavailable: pass
 else: raise AssertionError('relocated service configuration was accepted')
assert not list((directory / 'attempts').iterdir())
print(json.dumps(dict(refused_before_preflight=True, modules=provenance())))
''', service)
        self.assertTrue(result['refused_before_preflight'])
        after = {str(path.relative_to(service)): path.read_bytes()
                 for path in service.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_actual_fixed_stream_worker_uses_extracted_path_and_releases_native_owner(self):
        result = self.probe(r'''
import asyncio, select, signal, time
sys.path.insert(0, str(Path(sys.argv[3]) / 'tests'))
from test_continuous_paper_cli import create_current_capture
from trading_bot import paper_service
from trading_bot.paper_stream import run_stream, audit_stream
from trading_bot.paper_stream_store import StreamUnavailable, stream_writer
from trading_bot.paper_service_protocol import FrameReader
area = Path(sys.argv[4]); area.mkdir()
capture, account = area / 'capture', area / 'account'
create_current_capture(capture)
assert asyncio.run(run_stream(account, capture=capture, max_polls=1))['exit_code'] == 0
attempt = 'a' * 32
process = paper_service._spawn_worker('stream', account, attempt, os.getpid())
assert process.args[4] == str(runtime / 'tools/paper_service_worker.py')
reader = FrameReader('stream', attempt)
owned = False
try:
 deadline = time.monotonic() + 10
 while time.monotonic() < deadline and not owned:
  ready, _, _ = select.select([process.stdout], [], [], .05)
  if ready:
   block = os.read(process.stdout.fileno(), 4096)
   assert block, 'worker ended before ownership'
   owned = any(frame['event'] == 'owned' for frame in reader.feed(block))
 assert owned, 'worker never acquired native ownership'
 try:
  with stream_writer(account): pass
 except StreamUnavailable: pass
 else: raise AssertionError('native account lock was not held')
 process.send_signal(signal.SIGTERM)
 output, error = process.communicate(timeout=8)
 reader.feed(output); reader.finish()
 assert process.returncode == 143 and reader.result['exit_code'] == 143, (output, error)
 with stream_writer(account) as owner:
  assert owner.read()['control']['phase'] == 'stopped'
 audit = asyncio.run(audit_stream(account))
 assert audit['status'] == 'verified_prefix' and audit['events_verified'] == 0
 print(json.dumps(dict(native_owned=True, reaped=process.poll() == 143, modules=provenance())))
finally:
 if process.poll() is None: process.kill()
 process.communicate(timeout=5)
''', self.harness, self.root / 'fixed-worker')
        self.assertTrue(result['native_owned'])
        self.assertTrue(result['reaped'])
        self.verify_clean_release()

    def test_extracted_guarded_service_suite_in_separate_test_harness(self):
        result = self.probe(r'''
import unittest
test_directory = Path(sys.argv[3]) / 'tests'
sys.path.insert(0, str(test_directory))
import test_paper_service_cli
# Its exact ExecStart expectation must name the extracted canonical tool, not
# the harness's symlink spelling. All production paths still resolve unchanged.
assert test_paper_service_cli.SCRIPT.resolve() == runtime / 'tools/paper_service.py'
test_paper_service_cli.SCRIPT = test_paper_service_cli.SCRIPT.resolve()
suite = unittest.defaultTestLoader.discover(str(test_directory), pattern='test_paper_service*.py')
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
assert result.wasSuccessful(), 'extracted service regressions failed'
if os.environ.get('PAPER_SERVICE_REQUIRE_SOCKET') == '1':
 assert not result.skipped, result.skipped
print(json.dumps(dict(tests=result.testsRun,
 skips=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
 modules=provenance(), site_loaded='site' in sys.modules)))
''', self.harness, timeout=180)
        self.assertEqual(result['tests'], 143)
        self.assertFalse(result['site_loaded'])
        if result['skips']:
            # Keep nested capability limitations visible in actual CI/local logs.
            print('extracted_service_capability_skips=' + json.dumps(result['skips']), file=sys.stderr)
        self.verify_clean_release()

    @unittest.skipUnless(Path('/dev/full').exists(), 'requires POSIX /dev/full')
    def test_real_successful_build_with_failed_stdout_preserves_verifiable_artifact(self):
        destination = self.root / 'undelivered.tar'
        with open('/dev/full', 'wb', buffering=0) as failed_output:
            completed = self.run_cli(self.release_cli, 'build', '--output', destination,
                                     code=4, stdout=failed_output)
        self.assertIn(b'paper_release_output_unconfirmed', completed.stderr)
        self.assertEqual(destination.read_bytes(), self.archive.read_bytes())
        checked = self.run_cli(self.release_cli, 'check', destination, '--sha256', self.digest)
        self.assertEqual(checked['status'], 'verified_archive')
        self.run_cli(self.release_cli, 'build', '--output', destination, code=2)


if __name__ == '__main__':
    unittest.main()
