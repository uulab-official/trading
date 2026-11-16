"""Canonical bytes and hostile archives, built independently of the encoder."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
from trading_bot import paper_release_format as fmt


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode()


def header(name, size, *, kind=b'0', mode=0o600, uid=0, mtime=0):
    """Independent literal USTAR layout; no production or tarfile serializer."""
    raw = bytearray(512)
    raw[:len(name.encode())] = name.encode()
    for start, length, value in ((100, 8, mode), (108, 8, uid), (116, 8, 0),
                                 (124, 12, size), (136, 12, mtime)):
        raw[start:start + length] = (('%0*o' % (length - 1, value)) + '\0').encode()
    raw[148:156] = b'        '
    raw[156:157] = kind
    raw[257:265] = b'ustar\x0000'
    raw[148:156] = ('%06o\0 ' % sum(raw)).encode()
    return bytes(raw)


def archive(members):
    raw = b''.join(header(name, len(data)) + data + bytes(-len(data) % 512)
                   for name, data in members)
    return raw + bytes((-(len(raw) + 1024) % 10240) + 1024)


def manifest(files):
    return dict(schema_version=1, format='uulab.paper-runtime', profile='paper-runtime-v1',
        runtime=dict(implementation='cpython', minimum_python=[3, 10], platform='linux',
                     third_party_dependencies=[], interpreter_packaged=False),
        entrypoints=list(fmt.ENTRYPOINTS),
        files=[dict(path=p, size=len(files[p]), sha256=digest(files[p]))
               for p in fmt.RELEASE_FILES],
        boundaries=dict(data_included=False, service_activation=False,
                        live_execution=False, authenticated_provenance=False))


class ReleaseFormatTests(unittest.TestCase):
    def setUp(self):
        self.files = {p: ('fixture: ' + p + '\n').encode() for p in fmt.RELEASE_FILES}
        self.manifest = manifest(self.files)
        self.members = [(fmt.MANIFEST_NAME, canonical(self.manifest)), *self.files.items()]
        self.raw = archive(self.members)

    def rejected(self, raw):
        with self.assertRaises(fmt.ReleaseInvalid) as caught:
            fmt.decode_archive(raw, digest(raw))
        self.assertEqual(str(caught.exception), 'paper_release_invalid')

    def test_explicit_profile(self):
        self.assertEqual(len(fmt.RELEASE_FILES), 55)
        self.assertEqual(tuple(sorted(fmt.RELEASE_FILES)), fmt.RELEASE_FILES)
        self.assertEqual(len(set(fmt.RELEASE_FILES)), 55)
        self.assertEqual(fmt.ENTRYPOINTS, ('tools/market_capture.py', 'tools/paper_stream.py',
            'tools/paper_service.py', 'tools/paper_service_worker.py',
            'tools/paper_backup.py', 'tools/paper_backup_drill.py', 'tools/paper_release.py'))
        self.assertEqual(len(fmt.ENGINE_FILES), 24)
        self.assertEqual((fmt.MAX_ARCHIVE_BYTES, fmt.MAX_FILE_BYTES, fmt.MAX_MANIFEST_BYTES),
                         (8 * 1024**2, 1024**2, 64 * 1024))

    def test_encoder_matches_independent_bytes_and_order_independent_input(self):
        self.assertEqual(fmt.encode_archive(self.files), self.raw)
        self.assertEqual(fmt.encode_archive(dict(reversed(list(self.files.items())))), self.raw)

    def test_independent_archive_decodes_without_generic_tar_reader(self):
        with patch('tarfile.open', side_effect=AssertionError('generic archive parser')):
            self.assertEqual(fmt.decode_archive(self.raw, digest(self.raw)), dict(self.members))

    def test_standard_reader_sees_only_regular_private_fixed_members(self):
        with tarfile.open(fileobj=io.BytesIO(fmt.encode_archive(self.files)), mode='r:') as handle:
            self.assertEqual(handle.getnames(), [p for p, _ in self.members])
            for member in handle:
                self.assertTrue(member.isreg())
                self.assertEqual((member.mode, member.uid, member.gid, member.mtime), (0o600, 0, 0, 0))
                self.assertEqual((member.uname, member.gname, member.linkname), ('', '', ''))
                self.assertEqual(handle.extractfile(member).read(), dict(self.members)[member.name])

    def test_expected_external_digest_is_mandatory_and_strict(self):
        for wrong in ('0' * 64, digest(self.raw).upper(), '', 'a' * 63, 'a' * 65,
                      digest(self.raw) + '\n', None, True, 1):
            with self.subTest(expected=wrong), self.assertRaises(fmt.ReleaseInvalid):
                fmt.decode_archive(self.raw, wrong)

    def test_tampered_payload_fails_even_with_updated_external_digest(self):
        members = self.members[:]
        members[1] = (members[1][0], b'x' * len(members[1][1]))
        self.rejected(archive(members))

    def test_missing_extra_duplicate_and_reordered_members(self):
        for members in (self.members[:-1], self.members + [self.members[1]],
                        self.members[:2] + [self.members[1]] + self.members[2:],
                        [self.members[1], self.members[0]] + self.members[2:]):
            with self.subTest(names=[m[0] for m in members[:3]]):
                self.rejected(archive(members))

    def test_traversal_absolute_and_unknown_member_names(self):
        for name in ('../escape', '/escape', 'unknown', './paper-release.json'):
            with self.subTest(name=name):
                self.rejected(archive([(name, self.members[0][1]), *self.members[1:]]))

    def test_extension_links_special_sparse_and_metadata_headers(self):
        for kind in (b'1', b'2', b'3', b'4', b'5', b'6', b'x', b'g', b'L', b'S', b'\0'):
            with self.subTest(kind=kind):
                self.rejected(header(fmt.MANIFEST_NAME, len(self.members[0][1]), kind=kind) + self.raw[512:])
        for options in (dict(mode=0o644), dict(uid=1), dict(mtime=1)):
            with self.subTest(options=options):
                self.rejected(header(fmt.MANIFEST_NAME, len(self.members[0][1]), **options) + self.raw[512:])
        for offset in (148, 157, 257, 263, 265, 297, 329, 345, 500):
            raw = bytearray(self.raw)
            raw[offset] ^= 1
            self.rejected(bytes(raw))

    def test_noncanonical_size_and_huge_declared_sizes(self):
        for size in (fmt.MAX_MANIFEST_BYTES + 1, 8**11 - 1):
            self.rejected(header(fmt.MANIFEST_NAME, size) + self.raw[512:])
        for field in (b' ' * 11 + b'0', b'0000000000x\0', b'\x80' + b'\0' * 11):
            raw = bytearray(self.raw)
            raw[124:136] = field
            self.rejected(bytes(raw))

    def test_padding_truncation_extra_zero_blocks_and_compression(self):
        offset = 512 + len(self.members[0][1])
        raw = bytearray(self.raw)
        raw[offset] = 1
        self.rejected(bytes(raw))
        for raw in (self.raw[:-1], self.raw[:-512], self.raw + bytes(512),
                    self.raw + b'x', gzip.compress(self.raw), self.raw[:511], b''):
            self.rejected(raw)
        raw = bytearray(self.raw)
        raw[-1] = 1
        self.rejected(bytes(raw))

    def test_manifest_schema_types_unknown_fields_paths_and_hashes(self):
        mutations = (
            lambda m: m.update(schema_version=True), lambda m: m.update(schema_version=1.0),
            lambda m: m.update(extra=0), lambda m: m.update(profile='paper-runtime-v2'),
            lambda m: m['runtime'].update(minimum_python=[True, 10]),
            lambda m: m['runtime'].update(interpreter_packaged=0),
            lambda m: m['boundaries'].update(live_execution=0),
            lambda m: m['files'][0].update(size=True),
            lambda m: m['files'][0].update(size=-1),
            lambda m: m['files'][0].update(size=fmt.MAX_FILE_BYTES + 1),
            lambda m: m['files'][0].update(sha256='A' * 64),
            lambda m: m['files'][0].update(path='../secret'),
            lambda m: m['files'][0].update(extra='no'),
            lambda m: m['files'].append(m['files'][0]),
            lambda m: m['entrypoints'].reverse(),
        )
        for mutate in mutations:
            changed = json.loads(canonical(self.manifest))
            mutate(changed)
            self.rejected(archive([(fmt.MANIFEST_NAME, canonical(changed)), *self.members[1:]]))

    def test_manifest_duplicate_keys_numbers_encoding_depth_and_noncanonical_json(self):
        good = canonical(self.manifest)
        for bad in (good[:-1], good + b'\n', b' ' + good,
                    good.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
                    good.replace(b'"schema_version":1', b'"schema_version":NaN'),
                    good.replace(b'"schema_version":1', b'"schema_version":Infinity'),
                    good.replace(b'"schema_version":1', b'"schema_version":' + b'9' * 5000),
                    b'[' * 1500 + b']' * 1500, b'\xff', b'\xef\xbb\xbf' + good,
                    b'{}', b'null', b'[]', b'0'):
            self.rejected(archive([(fmt.MANIFEST_NAME, bad), *self.members[1:]]))

    def test_payload_and_total_size_bounds_and_input_types(self):
        for files in ({}, dict(self.files, unexpected=b''),
                      dict(self.files, **{fmt.RELEASE_FILES[0]: bytearray(b'a')}),
                      dict(self.files, **{fmt.RELEASE_FILES[0]: bytes(fmt.MAX_FILE_BYTES + 1)})):
            with self.assertRaises(fmt.ReleaseInvalid):
                fmt.encode_archive(files)
        self.rejected(bytes(fmt.MAX_ARCHIVE_BYTES + 1))
        with self.assertRaises(fmt.ReleaseInvalid):
            fmt.decode_archive(bytearray(self.raw), digest(self.raw))
        huge = self.files.copy()
        for path in fmt.RELEASE_FILES[:8]:
            huge[path] = bytes(fmt.MAX_FILE_BYTES)
        with self.assertRaises(fmt.ReleaseInvalid):
            fmt.encode_archive(huge)
        self.files[fmt.RELEASE_FILES[0]] = bytes(fmt.MAX_FILE_BYTES)
        raw = fmt.encode_archive(self.files)
        self.assertEqual(fmt.decode_archive(raw, digest(raw))[fmt.RELEASE_FILES[0]],
                         self.files[fmt.RELEASE_FILES[0]])

    def test_fingerprint_matches_native_algorithm_without_payload_imports(self):
        for part in ('packages/core', 'packages/data', 'packages/strategy'):
            sys.path.insert(0, str(ROOT / part))
        from trading_bot.paper_stream import _engine_fingerprint
        files = {path: (ROOT / path).read_bytes() for path in fmt.ENGINE_FILES.values()}
        self.assertEqual(fmt.execution_fingerprint(files), _engine_fingerprint())
        before = fmt.execution_fingerprint(files)
        files[next(iter(fmt.ENGINE_FILES.values()))] += b'\n'
        self.assertNotEqual(fmt.execution_fingerprint(files), before)
        with patch('importlib.import_module', side_effect=AssertionError('payload import')):
            self.assertEqual(fmt.execution_fingerprint(self.files),
                digest(json.dumps(dict(sources={name: digest(self.files[path])
                    for name, path in fmt.ENGINE_FILES.items()}, python=list(sys.version_info[:2]),
                    implementation=sys.implementation.name), sort_keys=True,
                    separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()))
        with self.assertRaises(fmt.ReleaseInvalid):
            fmt.execution_fingerprint({})
