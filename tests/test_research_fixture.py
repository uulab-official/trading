"""Raw-byte source matching, bounded observation and report-bound context."""
import copy
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))


class ResearchFixtureTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('trading_bot.research_fixture')
        except ModuleNotFoundError:
            self.fail('bounded explicit research fixture checker is missing')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / 'source'
        self.parent.mkdir()
        self.path = self.parent / 'fixture.bin'
        self.raw = b'{"price":"100"}\r\n\x00\xff'
        self.path.write_bytes(self.raw)
        self.expected = hashlib.sha256(self.raw).hexdigest()

    def check(self, path=None, expected=None):
        return self.module.check_fixture(self.path if path is None else path,
                                         self.expected if expected is None else expected)

    def assert_unavailable(self, call):
        with self.assertRaises((ValueError, OSError)) as caught:
            call()
        self.assertEqual(str(caught.exception), 'research_fixture_unavailable')
        self.assertNotIn(str(self.root), str(caught.exception))

    def after_first_read(self, action):
        read = os.read
        first = True

        def intercepted(fd, count):
            nonlocal first
            raw = read(fd, count)
            if first:
                first = False
                action()
            return raw

        return patch.object(self.module.os, 'read', side_effect=intercepted)

    def test_exact_raw_bytes_match_without_parsing_or_input_mutation(self):
        before = self.path.stat()
        self.assertEqual(self.check(), {
            'status': 'matched', 'scope': 'local_fixture_bytes', 'algorithm': 'sha256',
            'expected_sha256': self.expected, 'actual_sha256': self.expected,
            'size_bytes': len(self.raw),
        })
        self.assertEqual(self.path.read_bytes(), self.raw)
        after = self.path.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns))

    def test_formatting_change_is_a_mismatch_even_for_equivalent_json(self):
        self.path.write_bytes(b'{ "price": "100" }\n')
        expected = hashlib.sha256(b'{"price":"100"}').hexdigest()
        result = self.check(expected=expected)
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['expected_sha256'], expected)
        self.assertEqual(result['actual_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertNotEqual(result['actual_sha256'], expected)

    def test_empty_file_is_only_a_byte_observation(self):
        self.path.write_bytes(b'')
        digest = hashlib.sha256(b'').hexdigest()
        result = self.check(expected=digest)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['size_bytes'], 0)

    def test_exact_32_mib_limit_is_accepted(self):
        self.assertEqual(self.module.MAX_FIXTURE_BYTES, 32 * 1024 * 1024)
        with self.path.open('wb') as stream:
            stream.truncate(self.module.MAX_FIXTURE_BYTES)
        digest = hashlib.sha256()
        for _ in range(32):
            digest.update(b'\0' * 1024 * 1024)
        result = self.check(expected=digest.hexdigest())
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['size_bytes'], self.module.MAX_FIXTURE_BYTES)

    def test_known_oversize_is_rejected_before_reading(self):
        with self.path.open('wb') as stream:
            stream.truncate(self.module.MAX_FIXTURE_BYTES + 1)
        with patch.object(self.module.os, 'read', side_effect=AssertionError('must not read')):
            self.assert_unavailable(self.check)

    def test_growth_beyond_limit_reads_no_more_than_one_sentinel_byte(self):
        limit = self.module.MAX_FIXTURE_BYTES
        with self.path.open('wb') as stream:
            stream.truncate(limit - 1)
        read = os.read
        total = 0
        calls = 0

        def growing_read(fd, count):
            nonlocal total, calls
            if calls == 0:
                with self.path.open('r+b') as stream:
                    stream.truncate(limit + 8192)
            calls += 1
            raw = read(fd, count)
            total += len(raw)
            self.assertLessEqual(total, limit + 1)
            return raw

        with patch.object(self.module.os, 'read', side_effect=growing_read):
            self.assert_unavailable(self.check)
        self.assertEqual(total, limit + 1)
        self.assertGreater(calls, 1, 'the fixture must be streamed, not loaded in one allocation')

    def test_missing_directory_symlink_broken_symlink_and_fifo_are_rejected(self):
        alias = self.root / 'link'
        alias.symlink_to(self.path)
        broken = self.root / 'broken'
        broken.symlink_to(self.root / 'absent')
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for target in (self.root / 'missing', self.parent, alias, broken, fifo):
            with self.subTest(target=target.name):
                self.assert_unavailable(lambda: self.check(path=target))

    def test_invalid_path_and_expected_digest_are_bounded_without_open(self):
        for target in ('', None, 3, b'fixture', '\0PRIVATE_FILENAME'):
            with self.subTest(target=target):
                self.assert_unavailable(lambda: self.module.check_fixture(target, self.expected))
        for expected in ('', 'A' * 64, 'g' * 64, 'a' * 63, None, True, [], 'a'*64+'\n'):
            with self.subTest(expected=expected):
                with patch.object(self.module.os, 'open', side_effect=AssertionError('must not open')):
                    self.assert_unavailable(lambda: self.module.check_fixture(self.path, expected))

    def test_file_replaced_between_stat_and_open_is_rejected_before_read(self):
        replacement = self.parent / 'replacement'
        replacement.write_bytes(self.raw)
        original = os.open

        def race(path, flags, *args, **kwargs):
            if path == self.path.name and kwargs.get('dir_fd') is not None:
                replacement.replace(self.path)
            return original(path, flags, *args, **kwargs)

        with patch.object(self.module.os, 'open', side_effect=race), patch.object(
                self.module.os, 'read', side_effect=AssertionError('must not read replacement')):
            self.assert_unavailable(self.check)

    def test_fifo_or_symlink_replacement_at_open_never_enters_a_blocking_read(self):
        original = os.open
        for kind in ('fifo', 'symlink'):
            self.path.unlink()
            self.path.write_bytes(self.raw)

            def race(path, flags, *args, **kwargs):
                if path == self.path.name and kwargs.get('dir_fd') is not None:
                    self.assertTrue(flags & os.O_NONBLOCK)
                    self.assertTrue(flags & os.O_NOFOLLOW)
                    self.path.unlink()
                    if kind == 'fifo':
                        os.mkfifo(self.path)
                    else:
                        self.path.symlink_to(self.root / 'missing')
                return original(path, flags, *args, **kwargs)

            with self.subTest(kind=kind), patch.object(self.module.os, 'open', side_effect=race), patch.object(
                    self.module.os, 'read', side_effect=AssertionError('must not read special file')):
                self.assert_unavailable(self.check)

    def test_in_place_overwrite_with_restored_mtime_is_detected(self):
        before = self.path.stat()

        def modify():
            self.path.write_bytes(b'X' * len(self.raw))
            os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))

        with self.after_first_read(modify):
            self.assert_unavailable(self.check)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_append_truncate_and_permission_changes_are_detected(self):
        actions = (
            lambda: self.path.write_bytes(self.raw + b'after'),
            lambda: self.path.write_bytes(b''),
            lambda: self.path.chmod(0o400),
        )
        for action in actions:
            self.path.chmod(0o600)
            self.path.write_bytes(self.raw)
            with self.subTest(action=action), self.after_first_read(action):
                self.assert_unavailable(self.check)

    def test_named_file_replacement_deletion_and_symlink_after_read_are_rejected(self):
        for kind in ('replacement', 'deletion', 'symlink'):
            if os.path.lexists(self.path):
                self.path.unlink()
            self.path.write_bytes(self.raw)
            replacement = self.root / kind
            replacement.write_bytes(self.raw)

            def change():
                self.path.unlink()
                if kind == 'replacement':
                    replacement.replace(self.path)
                elif kind == 'symlink':
                    self.path.symlink_to(replacement)

            with self.subTest(kind=kind), self.after_first_read(change):
                self.assert_unavailable(self.check)

    def test_initial_parent_alias_is_allowed_but_redirect_during_read_is_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.parent, target_is_directory=True)
        self.assertEqual(self.check(path=alias / self.path.name)['status'], 'matched')
        other = self.root / 'other'
        other.mkdir()
        (other / self.path.name).write_bytes(self.raw)

        def redirect():
            alias.unlink()
            alias.symlink_to(other, target_is_directory=True)

        with self.after_first_read(redirect):
            self.assert_unavailable(lambda: self.check(path=alias / self.path.name))

    def test_parent_replacement_during_read_is_rejected(self):
        moved = self.root / 'moved'

        def replace_parent():
            self.parent.rename(moved)
            self.parent.mkdir()
            self.path.write_bytes(self.raw)

        with self.after_first_read(replace_parent):
            self.assert_unavailable(self.check)
        self.assertEqual((moved / self.path.name).read_bytes(), self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_parent_relocation_with_same_inode_symlink_back_is_rejected(self):
        moved = self.root / 'moved'

        def relocate():
            self.parent.rename(moved)
            self.parent.symlink_to(moved, target_is_directory=True)

        with self.after_first_read(relocate):
            self.assert_unavailable(self.check)

    def test_all_descriptors_close_after_success_read_failure_and_interrupt(self):
        original_open = os.open
        for failure in (None, OSError('PRIVATE_READ_DETAIL'), KeyboardInterrupt()):
            opened = []

            def track(*args, **kwargs):
                fd = original_open(*args, **kwargs)
                opened.append(fd)
                return fd

            with self.subTest(failure=type(failure).__name__), patch.object(self.module.os, 'open', side_effect=track):
                if failure is None:
                    self.assertEqual(self.check()['status'], 'matched')
                else:
                    with patch.object(self.module.os, 'read', side_effect=failure):
                        if isinstance(failure, KeyboardInterrupt):
                            with self.assertRaises(KeyboardInterrupt):
                                self.check()
                        else:
                            self.assert_unavailable(self.check)
            self.assertGreaterEqual(len(opened), 1)
            for fd in opened:
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_permission_and_path_resolution_errors_do_not_disclose_details(self):
        with patch.object(self.module.os, 'open', side_effect=PermissionError('PRIVATE_SOURCE_PATH')):
            self.assert_unavailable(self.check)
        loop = self.root / 'loop'
        loop.symlink_to(loop, target_is_directory=True)
        self.assert_unavailable(lambda: self.check(path=loop / 'source'))

    def test_validation_is_pure_returns_a_copy_and_binds_to_expected_digest(self):
        value = self.check()
        before = copy.deepcopy(value)
        with patch.object(self.module.os, 'open', side_effect=AssertionError('pure validation opened a file')):
            checked = self.module.validate_fixture_check(value, self.expected)
        self.assertEqual(checked, before)
        self.assertIsNot(checked, value)
        self.assertEqual(value, before)
        checked['status'] = 'changed'
        self.assertEqual(value, before)
        with self.assertRaises(ValueError):
            self.module.validate_fixture_check(value, 'b'*64)

    def test_validation_accepts_consistent_mismatch_and_size_boundaries(self):
        value = self.check()
        value.update(actual_sha256='b'*64, status='mismatch')
        for size in (0, self.module.MAX_FIXTURE_BYTES):
            with self.subTest(size=size):
                value['size_bytes'] = size
                self.assertEqual(self.module.validate_fixture_check(value, self.expected), value)

    def test_validation_rejects_inconsistent_unknown_or_malformed_context(self):
        base = self.check()
        mutations = [
            lambda value: value.update(extra='PRIVATE_VALUE'),
            lambda value: value.pop('algorithm'),
            lambda value: value.update(status='mismatch'),
            lambda value: value.update(status='not_requested'),
            lambda value: value.update(actual_sha256='b'*64),
            lambda value: value.update(expected_sha256='b'*64),
            lambda value: value.update(scope='source_authenticated'),
            lambda value: value.update(algorithm='SHA-256'),
        ]
        for key in ('actual_sha256', 'expected_sha256'):
            for invalid in (None, True, 'A'*64, 'a'*63, 'g'*64, 'a'*64+'\n', []):
                mutations.append(lambda value, key=key, invalid=invalid: value.update({key: invalid}))
        for invalid in (True, False, -1, 1.0, '1', self.module.MAX_FIXTURE_BYTES+1, None):
            mutations.append(lambda value, invalid=invalid: value.update(size_bytes=invalid))
        for mutate in mutations:
            value = copy.deepcopy(base)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                self.module.validate_fixture_check(value, self.expected)
            self.assertEqual(str(caught.exception), 'research_fixture_check_invalid')
        for value in (None, [], True, 'PRIVATE_VALUE'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.validate_fixture_check(value, self.expected)


if __name__ == '__main__':
    unittest.main()
