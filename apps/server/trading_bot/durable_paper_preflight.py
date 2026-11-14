"""Read-only diagnostic for an opted-in historical paper recovery.

Compatible captured state is not an owner lease or permission to resume. The
existing recovery command must independently validate under its own locks.
No durable session, lock file, order action or evidence publication is opened.
"""
from datetime import datetime, timezone
from decimal import localcontext
import os
from pathlib import Path

from trading_bot import durable_paper_resume as recovery
from trading_bot.paper_runner import PaperRunner
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_core.paper_session_observer import PaperSessionObserver, PaperSessionUnavailable

_SOURCE_NAMES = ('resume.json', 'input.json', 'before.paper-evidence.json')
_BLOCKED_REASONS = frozenset({
    'private_directory_required', 'invalid_manifest', 'input_changed',
    'engine_changed', 'initial_evidence_changed', 'invalid_input',
    'configuration_changed', 'invalid_initial_account', 'account_changed',
    'unexpected_book_state', 'mark_changed', 'fill_history_changed',
    'missing_fill', 'order_history_changed', 'input_too_large',
    'noncanonical_input', 'file_changed',
})


def _report(status, reason, **details):
    return dict(schema_version=1, source='durable_paper_preflight', mode='paper',
                read_only=True, live_enabled=False, resume_authorized=False,
                owner_state='unobserved', scope='captured_historical_state_only',
                observed_at=datetime.now(timezone.utc).isoformat(),
                status=status, reason=reason, **details)


def _source_signatures(directory):
    signatures = []
    for name in _SOURCE_NAMES:
        path = directory / name
        recovery._identity(path)
        signatures.append(recovery._stat_signature(path.lstat()))
    return tuple(signatures)


def _completion_present(directory):
    # Presence forbids another execution, even if this is corrupt or a symlink.
    # It is deliberately NOT described as a validated completion.
    return os.path.lexists(directory / 'result.json')


async def inspect_durable_replay(directory):
    """Inspect without mutating account state, creating locks or normalizing orders.

    Input and execution fingerprints retain the original v32 rules. Two validated
    observations must agree before returning counts. Concurrent writes can still
    happen AFTER this check; the report never certifies future availability.
    Cancellation propagates; fixed failure reports contain no raw paths or data.
    """
    try:
        target = Path(directory).absolute()
        directory_id = recovery._private_directory(target)
        if _completion_present(target):
            return _report('blocked', 'completion_present', completion_validated=False)
        if not os.path.lexists(target / recovery.MANIFEST):
            return _report('blocked', 'manifest_missing')
        signatures = _source_signatures(target)
        manifest, raw, config, candles, before = recovery._load(target)
        observer = PaperSessionObserver(target / 'account.sqlite')
        captured = observer.read_evidence()
        with localcontext(PAPER_CALCULATION_CONTEXT):
            expected = await PaperRunner(config).run(candles)
        if (recovery._private_directory(target) != directory_id
                or _source_signatures(target) != signatures):
            return _report('blocked', 'source_changed')
        if _completion_present(target):
            return _report('blocked', 'completion_present', completion_validated=False)
        # Check input bytes and code again; no second file stream is used for
        # accounting. The original captured account remains the report's basis.
        latest_manifest, latest_raw, _, _, latest_before = recovery._load(target)
        if latest_manifest != manifest or latest_raw != raw or latest_before != before:
            return _report('blocked', 'source_changed')
        current = observer.read_evidence()
        if current['snapshot'] != captured['snapshot']:
            return _report('blocked', 'observation_changed')
        count, unresolved = recovery._check_account(captured['payload'], manifest,
            before, expected, True, candles)
        if (recovery._private_directory(target) != directory_id
                or _source_signatures(target) != signatures):
            return _report('blocked', 'source_changed')
        if _completion_present(target):
            return _report('blocked', 'completion_present', completion_validated=False)
        status = 'reconciliation_required' if unresolved else 'compatible_snapshot'
        return _report(status, 'saved_fill_confirmation_required' if unresolved else 'prefix_matches',
            snapshot=captured['snapshot'], stored_mode=captured['payload']['control']['mode'],
            counts=dict(input_bars=len(candles), saved_fills=count,
                        expected_fills=len(expected['trades']),
                        remaining_fills=len(expected['trades']) - count,
                        unconfirmed_saved_fills=len(unresolved)),
            checks=dict(frozen_input=True, execution_fingerprint=True,
                        account_validation=True, original_fill_prefix=True,
                        stable_observation=True),
            boundaries=dict(account_writes=False, orders_submitted=False,
                            owner_availability='not_checked', full_history_replayed_in_memory=True,
                            completion_validated=False, authenticated_history=False))
    except PaperSessionUnavailable:
        return _report('unavailable', 'account_unavailable')
    except (OSError, TypeError):
        return _report('unavailable', 'source_unavailable')
    except Exception as error:
        code = str(error)
        prefix = 'durable_resume_'
        reason = code[len(prefix):] if code.startswith(prefix) else ''
        if reason in _BLOCKED_REASONS:
            return _report('blocked', reason)
        return _report('unavailable', 'inspection_failed')
