"""Real offline backup/recovery drill, private failures and no-clobber output."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/paper_backup_drill.py'
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / part))


def load_drill():
    spec = importlib.util.spec_from_file_location('paper_backup_drill_subject', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archived_account(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        manifest = json.loads(connection.execute('SELECT body FROM stream_manifest').fetchone()[0])
        head = json.loads(connection.execute('SELECT body FROM stream_head').fetchone()[0])
        events = [json.loads(row[0]) for row in connection.execute(
            'SELECT body FROM stream_events ORDER BY sequence')]
        return dict(schema_version=1, manifest=manifest, **head), [
            event['fill'] for event in events if event['fill'] is not None]
    finally:
        connection.close()


class PaperBackupDrillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='SECRET-drill-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, args):
        # The production drill also refuses network; this independent outer
        # boundary prevents accidental provider access during script loading.
        result = subprocess.run([sys.executable, '-c', r'''
import runpy,socket,sys
def forbidden(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
socket.socket.connect_ex=socket.socket.sendto=forbidden
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
''', str(SCRIPT), *args], text=True, capture_output=True, timeout=60)
        return result

    def private_json(self, result):
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout + result.stderr)
        self.assertNotIn('SECRET', result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_real_cli_drill_verifies_archived_recovery_and_refuses_repeating_output(self):
        output = self.root / 'exercise'
        result = self.invoke(['--directory', str(output)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = self.private_json(result)
        self.assertEqual(summary['status'], 'verified_recovery_drill')
        self.assertEqual(summary['consumed_bars'], 120)
        self.assertEqual(summary['bootstrap_bars'], 120)
        self.assertEqual(summary['source_bars'], 240)
        self.assertEqual(summary['fills'], 18)
        for key in ('synthetic', 'network_forbidden', 'original_input_unchanged',
                    'archive_verified_without_originals', 'restored_payloads_exact',
                    'archived_snapshots_exact', 'executable_layout_refused'):
            self.assertIs(summary[key], True, key)
        for key in ('execution_authorized', 'source_rebinding', 'rollback_protection', 'live_enabled'):
            self.assertIs(summary[key], False, key)
        report = json.loads((output / 'drill-report.json').read_text())
        self.assertEqual(report['summary'], summary)
        self.assertEqual(report['original_audit']['status'], 'verified_prefix')
        self.assertEqual(report['original_audit']['events_verified'], 120)
        self.assertEqual(report['backup']['status'], 'verified_backup')
        self.assertEqual(report['archived_check']['status'], 'verified_backup')
        self.assertEqual(report['restore']['status'], 'verified_restore')
        self.assertEqual(report['restored_check']['status'], 'verified_backup')

        from trading_bot.market_capture_store import read_capture
        from trading_bot.paper_stream_store import read_stream
        original = read_stream(output / 'account')
        self.assertEqual(original, report['original_snapshot'])
        self.assertEqual(original['control']['phase'], 'stopped')
        self.assertEqual(read_capture(output / 'capture'), report['capture_snapshot'])
        self.assertEqual(report['capture_snapshot']['state']['phase'], 'stopped')
        self.assertEqual(report['capture_snapshot']['state']['evicted_bars_total'], 40)
        self.assertFalse((output / 'account-unavailable').exists())
        self.assertFalse((output / 'capture-unavailable').exists())
        for name in ('backup', 'recovery'):
            bundle = output / name
            self.assertEqual({p.name for p in bundle.iterdir()}, {
                '.backup.lock', 'backup.json', 'account-database.bin', 'capture-database.bin'})
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o700)
            for path in bundle.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            copied, fills = archived_account(bundle / 'account-database.bin')
            self.assertEqual(copied, original)
            self.assertEqual(fills, report['fills'])
            self.assertEqual(len(fills), 18)
            self.assertEqual(sha256(bundle / 'account-database.bin'),
                             sha256(output / 'account/stream.sqlite3'))
            self.assertEqual(sha256(bundle / 'capture-database.bin'),
                             sha256(output / 'capture/capture.sqlite3'))
        self.assertEqual(stat.S_IMODE((output / 'drill-report.json').stat().st_mode), 0o600)

        before = {str(p.relative_to(output)): sha256(p) for p in output.rglob('*') if p.is_file()}
        repeated = self.invoke(['--directory', str(output)])
        self.assertEqual(repeated.returncode, 2)
        self.assertEqual(self.private_json(repeated), {'error': 'paper_backup_drill_unavailable'})
        self.assertEqual(before, {
            str(p.relative_to(output)): sha256(p) for p in output.rglob('*') if p.is_file()})

    def test_invalid_arguments_return_private_json_without_creating_output(self):
        output = self.root / 'SECRET-new'
        for args in ([], ['--directory'], ['--directory', str(output), '--live'],
                     ['--directory', str(output), '--source', 'SECRET-existing'],
                     ['--directory', str(output), '--directory-extra', 'bad']):
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.private_json(result), {'error': 'paper_backup_drill_arguments_invalid'})
        self.assertFalse(output.exists())

    def test_existing_or_unavailable_destination_is_never_reused(self):
        directory = self.root / 'existing'
        directory.mkdir()
        sentinel = directory / 'keep.txt'
        sentinel.write_text('SECRET-preserve')
        existing_file = self.root / 'file'
        existing_file.write_text('SECRET-file')
        linked = self.root / 'linked'
        linked.symlink_to(directory, target_is_directory=True)
        dangling = self.root / 'dangling'
        dangling.symlink_to(self.root / 'absent', target_is_directory=True)
        for path in (directory, existing_file, linked, dangling, self.root / 'missing/child'):
            with self.subTest(path=path):
                result = self.invoke(['--directory', str(path)])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.private_json(result), {'error': 'paper_backup_drill_unavailable'})
        self.assertEqual(sentinel.read_text(), 'SECRET-preserve')
        self.assertEqual(existing_file.read_text(), 'SECRET-file')
        self.assertEqual(list(directory.iterdir()), [sentinel])
        self.assertFalse((self.root / 'absent').exists())

    def test_symlink_parent_is_refused_before_writing_its_target(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        link = self.root / 'parent-link'
        link.symlink_to(target, target_is_directory=True)
        result = self.invoke(['--directory', str(link / 'new')])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.private_json(result), {'error': 'paper_backup_drill_unavailable'})
        self.assertEqual(list(target.iterdir()), [])

    def test_archive_verification_failure_restores_original_names_and_redacts_details(self):
        self.assertTrue(SCRIPT.exists(), 'The recovery drill has not been implemented')
        from trading_bot import paper_backup
        drill = load_drill()
        output = self.root / 'failed-exercise'
        original_check = paper_backup.check_backup

        async def failed_when_originals_unavailable(bundle, **kwargs):
            if not (output / 'capture').exists() and not (output / 'account').exists():
                raise ValueError('SECRET-unavailable-originals')
            return await original_check(bundle, **kwargs)

        with patch.object(paper_backup, 'check_backup', failed_when_originals_unavailable), \
                redirect_stdout(io.StringIO()) as stdout:
            code = drill.main(['--directory', str(output)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue()), {'error': 'paper_backup_drill_unavailable'})
        self.assertNotIn('SECRET', stdout.getvalue())
        self.assertTrue((output / 'capture/capture.sqlite3').is_file())
        self.assertTrue((output / 'account/stream.sqlite3').is_file())
        self.assertTrue((output / 'backup/backup.json').is_file())
        self.assertFalse((output / 'capture-unavailable').exists())
        self.assertFalse((output / 'account-unavailable').exists())
        self.assertFalse((output / 'drill-report.json').exists())

    def test_storage_failure_takes_priority_over_received_signal(self):
        drill = load_drill()
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        for signum in (signal.SIGINT, signal.SIGTERM):
            output = self.root / ('signal-' + str(int(signum)))

            def fail_sync(descriptor):
                # The real workspace has already been created. Deliver an actual
                # OS signal, then fail the low-level durability confirmation.
                self.assertTrue(output.is_dir())
                os.kill(os.getpid(), signum)
                raise OSError('SECRET-unconfirmed-directory-sync')

            with self.subTest(signal=signum), patch('os.fsync', side_effect=fail_sync), \
                    redirect_stdout(io.StringIO()) as stdout:
                code = drill.main(['--directory', str(output)])
                self.assertEqual(code, 4)
                self.assertEqual(json.loads(stdout.getvalue()), {
                    'error': 'paper_backup_drill_storage_failed_check_output'})
                self.assertNotIn('SECRET', stdout.getvalue())
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                self.assertTrue(output.is_dir(), 'Unconfirmed output must be preserved')
                self.assertFalse((output / 'drill-report.json').exists())
                self.assertEqual({key: signal.getsignal(key) for key in previous}, previous)

    def test_core_interruptions_keep_signal_exit_codes_and_restore_original_names(self):
        from trading_bot import paper_backup
        drill = load_drill()
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        for operation in ('create_backup', 'check_backup', 'verify_restore'):
            core_operation = getattr(paper_backup, operation)
            for signum in (signal.SIGINT, signal.SIGTERM):
                output = self.root / (operation + '-' + str(int(signum)))

                async def interrupt_at_core_boundary(*args, **kwargs):
                    os.kill(os.getpid(), signum)
                    return await core_operation(*args, **kwargs)

                with self.subTest(operation=operation, signal=signum), \
                        patch.object(paper_backup, operation, interrupt_at_core_boundary), \
                        redirect_stdout(io.StringIO()) as stdout:
                    code = drill.main(['--directory', str(output)])
                    self.assertEqual(code, 128 + int(signum))
                    self.assertEqual(json.loads(stdout.getvalue()), {
                        'error': 'paper_backup_drill_interrupted_check_output'})
                    self.assertNotIn('SECRET', stdout.getvalue())
                    self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                    self.assertTrue((output / 'capture/capture.sqlite3').is_file())
                    self.assertTrue((output / 'account/stream.sqlite3').is_file())
                    self.assertFalse((output / 'capture-unavailable').exists())
                    self.assertFalse((output / 'account-unavailable').exists())
                    self.assertFalse((output / 'drill-report.json').exists())
                    self.assertEqual({key: signal.getsignal(key) for key in previous}, previous)


if __name__ == '__main__':
    unittest.main()
