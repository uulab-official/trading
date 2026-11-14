"""Real local files and syscall faults exercise stable report I/O boundaries."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
sys.path.insert(0, str(ROOT / 'tools'))
from trading_bot import local_report_io as local_io


class LocalReportIOTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'report.json'

    def test_exact_bound_and_private_publication_in_public_parent(self):
        self.root.chmod(0o755)
        local_io.publish_new_report(self.path, b'1234')
        self.assertEqual(local_io.read_regular_bytes(self.path, max_bytes=4), b'1234')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        with self.assertRaises(ValueError):
            local_io.read_regular_bytes(self.path, max_bytes=3)

    def test_symlink_directory_and_fifo_rejected(self):
        target = self.root / 'target'
        target.write_bytes(b'x')
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            local_io.read_regular_bytes(self.path, max_bytes=4)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(ValueError):
            local_io.read_regular_bytes(self.path, max_bytes=4)
        self.path.rmdir()
        os.mkfifo(self.path)
        # A subprocess timeout makes a blocking regression fail instead of hanging CI.
        code = 'from trading_bot.local_report_io import read_regular_bytes; read_regular_bytes(%r, max_bytes=4)' % str(self.path)
        result = subprocess.run([sys.executable, '-c', code], timeout=3,
            env={**os.environ, 'PYTHONPATH': str(ROOT / 'apps/server')}, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'ValueError', result.stderr)

    def test_replacement_with_fifo_at_open_is_nonblocking(self):
        self.path.write_bytes(b'old')
        real_open = os.open
        def race(path, flags, *args, **kwargs):
            if path == self.path.name:
                self.path.unlink()
                os.mkfifo(self.path)
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertTrue(flags & os.O_NOFOLLOW)
            return real_open(path, flags, *args, **kwargs)
        with patch.object(local_io.os, 'open', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.read_regular_bytes(self.path, max_bytes=4)

    def test_file_replacement_during_read_rejected(self):
        self.path.write_bytes(b'old')
        real_read = os.read
        def race(fd, size):
            data = real_read(fd, size)
            if data:
                self.path.unlink()
                self.path.write_bytes(b'new')
            return data
        with patch.object(local_io.os, 'read', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.read_regular_bytes(self.path, max_bytes=4)

    def test_same_inode_mutation_during_read_rejected(self):
        self.path.write_bytes(b'old')
        real_read = os.read
        def race(fd, size):
            data = real_read(fd, size)
            if data:
                self.path.write_bytes(b'new')
            return data
        with patch.object(local_io.os, 'read', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.read_regular_bytes(self.path, max_bytes=4)

    def test_parent_replacement_during_read_rejected(self):
        parent = self.root / 'source'
        parent.mkdir()
        path = parent / 'report'
        path.write_bytes(b'old')
        real_read = os.read
        moved = self.root / 'moved'
        def race(fd, size):
            data = real_read(fd, size)
            if data:
                parent.rename(moved)
                parent.mkdir()
            return data
        with patch.object(local_io.os, 'read', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.read_regular_bytes(path, max_bytes=4)

    def test_existing_parent_alias_is_accepted(self):
        alias = self.root / 'alias'
        parent = self.root / 'actual'
        parent.mkdir()
        alias.symlink_to(parent, target_is_directory=True)
        local_io.publish_new_report(alias / 'report', b'content')
        self.assertEqual(local_io.read_regular_bytes(alias / 'report', max_bytes=7), b'content')

    def test_no_clobber_and_file_sync_before_publication(self):
        self.path.write_bytes(b'previous')
        with self.assertRaises(FileExistsError):
            local_io.publish_new_report(self.path, b'new')
        self.assertEqual(self.path.read_bytes(), b'previous')
        self.path.unlink()
        with patch.object(local_io.os, 'fsync', side_effect=OSError('private failure')):
            with self.assertRaises(OSError) as caught:
                local_io.publish_new_report(self.path, b'new')
        self.assertNotIsInstance(caught.exception, local_io.ReportPublicationUncertain)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_directory_sync_failure_is_uncertain_and_preserves_complete_output(self):
        real_sync = os.fsync
        def fault(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('secret filesystem details')
            return real_sync(fd)
        with patch.object(local_io.os, 'fsync', side_effect=fault):
            with self.assertRaises(local_io.ReportPublicationUncertain) as caught:
                local_io.publish_new_report(self.path, b'complete')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(self.path.read_bytes(), b'complete')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_link_then_error_is_uncertain_and_never_removes_output(self):
        real_link = os.link
        def fault(*args, **kwargs):
            real_link(*args, **kwargs)
            raise OSError('secret link outcome')
        with patch.object(local_io.os, 'link', side_effect=fault):
            with self.assertRaises(local_io.ReportPublicationUncertain):
                local_io.publish_new_report(self.path, b'complete')
        self.assertEqual(self.path.read_bytes(), b'complete')

    def test_parent_replacement_before_link_rejected_without_new_output(self):
        parent = self.root / 'output'
        parent.mkdir()
        moved = self.root / 'moved'
        real_sync = os.fsync
        def race(fd):
            real_sync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                parent.rename(moved)
                parent.mkdir()
        with patch.object(local_io.os, 'fsync', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.publish_new_report(parent / 'report', b'complete')
        self.assertFalse((parent / 'report').exists())
        self.assertFalse((moved / 'report').exists())

    def test_parent_replacement_at_link_stays_anchored_and_uncertain(self):
        parent = self.root / 'output'
        parent.mkdir()
        moved = self.root / 'moved'
        real_link = os.link
        def race(*args, **kwargs):
            parent.rename(moved)
            parent.mkdir()
            return real_link(*args, **kwargs)
        with patch.object(local_io.os, 'link', side_effect=race):
            with self.assertRaises(local_io.ReportPublicationUncertain):
                local_io.publish_new_report(parent / 'report', b'complete')
        self.assertFalse((parent / 'report').exists())
        self.assertEqual((moved / 'report').read_bytes(), b'complete')

    def test_replaced_staging_file_is_not_published(self):
        real_sync = os.fsync
        def race(fd):
            real_sync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                temporary, = self.root.iterdir()
                temporary.unlink()
                temporary.write_bytes(b'foreign')
        with patch.object(local_io.os, 'fsync', side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                local_io.publish_new_report(self.path, b'complete')
        self.assertFalse(self.path.exists())
        self.assertEqual([p.read_bytes() for p in self.root.iterdir()], [b'foreign'])

    def test_replaced_output_at_final_confirmation_is_uncertain(self):
        real_sync = os.fsync
        def race(fd):
            real_sync(fd)
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                self.path.unlink()
                self.path.write_bytes(b'foreign')
        with patch.object(local_io.os, 'fsync', side_effect=race):
            with self.assertRaises(local_io.ReportPublicationUncertain):
                local_io.publish_new_report(self.path, b'complete')
        self.assertEqual(self.path.read_bytes(), b'foreign')

    def test_report_modules_load_by_path_without_execution_dependencies(self):
        for name in ('research_report', 'replay_report'):
            code = '''import importlib.util
s = importlib.util.spec_from_file_location('isolated_report', %r)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
assert callable(m.read_report)
''' % str(ROOT / 'apps/server/trading_bot' / (name + '.py'))
            result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_quant_fixture_rejects_symlink_and_oversize_before_research(self):
        import quant_research
        fixture = self.root / 'fixture'
        fixture.write_bytes(b'{}')
        alias = self.root / 'alias'
        alias.symlink_to(fixture)
        with patch.object(quant_research, 'run_research') as run:
            with self.assertRaises(ValueError):
                quant_research.export_research(alias, self.path)
            with fixture.open('wb') as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
            with self.assertRaises(ValueError):
                quant_research.export_research(fixture, self.path)
        run.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_candidate_exact_bound_and_rejected_special_files_before_research(self):
        import quant_research
        candidates = self.root / 'candidates'
        candidates.write_bytes(b'[]' + b' ' * (16384 - 2))
        args = [str(self.root / 'fixture'), '--output', str(self.path),
                '--candidates', str(candidates)]
        result = {'run_id': 'a' * 64, 'summary': {'total_folds': 1}}
        with patch.object(quant_research, 'export_research', return_value=result) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quant_research.main(args), 0)
            self.assertEqual(run.call_args.kwargs['candidates'], [])
        candidates.write_bytes(b'[]' + b' ' * 16383)
        with patch.object(quant_research, 'export_research') as run:
            with self.assertRaises(ValueError):
                quant_research.main(args)
            candidates.unlink()
            target = self.root / 'actual-candidates'
            target.write_bytes(b'[]')
            candidates.symlink_to(target)
            with self.assertRaises(ValueError):
                quant_research.main(args)
            candidates.unlink()
            os.mkfifo(candidates)
            with self.assertRaises(ValueError):
                quant_research.main(args)
        run.assert_not_called()

    def test_read_growth_never_consumes_more_than_bound_plus_one(self):
        self.path.write_bytes(b'a')
        real_read = os.read
        requests = []
        def grow(fd, size):
            if not requests:
                with self.path.open('ab') as stream:
                    stream.write(b'b' * 10000)
            requests.append(size)
            return real_read(fd, size)
        with patch.object(local_io.os, 'read', side_effect=grow):
            with self.assertRaises(ValueError):
                local_io.read_regular_bytes(self.path, max_bytes=4)
        self.assertEqual(sum(requests), 5)

    def test_parent_same_inode_relocation_is_not_silently_accepted(self):
        parent = self.root / 'original'
        parent.mkdir()
        path = parent / 'report'
        path.write_bytes(b'old')
        moved = self.root / 'moved'
        real_read = os.read
        def race(fd, size):
            raw = real_read(fd, size)
            if raw:
                parent.rename(moved)
                parent.symlink_to(moved, target_is_directory=True)
            return raw
        with patch.object(local_io.os, 'read', side_effect=race):
            with self.assertRaises(OSError):
                local_io.read_regular_bytes(path, max_bytes=4)

    def test_research_directory_failure_preserves_report_and_runs_engine_once(self):
        import quant_research
        from trading_bot.walk_forward_research import run_research
        from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
        fixture = self.root / 'fixture'
        fixture.write_text(json.dumps(dataset()))
        original = fixture.read_bytes()
        study = run_research(dataset(), 'a' * 64, candidates=CANDIDATES,
                             settings=SETTINGS, evaluator=lambda *args: metrics())
        real_sync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('secret directory detail')
            return real_sync(fd)
        with patch.object(quant_research, 'run_research', return_value=study) as run:
            with patch.object(local_io.os, 'fsync', side_effect=fail_directory):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = quant_research.cli([str(fixture), '--output', str(self.path)])
        self.assertEqual(code, 3)
        run.assert_called_once()
        self.assertEqual(quant_research.read_report(self.path), study)
        self.assertEqual(fixture.read_bytes(), original)
        self.assertIn('Do not rerun', err.getvalue())
        self.assertNotIn('secret', err.getvalue())

    def test_cli_uncertainty_retains_original_status_and_does_not_retry(self):
        import quant_research
        import paper_replay_dashboard
        for module, expected in ((quant_research, 3), (paper_replay_dashboard, 4)):
            err = io.StringIO()
            with patch.object(module, 'main', side_effect=local_io.ReportPublicationUncertain()) as main:
                with contextlib.redirect_stderr(err):
                    self.assertEqual(module.cli([]), expected)
            main.assert_called_once_with([])
            self.assertIn('may have been published', err.getvalue())
            self.assertIn('Do not rerun', err.getvalue())
            self.assertNotIn('secret', err.getvalue())

    def _research_report(self):
        from trading_bot.walk_forward_research import run_research
        from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
        return run_research(dataset(), 'a' * 64, candidates=CANDIDATES,
                            settings=SETTINGS, evaluator=lambda *args: metrics())

    def test_research_collision_rejects_replaced_parent_and_preserves_outputs(self):
        from trading_bot import research_report
        report = self._research_report()
        raw = research_report.canonical(report).encode()
        parent = self.root / 'output'
        parent.mkdir()
        path = parent / 'research.json'
        moved = self.root / 'moved'
        real_link = os.link

        def collide(source, destination, **kwargs):
            parent.rename(moved)
            parent.mkdir()
            (moved / path.name).write_bytes(raw)
            path.write_bytes(raw)
            # The actual anchored syscall must encounter the original parent.
            return real_link(source, destination, **kwargs)

        with patch.object(local_io.os, 'link', side_effect=collide) as linked:
            with self.assertRaises(OSError) as caught:
                research_report.write_report(path, report)
        linked.assert_called_once()
        self.assertNotIsInstance(caught.exception, local_io.ReportPublicationUncertain)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual((moved / path.name).read_bytes(), raw)
        self.assertEqual(sorted(p.name for p in moved.iterdir()), [path.name])

    def test_research_collision_rejects_parent_replacement_during_cleanup(self):
        from trading_bot import research_report
        report = self._research_report()
        raw = research_report.canonical(report).encode()
        parent = self.root / 'output'
        parent.mkdir()
        path = parent / 'research.json'
        moved = self.root / 'moved'
        real_link, real_unlink = os.link, os.unlink

        def collide(source, destination, **kwargs):
            path.write_bytes(raw)
            return real_link(source, destination, **kwargs)

        def move_after_cleanup(name, *args, **kwargs):
            result = real_unlink(name, *args, **kwargs)
            if str(name).startswith('.report-') and kwargs.get('dir_fd') is not None:
                parent.rename(moved)
                parent.mkdir()
                path.write_bytes(raw)
            return result

        with patch.object(local_io.os, 'link', side_effect=collide):
            with patch.object(local_io.os, 'unlink', side_effect=move_after_cleanup):
                with self.assertRaises(OSError) as caught:
                    research_report.write_report(path, report)
        self.assertNotIsInstance(caught.exception, local_io.ReportPublicationUncertain)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual((moved / path.name).read_bytes(), raw)
        self.assertEqual(sorted(p.name for p in moved.iterdir()), [path.name])

    def test_research_equal_collision_retains_original_generation_and_bytes(self):
        from trading_bot import research_report
        report = self._research_report()
        previous = {**report, 'generated_at': '2020-01-01T00:00:00+00:00'}
        raw = research_report.canonical(previous).encode()
        real_link = os.link

        def collide(source, destination, **kwargs):
            self.path.write_bytes(raw)
            return real_link(source, destination, **kwargs)

        with patch.object(local_io.os, 'link', side_effect=collide) as linked:
            returned = research_report.write_report(self.path, report)
        linked.assert_called_once()
        self.assertEqual(returned, previous)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_research_conflicting_collision_preserves_other_valid_report(self):
        from trading_bot import research_report
        report = self._research_report()
        other = json.loads(json.dumps(report))
        other['experiment']['fixture_sha256'] = 'b' * 64
        other['run_id'] = research_report.research_identity(other['experiment'], False)
        raw = research_report.canonical(research_report.validate_report(other)).encode()
        real_link = os.link

        def collide(source, destination, **kwargs):
            self.path.write_bytes(raw)
            return real_link(source, destination, **kwargs)

        with patch.object(local_io.os, 'link', side_effect=collide) as linked:
            with self.assertRaisesRegex(ValueError, 'new report path'):
                research_report.write_report(self.path, report)
        linked.assert_called_once()
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.root.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
