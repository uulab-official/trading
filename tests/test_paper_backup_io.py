"""Real private files and syscall faults exercise backup IO ownership."""
import hashlib
import importlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
try:
    io = importlib.import_module('trading_bot.paper_backup_io')
except ModuleNotFoundError:
    io = None


class BackupIOTests(unittest.TestCase):
    def setUp(self):
        if io is None:
            self.fail('The requested backup IO implementation is missing')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'private'
        self.directory.mkdir(mode=0o700)

    def file(self, name='source.bin', data=b'abc'):
        path = self.directory / name
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def test_private_creation_and_existing_directory_are_not_clobbered(self):
        target = self.root / 'new'
        with io.PrivateDirectory(target, create=True) as directory:
            self.assertEqual(directory.path, target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            io.write_file(directory, 'data.bin', b'abc', 3)
            directory.sync()
        with self.assertRaises(io.BackupUnavailable):
            with io.PrivateDirectory(target, create=True):
                pass
        self.assertEqual((target / 'data.bin').read_bytes(), b'abc')

    def test_existing_file_is_never_truncated(self):
        path = self.file()
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupUnavailable):
                io.write_file(directory, path.name, b'new', 3)
        self.assertEqual(path.read_bytes(), b'abc')

    def test_restrictive_umask_does_not_create_unreadable_artifacts(self):
        target = self.root / 'new'
        previous = os.umask(0o777)
        try:
            with io.PrivateDirectory(target, create=True) as directory:
                io.write_file(directory, 'payload.bin', b'abc', 3)
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((target / 'payload.bin').stat().st_mode), 0o600)

    def test_parent_and_leaf_symlinks_are_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.directory, target_is_directory=True)
        for path in (alias, alias / 'child'):
            with self.subTest(path=path):
                with self.assertRaises(io.BackupUnavailable):
                    with io.PrivateDirectory(path, create=path.name == 'child'):
                        pass
        self.assertFalse((self.directory / 'child').exists())

    def test_directory_permissions_are_required(self):
        self.directory.chmod(0o755)
        with self.assertRaises(io.BackupUnavailable):
            with io.PrivateDirectory(self.directory):
                pass

    def test_unsafe_names_and_invalid_bounds_cannot_create_files(self):
        with io.PrivateDirectory(self.directory) as directory:
            for name in ('', '.', '..', '../escape', 'a/b', 'a\\b', 'a\x00b', 'a\nb', 'x' * 256):
                with self.subTest(name=name):
                    with self.assertRaises(io.BackupUnavailable):
                        directory.open_file(name, create=True, limit=3)
            for limit in (False, -1, None, '3'):
                with self.subTest(limit=limit):
                    with self.assertRaises(io.BackupUnavailable):
                        directory.open_file('new.bin', create=True, limit=limit)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_file_types_links_permissions_and_bounds_are_refused(self):
        regular = self.file()
        (self.directory / 'alias.bin').symlink_to(regular)
        os.link(regular, self.directory / 'linked.bin')
        (self.directory / 'folder').mkdir(mode=0o700)
        os.mkfifo(self.directory / 'pipe', 0o600)
        with io.PrivateDirectory(self.directory) as directory:
            for name in ('source.bin', 'alias.bin', 'linked.bin', 'folder', 'pipe'):
                with self.subTest(name=name):
                    with self.assertRaises(io.BackupUnavailable):
                        directory.open_file(name, limit=10)
        (self.directory / 'linked.bin').unlink()
        regular.chmod(0o644)
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupUnavailable):
                directory.open_file(regular.name, limit=3)
        regular.chmod(0o600)
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupUnavailable):
                directory.open_file(regular.name, limit=2)
            with self.assertRaises(io.BackupUnavailable):
                directory.open_file(regular.name, limit=4, minimum=4)

    def test_hash_is_exact_and_file_offset_is_unchanged(self):
        self.file()
        with io.PrivateDirectory(self.directory) as directory:
            fd = directory.open_file('source.bin', limit=3)
            os.lseek(fd, 2, os.SEEK_SET)
            result = io.file_info(fd, 3, check=lambda: directory.check_file('source.bin'))
            self.assertEqual(result, {'size': 3, 'sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'})
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 2)

    def test_empty_file_hash_and_exact_copy_bound(self):
        self.file(data=b'')
        with io.PrivateDirectory(self.directory) as source, io.PrivateDirectory(self.root / 'target', create=True) as target:
            fd = source.open_file('source.bin', limit=0)
            info = io.copy_file(fd, target, 'copy.bin', 0)
            self.assertEqual(info, {'size': 0, 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'})
            self.assertEqual((target.path / 'copy.bin').read_bytes(), b'')

    def test_file_mutation_is_detected_until_explicit_refresh(self):
        path = self.file()
        with io.PrivateDirectory(self.directory) as directory:
            directory.open_file(path.name, limit=10)
            path.write_bytes(b'changed')
            with self.assertRaises(io.BackupUnavailable):
                directory.check_file(path.name)
            directory.refresh_file(path.name)
            directory.check_file(path.name)
            self.assertEqual(io.file_info(directory.files[path.name], 10)['size'], 7)

    def test_refresh_never_accepts_replacement_inode(self):
        path = self.file()
        with io.PrivateDirectory(self.directory) as directory:
            directory.open_file(path.name, limit=10)
            path.rename(self.directory / 'moved.bin')
            self.file(data=b'abc')
            with self.assertRaises(io.BackupUnavailable):
                directory.refresh_file(path.name)

    def test_parent_and_leaf_replacement_are_detected(self):
        with io.PrivateDirectory(self.directory) as directory:
            self.directory.rename(self.root / 'moved')
            self.directory.mkdir(mode=0o700)
            with self.assertRaises(io.BackupUnavailable):
                directory.check()
        parent = self.root / 'nested'
        parent.mkdir(mode=0o700)
        leaf = parent / 'leaf'
        leaf.mkdir(mode=0o700)
        with io.PrivateDirectory(leaf) as directory:
            parent.rename(self.root / 'old-parent')
            parent.mkdir(mode=0o700)
            (parent / 'leaf').mkdir(mode=0o700)
            with self.assertRaises(io.BackupUnavailable):
                directory.check()

    def test_expected_parent_refuses_replacement_before_creation(self):
        parent = self.root / 'parent'
        parent.mkdir(mode=0o700)
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        info = os.fstat(descriptor)
        expected = (str(parent.resolve()), (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
        parent.rename(self.root / 'old-parent')
        parent.mkdir(mode=0o700)
        with self.assertRaises(io.BackupUnavailable):
            with io.PrivateDirectory(parent / 'new', create=True, expected_parent=expected):
                pass
        self.assertFalse((parent / 'new').exists())
        self.assertFalse((self.root / 'old-parent' / 'new').exists())
        self.assertEqual(os.listdir(descriptor), [])

    def test_matching_expected_parent_allows_private_creation(self):
        info = self.root.stat()
        expected = (str(self.root.resolve()), (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid))
        with io.PrivateDirectory(self.root / 'new', create=True, expected_parent=expected) as directory:
            io.write_file(directory, 'data.bin', b'abc', 3)
            directory.sync()
        self.assertEqual((self.root / 'new' / 'data.bin').read_bytes(), b'abc')

    def test_exact_directory_listing_rejects_unexpected_and_large_sets(self):
        self.file()
        with io.PrivateDirectory(self.directory) as directory:
            directory.check_names({'source.bin'})
            with self.assertRaises(io.BackupUnavailable):
                directory.check_names(set())
            for index in range(16):
                self.file('extra-%d' % index)
            with self.assertRaises(io.BackupUnavailable):
                directory.check_names({'source.bin'})

    def test_shared_lock_refuses_exclusive_owner_and_allows_shared_owner(self):
        self.file('.owner.lock', b'')
        with io.PrivateDirectory(self.directory) as first, io.PrivateDirectory(self.directory) as second:
            first.lock('.owner.lock')
            second.lock('.owner.lock')
            with io.PrivateDirectory(self.directory) as writer:
                with self.assertRaises(io.BackupUnavailable):
                    writer.lock('.owner.lock', exclusive=True)
        with io.PrivateDirectory(self.directory) as writer:
            writer.lock('.owner.lock', exclusive=True)

    def test_exclusive_lock_refuses_shared_reader(self):
        self.file('.owner.lock', b'')
        with io.PrivateDirectory(self.directory) as writer:
            writer.lock('.owner.lock', exclusive=True)
            with io.PrivateDirectory(self.directory) as reader:
                with self.assertRaises(io.BackupUnavailable):
                    reader.lock('.owner.lock')

    def test_nonempty_lock_is_refused(self):
        self.file('.owner.lock', b'not empty')
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupUnavailable):
                directory.lock('.owner.lock')

    def test_new_lock_sync_failure_is_uncertain_and_preserves_empty_file(self):
        with io.PrivateDirectory(self.directory) as directory:
            with patch.object(io.os, 'fsync', side_effect=OSError('lock sync failure')):
                with self.assertRaises(io.BackupStorageError):
                    directory.lock('.owner.lock', exclusive=True, create=True)
        self.assertTrue((self.directory / '.owner.lock').is_file())
        self.assertEqual((self.directory / '.owner.lock').read_bytes(), b'')

    def test_new_lock_replacement_during_sync_is_uncertain(self):
        real_fsync = os.fsync
        def replace(fd):
            real_fsync(fd)
            path = self.directory / '.owner.lock'
            path.rename(self.directory / 'previous.lock')
            self.file('.owner.lock', b'')
        with io.PrivateDirectory(self.directory) as directory:
            with patch.object(io.os, 'fsync', side_effect=replace):
                with self.assertRaises(io.BackupStorageError):
                    directory.lock('.owner.lock', exclusive=True, create=True)
        self.assertEqual((self.directory / 'previous.lock').read_bytes(), b'')
        self.assertEqual((self.directory / '.owner.lock').read_bytes(), b'')

    def test_closed_handle_is_unusable(self):
        directory = io.PrivateDirectory(self.directory)
        with directory:
            pass
        with self.assertRaises(io.BackupUnavailable):
            directory.check()

    def test_failed_context_entry_releases_held_descriptors(self):
        directory = io.PrivateDirectory(self.directory)
        descriptor = directory.directory_fd
        self.directory.rename(self.root / 'moved')
        with self.assertRaises(io.BackupUnavailable):
            with directory:
                pass
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_failed_entry_of_created_directory_is_uncertain(self):
        target = self.root / 'target'
        directory = io.PrivateDirectory(target, create=True)
        descriptor = directory.directory_fd
        target.rename(self.root / 'moved')
        with self.assertRaises(io.BackupStorageError):
            with directory:
                pass
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertTrue((self.root / 'moved').is_dir())

    def test_forked_handle_cannot_be_used_and_close_keeps_parent_lock(self):
        self.file('.owner.lock', b'')
        with io.PrivateDirectory(self.directory) as directory:
            directory.lock('.owner.lock', exclusive=True)
            child = os.fork()
            if child == 0:
                try:
                    try:
                        directory.check()
                    except io.BackupUnavailable:
                        directory.close()
                        os._exit(0)
                    os._exit(3)
                except BaseException:
                    os._exit(4)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            with io.PrivateDirectory(self.directory) as other:
                with self.assertRaises(io.BackupUnavailable):
                    other.lock('.owner.lock')
            directory.check()

    def test_hash_detects_same_inode_mutation(self):
        path = self.file(data=b'a' * (1024 * 1024 + 5))
        calls = [0]
        def mutate():
            calls[0] += 1
            if calls[0] == 2:
                path.write_bytes(b'b' * (1024 * 1024 + 5))
        with io.PrivateDirectory(self.directory) as directory:
            fd = directory.open_file(path.name, limit=2 * 1024 * 1024)
            with self.assertRaises(io.BackupUnavailable):
                io.file_info(fd, 2 * 1024 * 1024, check=mutate)

    def test_hash_refuses_oversize_without_reading(self):
        self.file(data=b'abc')
        with io.PrivateDirectory(self.directory) as directory:
            fd = directory.open_file('source.bin', limit=3)
            with patch.object(io.os, 'pread', side_effect=AssertionError('oversize read')):
                with self.assertRaises(io.BackupUnavailable):
                    io.file_info(fd, 2)

    def test_copy_is_streamed_and_preserves_source(self):
        payload = b'abcdef' * 400000
        path = self.file(data=payload)
        real_pread = os.pread
        def bounded(fd, size, offset):
            self.assertLessEqual(size, 1024 * 1024)
            return real_pread(fd, size, offset)
        with io.PrivateDirectory(self.directory) as source, io.PrivateDirectory(self.root / 'target', create=True) as target:
            fd = source.open_file(path.name, limit=len(payload))
            with patch.object(io.os, 'pread', side_effect=bounded):
                result = io.copy_file(fd, target, 'copy.bin', len(payload), check=lambda: source.check_file(path.name))
            self.assertEqual(result, {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            self.assertEqual((target.path / 'copy.bin').read_bytes(), payload)
            target.check_file('copy.bin')
        self.assertEqual(path.read_bytes(), payload)

    def test_copy_short_writes_are_completed(self):
        self.file(data=b'abcdefgh')
        real_write = os.write
        def short(fd, value):
            return real_write(fd, value[:2])
        with io.PrivateDirectory(self.directory) as source, io.PrivateDirectory(self.root / 'target', create=True) as target:
            fd = source.open_file('source.bin', limit=8)
            with patch.object(io.os, 'write', side_effect=short):
                io.copy_file(fd, target, 'copy.bin', 8)
            self.assertEqual((target.path / 'copy.bin').read_bytes(), b'abcdefgh')

    def test_zero_write_is_uncertain_and_preserves_created_output(self):
        with io.PrivateDirectory(self.directory) as directory:
            with patch.object(io.os, 'write', return_value=0):
                with self.assertRaises(io.BackupStorageError):
                    io.write_file(directory, 'new.bin', b'abc', 3)
        self.assertTrue((self.directory / 'new.bin').exists())

    def test_payload_fsync_failure_is_uncertain_and_preserves_output(self):
        with io.PrivateDirectory(self.directory) as directory:
            with patch.object(io.os, 'fsync', side_effect=OSError('disk failure')):
                with self.assertRaises(io.BackupStorageError):
                    io.write_file(directory, 'new.bin', b'abc', 3)
        self.assertEqual((self.directory / 'new.bin').read_bytes(), b'abc')

    def test_directory_sync_failure_is_uncertain(self):
        with io.PrivateDirectory(self.directory) as directory:
            with patch.object(io.os, 'fsync', side_effect=OSError('disk failure')):
                with self.assertRaises(io.BackupStorageError):
                    directory.sync()

    def test_directory_creation_failure_after_mkdir_preserves_directory(self):
        target = self.root / 'target'
        with patch.object(io.os, 'fchmod', side_effect=OSError('chmod failure')):
            with self.assertRaises(io.BackupStorageError):
                with io.PrivateDirectory(target, create=True):
                    pass
        self.assertTrue(target.exists())

    def test_copy_source_change_after_creation_is_uncertain(self):
        source_path = self.file(data=b'abc')
        with io.PrivateDirectory(self.directory) as source, io.PrivateDirectory(self.root / 'target', create=True) as target:
            fd = source.open_file(source_path.name, limit=3)
            changed = [False]
            def mutate():
                if (target.path / 'copy.bin').exists() and not changed[0]:
                    changed[0] = True
                    source_path.write_bytes(b'xyz')
                source.check_file(source_path.name)
            with self.assertRaises(io.BackupStorageError):
                io.copy_file(fd, target, 'copy.bin', 3, check=mutate)
            self.assertTrue((target.path / 'copy.bin').exists())

    def test_stop_before_write_leaves_no_artifact(self):
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupInterrupted) as caught:
                io.write_file(directory, 'new.bin', b'abc', 3, stop=lambda: 15)
            self.assertEqual(caught.exception.signum, 15)
        self.assertFalse((self.directory / 'new.bin').exists())

    def test_stop_after_creation_preserves_partial_output(self):
        with io.PrivateDirectory(self.directory) as directory:
            def stop():
                return 2 if (self.directory / 'new.bin').exists() else 0
            with self.assertRaises(io.BackupInterrupted) as caught:
                io.write_file(directory, 'new.bin', b'abc', 3, stop=stop)
            self.assertEqual(caught.exception.signum, 2)
        self.assertTrue((self.directory / 'new.bin').exists())

    def test_invalid_stop_values_are_refused(self):
        for value in (True, False, 1, -1, '15', None, 9):
            with self.subTest(value=value):
                with self.assertRaises(io.BackupUnavailable):
                    io.check_stop(lambda: value)
        io.check_stop(None)
        io.check_stop(lambda: 0)
        for value in (2, 15):
            with self.subTest(value=value):
                with self.assertRaises(io.BackupInterrupted) as caught:
                    io.check_stop(lambda: value)
                self.assertEqual(caught.exception.signum, value)

    def test_invalid_data_is_refused_before_creation(self):
        with io.PrivateDirectory(self.directory) as directory:
            for value, limit in ((b'long', 3), ('abc', 3), (b'abc', False)):
                with self.subTest(value=value, limit=limit):
                    with self.assertRaises(io.BackupUnavailable):
                        io.write_file(directory, 'new.bin', value, limit)
        self.assertFalse((self.directory / 'new.bin').exists())

    def test_prewrite_binding_failure_is_unavailable_without_artifact(self):
        def fail():
            raise OSError('source no longer available')
        with io.PrivateDirectory(self.directory) as directory:
            with self.assertRaises(io.BackupUnavailable):
                io.write_file(directory, 'new.bin', b'abc', 3, check=fail)
        self.assertFalse((self.directory / 'new.bin').exists())


if __name__ == '__main__':
    unittest.main()
