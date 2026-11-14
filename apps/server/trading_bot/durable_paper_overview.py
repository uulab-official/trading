"""Bounded, sequential, read-only inspections of explicitly named historical runs.

These are separate captured observations, never an atomic portfolio or permission
for execution. Existing audit/preflight own all economic and history validation.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re

from trading_bot import durable_paper_resume as recovery
from trading_bot.durable_paper_audit import audit_durable_replay
from trading_bot.durable_paper_preflight import inspect_durable_replay

MAX_RUNS = 8
STATUSES = ('verified_completion', 'compatible_snapshot', 'reconciliation_required',
            'blocked', 'unavailable')


def _now():
    return datetime.now(timezone.utc).isoformat()


def validate_runs(runs):
    """Validate the complete explicit list before inspecting any account."""
    if not isinstance(runs, (list, tuple)) or not 1 <= len(runs) <= MAX_RUNS:
        raise ValueError('invalid_arguments')
    clean, labels, paths = [], set(), set()
    for entry in runs:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError('invalid_arguments')
        label, raw = entry
        if not isinstance(label, str) or not re.fullmatch(r'[a-zA-Z0-9_가-힣-]{1,40}', label):
            raise ValueError('invalid_arguments')
        try:
            raw = os.fspath(raw)
            if not isinstance(raw, str) or not raw or '\0' in raw or len(raw) > 4096:
                raise ValueError('invalid_arguments')
            path = Path(os.path.abspath(raw))
        except (TypeError, OSError):
            raise ValueError('invalid_arguments') from None
        if label in labels or path in paths:
            raise ValueError('invalid_arguments')
        labels.add(label)
        paths.add(path)
        clean.append((label, path))
    return clean


def _marker(path):
    try:
        return recovery._stat_signature((path / 'result.json').lstat())
    except FileNotFoundError:
        return None


def _failure(status, reason):
    return dict(status=status, reason=reason, mode='paper', read_only=True,
                completion_validated=False, resume_authorized=False,
                live_enabled=False, owner_state='unobserved')


async def _inspect_one(path):
    kind = 'undetermined'
    try:
        identity = recovery._private_directory(path)
        marker = _marker(path)
        kind = 'completion_audit' if marker is not None else 'preflight'
        inspector = audit_durable_replay if marker is not None else inspect_durable_replay
        diagnostic = await inspector(path)
        if recovery._private_directory(path) != identity or _marker(path) != marker:
            return kind, _failure('blocked', 'source_changed')
        if (not isinstance(diagnostic, dict) or diagnostic.get('status') not in STATUSES
                or diagnostic.get('mode') != 'paper' or diagnostic.get('read_only') is not True
                or diagnostic.get('resume_authorized') is not False
                or diagnostic.get('live_enabled') is not False
                or not isinstance(diagnostic.get('reason'), str)
                or not re.fullmatch(r'[a-z_]{1,80}', diagnostic['reason'])
                or (diagnostic['status'] == 'verified_completion' and
                    (kind != 'completion_audit' or diagnostic.get('completion_validated') is not True))
                or (kind == 'completion_audit' and diagnostic['status'] in
                    ('compatible_snapshot', 'reconciliation_required'))):
            return kind, _failure('unavailable', 'invalid_inspection_result')
        return kind, diagnostic
    except ValueError as error:
        if str(error) == 'durable_resume_private_directory_required':
            return kind, _failure('blocked', 'private_directory_required')
        return kind, _failure('unavailable', 'inspection_failed')
    except OSError:
        return kind, _failure('unavailable', 'source_unavailable')
    except Exception:
        return kind, _failure('unavailable', 'inspection_failed')


async def inspect_runs(runs):
    """Keep failed rows; propagate cancellation; never create a writer or lock.

    Each successful row retains its own validator's snapshot and observation time.
    No promise is made that earlier accounts remain unchanged when this call ends.
    """
    targets = validate_runs(runs)
    started = _now()
    rows = []
    for label, path in targets:
        row_started = _now()
        kind, diagnostic = await _inspect_one(path)
        rows.append(dict(label=label, inspection=kind, status=diagnostic['status'],
            reason=diagnostic['reason'], started_at=row_started, finished_at=_now(),
            diagnostic=diagnostic))
    counts = {status: sum(row['status'] == status for row in rows) for status in STATUSES}
    counts['total'] = len(rows)
    return dict(schema_version=1, source='durable_paper_run_overview', mode='paper',
        read_only=True, live_enabled=False, resume_authorized=False,
        owner_state='unobserved', simultaneous_snapshot=False,
        scope='independent_historical_observations', started_at=started, finished_at=_now(),
        counts=counts, all_completed=counts['verified_completion'] == len(rows), runs=rows,
        warnings=['observations_are_not_simultaneous', 'earlier_observations_may_change',
                  'not_execution_authorization', 'private_identifiers', 'not_profitability_evidence'])


def exit_code(report):
    """Exit 0 includes compatible incomplete observations, never trading approval."""
    if report['counts']['blocked'] or report['counts']['unavailable']:
        return 2
    return 4 if report['counts']['reconciliation_required'] else 0
