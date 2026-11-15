"""Bounded canonical PAPER source archive; no filesystem or payload execution.

The only supported wire format is the fixed, uncompressed USTAR profile below.
Decoding compares raw headers to trusted serializations; tarfile is used only
as a standard-library header writer, never as an archive reader or extractor.
"""
import hashlib
import json
import re
import sys
import tarfile
from types import MappingProxyType

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_NAME = 'paper-release.json'

ENTRYPOINTS = (
    'tools/market_capture.py', 'tools/paper_stream.py',
    'tools/paper_service.py', 'tools/paper_service_worker.py',
    'tools/paper_backup.py', 'tools/paper_backup_drill.py', 'tools/paper_release.py',
)
RELEASE_FILES = tuple(sorted((
    'apps/server/trading_bot/durable_paper_resume.py',
    'apps/server/trading_bot/durable_paper_run.py',
    'apps/server/trading_bot/local_report_io.py',
    'apps/server/trading_bot/market_capture.py',
    'apps/server/trading_bot/market_capture_store.py',
    'apps/server/trading_bot/paper_backup.py',
    'apps/server/trading_bot/paper_backup_io.py',
    'apps/server/trading_bot/paper_checkpoint.py',
    'apps/server/trading_bot/paper_runner.py',
    'apps/server/trading_bot/paper_service.py',
    'apps/server/trading_bot/paper_service_protocol.py',
    'apps/server/trading_bot/paper_service_state.py',
    'apps/server/trading_bot/paper_service_unit.py',
    'apps/server/trading_bot/paper_service_worker.py',
    'apps/server/trading_bot/paper_stream.py',
    'apps/server/trading_bot/paper_stream_engine.py',
    'apps/server/trading_bot/paper_stream_store.py',
    'apps/server/trading_bot/paper_release.py',
    'apps/server/trading_bot/paper_release_format.py',
    'packages/core/trading_core/__init__.py',
    'packages/core/trading_core/exchange.py',
    'packages/core/trading_core/order_manager.py',
    'packages/core/trading_core/order_reconciliation.py',
    'packages/core/trading_core/order_state_store.py',
    'packages/core/trading_core/paper_admission.py',
    'packages/core/trading_core/paper_control.py',
    'packages/core/trading_core/paper_evidence.py',
    'packages/core/trading_core/paper_evidence_compare.py',
    'packages/core/trading_core/paper_exchange.py',
    'packages/core/trading_core/paper_session.py',
    'packages/core/trading_core/paper_session_observer.py',
    'packages/data/trading_data/__init__.py',
    'packages/data/trading_data/_validation.py',
    'packages/data/trading_data/candle.py',
    'packages/data/trading_data/indicator.py',
    'packages/data/trading_data/public_candle_transport.py',
    'packages/data/trading_data/upbit_history.py',
    'packages/strategy/trading_strategy/__init__.py',
    'packages/strategy/trading_strategy/base.py',
    'packages/strategy/trading_strategy/breakout_strategy.py',
    'packages/strategy/trading_strategy/ma_strategy.py',
    'packages/strategy/trading_strategy/rsi_strategy.py',
    'LICENSE', 'docs/MARKET_CAPTURE.md', 'docs/CONTINUOUS_PAPER.md',
    'docs/PAPER_BACKUP.md', 'docs/PAPER_SERVICE.md', 'docs/PAPER_RELEASE.md',
    *ENTRYPOINTS,
)))
ENGINE_FILES = MappingProxyType({
    'trading_bot.paper_runner': 'apps/server/trading_bot/paper_runner.py',
    'trading_bot.paper_checkpoint': 'apps/server/trading_bot/paper_checkpoint.py',
    'trading_bot.durable_paper_run': 'apps/server/trading_bot/durable_paper_run.py',
    'trading_bot.durable_paper_resume': 'apps/server/trading_bot/durable_paper_resume.py',
    'trading_core.exchange': 'packages/core/trading_core/exchange.py',
    'trading_core.paper_exchange': 'packages/core/trading_core/paper_exchange.py',
    'trading_core.order_manager': 'packages/core/trading_core/order_manager.py',
    'trading_core.order_reconciliation': 'packages/core/trading_core/order_reconciliation.py',
    'trading_core.order_state_store': 'packages/core/trading_core/order_state_store.py',
    'trading_core.paper_session': 'packages/core/trading_core/paper_session.py',
    'trading_core.paper_admission': 'packages/core/trading_core/paper_admission.py',
    'trading_core.paper_control': 'packages/core/trading_core/paper_control.py',
    'trading_data.candle': 'packages/data/trading_data/candle.py',
    'trading_data.indicator': 'packages/data/trading_data/indicator.py',
    'trading_data._validation': 'packages/data/trading_data/_validation.py',
    'trading_data.upbit_history': 'packages/data/trading_data/upbit_history.py',
    'trading_strategy.base': 'packages/strategy/trading_strategy/base.py',
    'trading_strategy.ma_strategy': 'packages/strategy/trading_strategy/ma_strategy.py',
    'trading_strategy.rsi_strategy': 'packages/strategy/trading_strategy/rsi_strategy.py',
    'trading_bot.paper_stream_engine': 'apps/server/trading_bot/paper_stream_engine.py',
    'trading_bot.paper_stream_store': 'apps/server/trading_bot/paper_stream_store.py',
    'trading_bot.paper_stream': 'apps/server/trading_bot/paper_stream.py',
    'trading_bot.market_capture_store': 'apps/server/trading_bot/market_capture_store.py',
    'trading_bot.market_capture': 'apps/server/trading_bot/market_capture.py',
})
_SHA256 = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_SIZE = re.compile(rb'[0-7]{11}\x00\Z')


