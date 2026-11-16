"""Real filesystem release observations and fault boundaries."""
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))


class PaperReleaseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('trading_bot.paper_release'),
                             'stable release filesystem layer is not implemented')
        self.m = importlib.import_module('trading_bot.paper_release')
        self.f = importlib.import_module('trading_bot.paper_release_format')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.files = {p: ('fixture ' + p + '\n').encode() for p in self.f.RELEASE_FILES}
        for name, data in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
        self.root_patch = patch.object(self.m, '_ROOT', self.source)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.archive = self.base / 'release.tar'
        self.tree = self.base / 'runtime'

    def build(self):
        return self.m.build_release(self.archive)

    def extract(self):
        report = self.build()
        self.m.extract_release(self.archive, self.tree, expected_sha256=report['archive_sha256'])
        return report

    def test_build_check_extract_verify_private_exact_tree(self):
        report = self.extract()
        digest = report['archive_sha256']
        self.assertEqual(report['status'], 'built')
        self.assertEqual(report['file_count'], len(self.files))
        self.assertEqual(self.m.check_release(self.archive, expected_sha256=digest)['status'], 'verified_archive')
        self.assertEqual(self.m.verify_release(self.tree, expected_sha256=digest)['status'], 'verified_tree')
        observed = set()
        for path in self.tree.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
            if path.is_file():
                observed.add(path.relative_to(self.tree).as_posix())
        self.assertEqual(observed, set(self.files) | {self.f.MANIFEST_NAME})
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)

    def test_determinism_unlisted_secrets_ignored_and_read_only_checks(self):
        (self.source / '.secret').write_bytes(b'never ship me')
        first = self.build()
        for path in self.source.rglob('*'):
            os.utime(path, (1000000000, 1000000000))
        second_path = self.base / 'second.tar'
        old = os.umask(0o777)
        try:
            second = self.m.build_release(second_path)
        finally:
            os.umask(old)
        self.assertEqual(first['archive_sha256'], second['archive_sha256'])
        self.assertEqual(self.archive.read_bytes(), second_path.read_bytes())
        before = set(self.base.rglob('*'))
        self.m.check_release(self.archive, expected_sha256=first['archive_sha256'])
        self.assertEqual(before, set(self.base.rglob('*')))

    def test_no_clobber_and_wrong_digest_before_creation(self):
        digest = self.build()['archive_sha256']
        data = self.archive.read_bytes()
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.build()
        self.assertEqual(data, self.archive.read_bytes())
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.extract_release(self.archive, self.tree, expected_sha256='0' * 64)
        self.assertFalse(self.tree.exists())
        self.tree.mkdir()
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        self.assertEqual(list(self.tree.iterdir()), [])

    def test_refuse_source_hardlink_symlink_fifo_and_writable_input(self):
        target = self.source / self.f.RELEASE_FILES[0]
        original = target.read_bytes()
        for kind in ('hardlink', 'symlink', 'fifo', 'writable'):
            with self.subTest(kind=kind):
                target.unlink()
                other = self.base / 'other'
                other.write_bytes(original)
                if kind == 'hardlink':
                    os.link(other, target)
                elif kind == 'symlink':
                    target.symlink_to(other)
                elif kind == 'fifo':
                    os.mkfifo(target)
                else:
                    target.write_bytes(original)
                    target.chmod(0o666)
                with self.assertRaises(self.m.ReleaseUnavailable):
                    self.build()
                self.assertFalse(self.archive.exists())
                target.unlink()
                other.unlink()
                target.write_bytes(original)
                target.chmod(0o644)

    def test_no_source_destination_nesting_or_symlink_components(self):
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.build_release(self.source / 'artifact.tar')
        link = self.base / 'alias'
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.build_release(link / 'artifact.tar')
        digest = self.build()['archive_sha256']
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.extract_release(self.archive, self.source / 'runtime', expected_sha256=digest)
        self.assertFalse((self.source / 'runtime').exists())

    def test_verify_rejects_extra_missing_modified_or_nonprivate_tree(self):
        digest = self.extract()['archive_sha256']
        target = self.tree / self.f.RELEASE_FILES[0]
        original = target.read_bytes()
        extra = self.tree / '__pycache__'
        extra.mkdir()
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.verify_release(self.tree, expected_sha256=digest)
        extra.rmdir()
        target.write_bytes(b'x' * len(original))
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.verify_release(self.tree, expected_sha256=digest)
        target.write_bytes(original)
        target.chmod(0o644)
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.verify_release(self.tree, expected_sha256=digest)
        target.unlink()
        with self.assertRaises(self.m.ReleaseUnavailable):
            self.m.verify_release(self.tree, expected_sha256=digest)

    def test_stop_contract_and_no_descriptor_leaks(self):
        before = len(os.listdir('/proc/self/fd'))
        for value in (True, False, 1, 9, '2', None):
            with self.subTest(value=value), self.assertRaises(self.m.ReleaseUnavailable):
                self.m.build_release(self.archive, stop=lambda: value)
        for signum in (2, 15):
            with self.assertRaises(self.m.ReleaseInterrupted) as raised:
                self.m.build_release(self.archive, stop=lambda: signum)
            self.assertEqual(raised.exception.signum, signum)
        self.extract()
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_short_writes_complete_but_zero_write_preserves_possible_output(self):
        write = os.write
        with patch.object(self.m.os, 'write', side_effect=lambda fd, data: write(fd, data[:17])):
            digest = self.build()['archive_sha256']
        self.m.check_release(self.archive, expected_sha256=digest)
        other = self.base / 'partial.tar'
        with patch.object(self.m.os, 'write', return_value=0):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.build_release(other)
        self.assertTrue(other.exists())
        self.assertEqual(stat.S_IMODE(other.stat().st_mode), 0o600)

    def test_fsync_failure_preserves_output_and_closes_descriptors(self):
        before = len(os.listdir('/proc/self/fd'))
        with patch.object(self.m.os, 'fsync', side_effect=OSError('private path diagnostic')):
            with self.assertRaises(self.m.ReleaseStorageError) as raised:
                self.build()
        self.assertEqual(str(raised.exception), 'paper_release_storage_unconfirmed')
        self.assertTrue(self.archive.exists())
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_archive_replacement_during_read_is_refused(self):
        digest = self.build()['archive_sha256']
        read = os.pread
        replaced = False
        def swap(fd, size, offset):
            nonlocal replaced
            data = read(fd, size, offset)
            if not replaced:
                replaced = True
                self.archive.rename(self.base / 'original.tar')
                self.archive.write_bytes((self.base / 'original.tar').read_bytes())
                self.archive.chmod(0o600)
            return data
        with patch.object(self.m.os, 'pread', side_effect=swap):
            with self.assertRaises(self.m.ReleaseUnavailable):
                self.m.check_release(self.archive, expected_sha256=digest)

    def test_original_source_retained_through_final_output_fsync(self):
        target = self.source / self.f.RELEASE_FILES[0]
        fsync = os.fsync
        changed = False
        def swap(fd):
            nonlocal changed
            fsync(fd)
            if not changed:
                changed = True
                original = target.read_bytes()
                target.rename(target.with_name(target.name + '.old'))
                target.write_bytes(original)
        with patch.object(self.m.os, 'fsync', side_effect=swap):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.build()
        self.assertTrue(self.archive.exists())

    def test_destination_parent_pinned_before_archive_validation(self):
        digest = self.build()['archive_sha256']
        parent = self.base / 'destination'
        parent.mkdir()
        decode = self.f.decode_archive
        def swap(*args, **kwargs):
            result = decode(*args, **kwargs)
            parent.rename(self.base / 'old-destination')
            parent.mkdir()
            return result
        with patch.object(self.f, 'decode_archive', side_effect=swap):
            with self.assertRaises(self.m.ReleaseUnavailable):
                self.m.extract_release(self.archive, parent / 'tree', expected_sha256=digest)
        self.assertFalse((parent / 'tree').exists())
        self.assertFalse((self.base / 'old-destination' / 'tree').exists())

    def test_created_directory_cannot_be_replaced_before_open(self):
        digest = self.build()['archive_sha256']
        original_stat = os.stat
        swapped = False
        def swap(path, *args, **kwargs):
            nonlocal swapped
            result = original_stat(path, *args, **kwargs)
            if path == self.tree.name and kwargs.get('dir_fd') is not None and not swapped:
                swapped = True
                self.tree.rename(self.base / 'abandoned')
                self.tree.mkdir(mode=0o700)
            return result
        with patch.object(self.m.os, 'stat', side_effect=swap):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        self.assertEqual(list(self.tree.iterdir()), [])

    def test_extra_tree_entry_during_report_is_detected(self):
        digest = self.extract()['archive_sha256']
        fingerprint = self.f.execution_fingerprint
        def insert(files):
            result = fingerprint(files)
            (self.tree / 'unexpected').write_bytes(b'late mutation')
            return result
        with patch.object(self.f, 'execution_fingerprint', side_effect=insert):
            with self.assertRaises(self.m.ReleaseUnavailable):
                self.m.verify_release(self.tree, expected_sha256=digest)

    def test_archive_held_through_extraction_final_fsync(self):
        digest = self.build()['archive_sha256']
        fsync = os.fsync
        changed = False
        def swap(fd):
            nonlocal changed
            fsync(fd)
            if stat.S_ISDIR(os.fstat(fd).st_mode) and not changed:
                changed = True
                self.archive.rename(self.base / 'old.tar')
                self.archive.write_bytes((self.base / 'old.tar').read_bytes())
                self.archive.chmod(0o600)
        with patch.object(self.m.os, 'fsync', side_effect=swap):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        self.assertTrue(self.tree.exists())

    def test_actual_signal_preserves_partial_output(self):
        import signal
        intent = 0
        def handler(signum, frame):
            nonlocal intent
            intent = signum
        old = signal.signal(signal.SIGTERM, handler)
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        write = os.write
        def interrupt(fd, data):
            count = write(fd, data[:17])
            os.kill(os.getpid(), signal.SIGTERM)
            return count
        with patch.object(self.m.os, 'write', side_effect=interrupt):
            with self.assertRaises(self.m.ReleaseInterrupted) as raised:
                self.m.build_release(self.archive, stop=lambda: intent)
        self.assertEqual(raised.exception.signum, 15)
        self.assertEqual(self.archive.stat().st_size, 17)

    def test_storage_uncertainty_overrides_requested_signal(self):
        intent = 0
        write = os.write
        def interrupt(fd, data):
            nonlocal intent
            count = write(fd, data[:17])
            self.archive.rename(self.base / 'old.tar')
            self.archive.write_bytes(b'replacement')
            self.archive.chmod(0o600)
            intent = 2
            return count
        with patch.object(self.m.os, 'write', side_effect=interrupt):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.build_release(self.archive, stop=lambda: intent)
        self.assertEqual(self.archive.read_bytes(), b'replacement')

    def test_cleanup_failure_is_storage_uncertainty(self):
        digest = self.build()['archive_sha256']
        before = len(os.listdir('/proc/self/fd'))
        close = os.close
        def uncertain(fd):
            close(fd)
            raise OSError('close outcome unconfirmed')
        with patch.object(self.m.os, 'close', side_effect=uncertain):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.check_release(self.archive, expected_sha256=digest)
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_extract_under_restrictive_umask(self):
        digest = self.build()['archive_sha256']
        old = os.umask(0o777)
        try:
            self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        finally:
            os.umask(old)
        self.assertEqual(self.m.verify_release(self.tree, expected_sha256=digest)['status'], 'verified_tree')

    def test_source_same_size_change_during_read_refused_before_output(self):
        target = self.source / self.f.RELEASE_FILES[0]
        original = target.read_bytes()
        read = os.pread
        changed = False
        def mutate(fd, size, offset):
            nonlocal changed
            data = read(fd, size, offset)
            if not changed:
                changed = True
                target.write_bytes(b'x' * len(original))
            return data
        with patch.object(self.m.os, 'pread', side_effect=mutate):
            with self.assertRaises(self.m.ReleaseUnavailable):
                self.build()
        self.assertFalse(self.archive.exists())

    def test_tree_replaced_on_final_parent_fsync_is_not_adopted(self):
        digest = self.build()['archive_sha256']
        fsync = os.fsync
        changed = False
        parent_identity = (self.base.stat().st_dev, self.base.stat().st_ino)
        def swap(fd):
            nonlocal changed
            fsync(fd)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == parent_identity and not changed:
                changed = True
                self.tree.rename(self.base / 'original-tree')
                self.tree.mkdir(mode=0o700)
        with patch.object(self.m.os, 'fsync', side_effect=swap):
            with self.assertRaises(self.m.ReleaseStorageError):
                self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        self.assertEqual(list(self.tree.iterdir()), [])
        self.assertTrue((self.base / 'original-tree' / self.f.MANIFEST_NAME).exists())

    def test_archive_nonregular_inputs_and_oversize_refused_without_output(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'oversize'):
            with self.subTest(kind=kind):
                other = self.base / 'other'
                other.write_bytes(b'x')
                if kind == 'symlink':
                    self.archive.symlink_to(other)
                elif kind == 'hardlink':
                    os.link(other, self.archive)
                elif kind == 'fifo':
                    os.mkfifo(self.archive)
                else:
                    with self.archive.open('wb') as stream:
                        stream.truncate(self.f.MAX_ARCHIVE_BYTES + 1)
                self.archive.chmod(0o600)
                with self.assertRaises(self.m.ReleaseUnavailable):
                    self.m.extract_release(self.archive, self.tree, expected_sha256='0' * 64)
                self.assertFalse(self.tree.exists())
                self.archive.unlink()
                other.unlink()

    def test_double_slash_absolute_path_is_normalized_without_recursion(self):
        report = self.m.build_release('/' + str(self.archive))
        self.assertEqual(report['status'], 'built')
        self.assertTrue(self.archive.exists())

    def test_restrictive_umask_chmod_never_mutates_renamed_replacement(self):
        digest = self.build()['archive_sha256']
        victim = self.base / 'unrelated-directory'
        victim.mkdir(mode=0o755)
        victim.chmod(0o755)
        victim_inode = victim.stat().st_ino
        original = self.base / 'original-directory'
        chmod = os.chmod
        swapped = False
        def swap(path, mode, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                self.tree.rename(original)
                victim.rename(self.tree)
            return chmod(path, mode, *args, **kwargs)
        before_fds = len(os.listdir('/proc/self/fd'))
        old = os.umask(0o777)
        try:
            with patch.object(self.m.os, 'chmod', side_effect=swap):
                with self.assertRaises(self.m.ReleaseStorageError):
                    self.m.extract_release(self.archive, self.tree, expected_sha256=digest)
        finally:
            os.umask(old)
        self.assertTrue(swapped)
        self.assertEqual(self.tree.stat().st_ino, victim_inode)
        self.assertEqual(stat.S_IMODE(self.tree.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o700)
        self.assertEqual(list(self.tree.iterdir()), [])
        self.assertEqual(list(original.iterdir()), [])
        self.assertEqual(before_fds, len(os.listdir('/proc/self/fd')))

    def test_deep_unavailable_parent_is_preoutput_refusal_without_recursion(self):
        before_fds = len(os.listdir('/proc/self/fd'))
        output = '/' + 'a/' * 1100 + 'archive.tar'
        with self.assertRaises(self.m.ReleaseUnavailable) as raised:
            self.m.build_release(output)
        self.assertEqual(str(raised.exception), 'paper_release_unavailable')
        self.assertFalse(self.archive.exists())
        self.assertEqual(before_fds, len(os.listdir('/proc/self/fd')))


if __name__ == '__main__':
    unittest.main()
