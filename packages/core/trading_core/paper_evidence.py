"""Portable full-account observations, not executable backups or attestations.

No broker access, writer store, order normalization or restore operation. Files
contain private plaintext paper-account data and must not be publicly published.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat

from trading_core.order_state_store import MAX_BYTES, _canonical, _digest, _pairs
from trading_core.paper_session import _bundle

FORMAT = 'uulab.paper-session.evidence'
MAX_EVIDENCE_BYTES = MAX_BYTES + 4096
_FIELDS = frozenset(('format', 'format_version', 'captured_at', 'mode', 'read_only',
                     'live_enabled', 'snapshot', 'payload', 'content_sha256'))
_SOURCE_FIELDS = frozenset(('account_id', 'session_version', 'revision', 'digest'))


class PaperEvidenceInvalid(ValueError):
    """No valid complete observation; never substitute an empty account."""


class PaperEvidenceWriteError(OSError):
    """Output could not be published; no existing target is overwritten."""


class PaperEvidencePublishedError(PaperEvidenceWriteError):
    """Output may exist but durability is unconfirmed; inspect, never delete it."""


def _make_evidence(data, orders, book, version, revision, digest):
    body = dict(format=FORMAT, format_version=1,
                captured_at=datetime.now(timezone.utc).isoformat(),
                mode='paper', read_only=True, live_enabled=False,
                snapshot=dict(account_id=data['account_id'], session_version=version,
                              revision=str(revision), digest=digest), payload=data)
    return dict(**body, content_sha256=_digest(_canonical(body)))


def _invalid_constant(_):
    raise ValueError('nonfinite_json')


def validate_evidence(raw):
    """Validate bounded canonical bytes, checksums AND original account arithmetic.

    Checksums are not signatures. A valid old snapshot is not the current account.
    All orders/fills are validated even though the returned summary is bounded.
    """
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_EVIDENCE_BYTES:
            raise ValueError('invalid_evidence_size')
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=_invalid_constant)
        if (not isinstance(data, dict) or set(data) != _FIELDS
                or data['format'] != FORMAT or type(data['format_version']) is not int
                or data['format_version'] != 1 or data['mode'] != 'paper'
                or data['read_only'] is not True or data['live_enabled'] is not False
                or raw != (_canonical(data) + '\n').encode('utf-8')):
            raise ValueError('invalid_evidence_envelope')
        stamp = data['captured_at']
        if (not isinstance(stamp, str) or not re.fullmatch(
                r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00', stamp)):
            raise ValueError('invalid_capture_time')
        datetime.fromisoformat(stamp)  # Reject impossible dates, not just format.
        source = data['snapshot']
        if (not isinstance(source, dict) or set(source) != _SOURCE_FIELDS
                or type(source['session_version']) is not int
                or source['session_version'] not in (1, 2, 3)
                or not isinstance(source['revision'], str)
                or not re.fullmatch(r'0|[1-9][0-9]{0,18}', source['revision'])
                or int(source['revision']) >= 2**62):
            raise ValueError('invalid_snapshot_metadata')
        body = {key: value for key, value in data.items() if key != 'content_sha256'}
        if data['content_sha256'] != _digest(_canonical(body)):
            raise ValueError('invalid_envelope_digest')
        payload = _canonical(data['payload'])
        if source['digest'] != _digest(payload):
            raise ValueError('invalid_payload_digest')
        bundle, orders, book = _bundle(payload)
        version = 3 if 'control' in bundle else 2 if 'policy' in bundle else 1
        if source['session_version'] != version or source['account_id'] != bundle['account_id']:
            raise ValueError('inconsistent_snapshot_identity')
        from trading_core.paper_session_observer import _project
        report = _project(bundle, orders, book, version, int(source['revision']), source['digest'])
        return dict(valid=True, format=FORMAT, format_version=1,
                    scope='offline_snapshot_not_current_account', mode='paper',
                    read_only=True, live_enabled=False, captured_at=stamp,
                    verified_at=report['observed_at'], snapshot=source,
                    content_sha256=data['content_sha256'], account=report['account'],
                    orders={key: value for key, value in report['orders'].items()
                            if key not in ('items', 'truncated')},
                    control=report['control'], policy=report['policy'],
                    observation=report['observation'])
    except Exception:
        raise PaperEvidenceInvalid('paper_evidence_invalid') from None


def _file_info(info):
    # Hardlinks are safe for this read-only artifact. A crash after publication
    # can leave the temporary link; do not require modifying evidence to verify.
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_EVIDENCE_BYTES):
        raise ValueError('private_regular_evidence_required')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid)


def _read_evidence(path):
    """Return validated captured bytes and summary from the SAME private-file read."""
    fd = None
    try:
        target = Path(path).absolute()
        before = _file_info(target.lstat())
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if _file_info(os.fstat(fd)) != before:
            raise ValueError('evidence_changed')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(MAX_EVIDENCE_BYTES + 1)
            report = validate_evidence(raw)
            if _file_info(os.fstat(stream.fileno())) != before:
                raise ValueError('evidence_changed')
        if _file_info(target.lstat()) != before:
            raise ValueError('evidence_replaced')
        return raw, report
    except Exception:
        raise PaperEvidenceInvalid('paper_evidence_invalid') from None
    finally:
        if fd is not None:
            os.close(fd)


def verify_evidence(path):
    """Read one private regular file, without SQLite or any write/repair."""
    return _read_evidence(path)[1]


def _publish(raw, output):
    """POSIX same-directory no-clobber publish; never unlink a final target."""
    directory = fd = None
    temporary = None
    created_id = None
    published = False
    try:
        target = Path(output).absolute()
        parent = target.parent.resolve(strict=True)
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('private_output_directory_required')
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        held = os.fstat(directory)
        if (held.st_dev, held.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('directory_changed')
        try:
            os.stat(target.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError('output_exists')
        candidate = '.paper-evidence-' + secrets.token_hex(16) + '.tmp'
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        temporary = candidate  # Own cleanup only after exclusive creation succeeded.
        created = os.fstat(fd)
        created_id = created.st_dev, created.st_ino
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target.name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=directory)
        temporary = None
        os.fsync(directory)
        final = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
        current_parent = parent.stat()
        if ((final.st_dev, final.st_ino) != created_id
                or (current_parent.st_dev, current_parent.st_ino) != (held.st_dev, held.st_ino)):
            raise ValueError('published_path_changed')
    except Exception:
        # A link call can succeed then lose its reply. Never remove final output.
        if not published and directory is not None and created_id is not None:
            try:
                final = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
                published = (final.st_dev, final.st_ino) == created_id
            except OSError:
                pass
        if published:
            raise PaperEvidencePublishedError('paper_evidence_published_check_required') from None
        raise PaperEvidenceWriteError('paper_evidence_output_unavailable') from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            try:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except OSError:
                        pass  # Leave an orphan temp, not a truncated final file.
            finally:
                os.close(directory)


def export_evidence(observer, output, *, expected_revision=None, expected_digest=None):
    """Save all records from one successful observation; no automatic retry."""
    evidence = observer.read_evidence(expected_revision=expected_revision,
                                      expected_digest=expected_digest)
    raw = (_canonical(evidence) + '\n').encode('utf-8')
    report = validate_evidence(raw)
    _publish(raw, output)
    return report