class ReleaseInvalid(ValueError):
    """An unsupported, malformed or integrity-mismatched release."""

    def __init__(self):
        super().__init__('paper_release_invalid')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')


def _manifest(entries):
    return dict(schema_version=1, format='uulab.paper-runtime', profile='paper-runtime-v1',
        runtime=dict(implementation='cpython', minimum_python=[3, 10], platform='linux',
                     third_party_dependencies=[], interpreter_packaged=False),
        entrypoints=list(ENTRYPOINTS), files=entries,
        boundaries=dict(data_included=False, service_activation=False,
                        live_execution=False, authenticated_provenance=False))


def _header(path, size):
    info = tarfile.TarInfo(path)
    info.type = tarfile.REGTYPE
    info.size = size
    info.mode = 0o600
    info.uid = info.gid = info.mtime = 0
    info.uname = info.gname = info.linkname = ''
    return info.tobuf(format=tarfile.USTAR_FORMAT, encoding='ascii', errors='strict')


def _padded_size(size):
    return ((size + 511) // 512) * 512


def _archive_size(end):
    return ((end + 1024 + 10239) // 10240) * 10240


def _valid_bytes(data, maximum):
    if type(data) is not bytes or len(data) > maximum:
        raise ReleaseInvalid()


def encode_archive(files: dict[str, bytes]) -> bytes:
    """Return deterministic archive bytes for exactly the versioned payload."""
    if type(files) is not dict or set(files) != set(RELEASE_FILES):
        raise ReleaseInvalid()
    for path in RELEASE_FILES:
        _valid_bytes(files[path], MAX_FILE_BYTES)
    entries = [dict(path=path, size=len(files[path]), sha256=_sha(files[path]))
               for path in RELEASE_FILES]
    manifest = _canonical(_manifest(entries))
    _valid_bytes(manifest, MAX_MANIFEST_BYTES)
    members = [(MANIFEST_NAME, manifest), *((p, files[p]) for p in RELEASE_FILES)]
    end = sum(512 + _padded_size(len(data)) for _, data in members)
    size = _archive_size(end)
    if size > MAX_ARCHIVE_BYTES:
        raise ReleaseInvalid()
    chunks = []
    for path, data in members:
        chunks.extend((_header(path, len(data)), data, bytes(-len(data) % 512)))
    chunks.append(bytes(size - end))
    return b''.join(chunks)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseInvalid()
        result[key] = value
    return result


def _integer(text):
    if len(text) > 10:
        raise ReleaseInvalid()
    return int(text)


def _no_number(text):
    raise ReleaseInvalid()


def _read_manifest(data):
    # Bound nesting before json.loads so decoder recursion is never a resource
    # limit. Strings and escaped quotes do not contribute structural nesting.
    depth = 0
    quoted = escaped = False
    for value in data:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            if depth > 8:
                raise ReleaseInvalid()
        elif value in (93, 125):
            depth -= 1
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                           parse_int=_integer, parse_float=_no_number,
                           parse_constant=_no_number)
    except (ValueError, UnicodeError, RecursionError):
        raise ReleaseInvalid() from None
    if type(value) is not dict or type(value.get('files')) is not list:
        raise ReleaseInvalid()
    entries = value['files']
    if len(entries) != len(RELEASE_FILES):
        raise ReleaseInvalid()
    for path, entry in zip(RELEASE_FILES, entries):
        if (type(entry) is not dict or set(entry) != {'path', 'size', 'sha256'}
                or entry['path'] != path or type(entry['size']) is not int
                or not 0 <= entry['size'] <= MAX_FILE_BYTES
                or type(entry['sha256']) is not str
                or _SHA256.fullmatch(entry['sha256']) is None):
            raise ReleaseInvalid()
    # Comparing to the exact expected bytes rejects unknown fields, bool/int
    # equality tricks, duplicate/noncanonical JSON, and changed fixed metadata.
    if data != _canonical(_manifest(entries)):
        raise ReleaseInvalid()
    return entries


def _read_member(raw, offset, path, size):
    end = offset + 512 + size
    padded_end = offset + 512 + _padded_size(size)
    if (padded_end > len(raw) or raw[offset:offset + 512] != _header(path, size)
            or any(raw[end:padded_end])):
        raise ReleaseInvalid()
    return raw[offset + 512:end], padded_end


def decode_archive(raw: bytes, expected_sha256: str) -> dict[str, bytes]:
    """Validate external digest and every byte; return payload and manifest.

    No archive-supplied member name is used to choose a destination or parser.
    Filesystem callers must separately pin and validate the bytes they read.
    """
    _valid_bytes(raw, MAX_ARCHIVE_BYTES)
    if (type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None
            or _sha(raw) != expected_sha256 or len(raw) < 512):
        raise ReleaseInvalid()
    size_field = raw[124:136]
    if _SIZE.fullmatch(size_field) is None:
        raise ReleaseInvalid()
    size = int(size_field[:-1], 8)
    if size > MAX_MANIFEST_BYTES:
        raise ReleaseInvalid()
    manifest, offset = _read_member(raw, 0, MANIFEST_NAME, size)
    entries = _read_manifest(manifest)
    # Refuse an impossible layout before processing any payload contents.
    end = offset + sum(512 + _padded_size(entry['size']) for entry in entries)
    if _archive_size(end) != len(raw):
        raise ReleaseInvalid()
    result = {MANIFEST_NAME: manifest}
    for path, entry in zip(RELEASE_FILES, entries):
        data, offset = _read_member(raw, offset, path, entry['size'])
        if _sha(data) != entry['sha256']:
            raise ReleaseInvalid()
        result[path] = data
    if any(raw[offset:]):
        raise ReleaseInvalid()
    return result


def execution_fingerprint(files: dict[str, bytes]) -> str:
    """Observe native engine compatibility using bytes, never archived imports."""
    if type(files) is not dict:
        raise ReleaseInvalid()
    sources = {}
    for name, path in ENGINE_FILES.items():
        if path not in files:
            raise ReleaseInvalid()
        _valid_bytes(files[path], MAX_FILE_BYTES)
        sources[name] = _sha(files[path])
    # Native paper_stream._digest has no final newline and ensure_ascii=False.
    value = dict(sources=sources, python=list(sys.version_info[:2]),
                 implementation=sys.implementation.name)
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False).encode('utf-8'))
