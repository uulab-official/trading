"""Verify captured completed historical runs without repairing or executing them.

Requires a v32 opt-in manifest. A verified result is a consistency observation,
not authenticity, current price evidence, an owner lease, or trading authority.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import os
from pathlib import Path
import re

from trading_bot import durable_paper_resume as recovery
from trading_bot.paper_runner import PaperRunner
from trading_core.order_state_store import _canonical
from trading_core.paper_evidence import validate_evidence, PaperEvidenceInvalid
from trading_core.paper_evidence_compare import _compare_captured_evidence, PaperEvidenceComparisonError
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_core.paper_session_observer import PaperSessionObserver, PaperSessionUnavailable

MAX_RESULT = 20 * 1024 * 1024
_BASE_FILES = ('resume.json', 'input.json', 'before.paper-evidence.json', 'result.json')
_RESULT_FIELDS = frozenset(('schema_version', 'source', 'mode', 'live_enabled', 'status',
    'account_id', 'input_sha256', 'replay', 'comparison', 'boundaries'))
_RECOVERY_REASONS = frozenset(('private_directory_required', 'invalid_manifest',
    'input_changed', 'engine_changed', 'initial_evidence_changed', 'invalid_input',
    'configuration_changed', 'invalid_initial_account', 'account_changed',
    'unexpected_book_state', 'mark_changed', 'fill_history_changed', 'missing_fill',
    'order_history_changed', 'reconciliation_required', 'input_too_large',
    'noncanonical_input', 'file_changed'))


class _AuditConflict(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _AuditConflict(reason)


def _report(status, reason, **details):
    return dict(schema_version=1, source='durable_paper_completion_audit', mode='paper',
        read_only=True, live_enabled=False, resume_authorized=False, owner_state='unobserved',
        completion_validated=status == 'verified_completion',
        scope='captured_historical_completion_only',
        observed_at=datetime.now(timezone.utc).isoformat(), status=status, reason=reason, **details)


def _signatures(directory, names):
    result = {}
    for name in names:
        path = directory / name
        recovery._identity(path)
        result[name] = recovery._stat_signature(path.lstat())
    return result


def _utc(value):
    _require(isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00', value), 'invalid_result')
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise _AuditConflict('invalid_result') from None


def _result_contract(value):
    _require(isinstance(value, dict), 'invalid_result')
    recovered = 'recovery' in value
    fields = _RESULT_FIELDS | {'recovery', 'final_evidence'} if recovered else _RESULT_FIELDS
    _require(set(value) == fields and type(value['schema_version']) is int
        and value['schema_version'] == 1 and value['source'] == 'durable_historical_paper_run'
        and value['mode'] == 'paper' and value['live_enabled'] is False
        and value['status'] == 'completed_halted' and isinstance(value['replay'], dict)
        and isinstance(value['comparison'], dict), 'invalid_result')
    boundary = dict(prices='historical_inputs', process_liveness='not_asserted',
        resume_supported=True, risk_controls='original_paper_runner',
        quote_freshness='not_applicable_to_historical_replay')
    _require(_canonical(value['boundaries']) == _canonical(boundary), 'invalid_result')
    _utc(value['comparison'].get('compared_at'))
    if not recovered:
        return 'after.paper-evidence.json'
    meta = value['recovery']
    _require(isinstance(meta, dict) and set(meta) == {
        'method', 'existing_fills_skipped', 'saved_fills_reconciled'}
        and meta['method'] == 'deterministic_prefix_replay', 'invalid_result')
    for key in ('existing_fills_skipped', 'saved_fills_reconciled'):
        _require(type(meta[key]) is int and 0 <= meta[key] <= 10000, 'invalid_result')
    _require(meta['saved_fills_reconciled'] <= meta['existing_fills_skipped'], 'invalid_result')
    name = value['final_evidence']
    _require(isinstance(name, str) and re.fullmatch(
        r'resume-[a-f0-9]{32}\.paper-evidence\.json', name), 'invalid_result')
    return name


def _sources_stable(target, directory_id, signatures, engine):
    _require(recovery._engine_fingerprint() == engine, 'engine_changed')
    # Source checks are LAST: hashing execution sources can yield to another writer.
    _require(recovery._private_directory(target) == directory_id
             and _signatures(target, signatures) == signatures, 'source_changed')


async def audit_durable_replay(directory):
    """Reproduce and cross-check completion, never opening a durable writer.

    Counts include every fill, not the capped UI/comparison arrays. Concurrent
    changes invalidate the observation; checks do not freeze future mutations.
    Cancellation propagates to the caller without changing the saved account.
    """
    try:
        target = Path(directory).absolute()
        directory_id = recovery._private_directory(target)
        if not os.path.lexists(target / 'result.json'):
            return _report('blocked', 'completion_missing')
        if not os.path.lexists(target / recovery.MANIFEST):
            return _report('blocked', 'manifest_missing')
        signatures = _signatures(target, _BASE_FILES)
        result_raw, result = recovery._read_private(target / 'result.json', MAX_RESULT)
        final_name = _result_contract(result)
        signatures.update(_signatures(target, (final_name,)))
        manifest, input_raw, config, candles, before = recovery._load(target)
        _require(result['account_id'] == manifest['account_id']
            and result['input_sha256'] == recovery._hash(input_raw), 'result_identity_mismatch')
        before_raw = (_canonical(before) + '\n').encode()
        before_summary = validate_evidence(before_raw)
        final_raw, final = recovery._read_private(target / final_name, MAX_RESULT)
        final_summary = validate_evidence(final_raw)
        _require(final_summary['control']['mode'] == 'halted'
                 and final_summary['orders']['unresolved'] == 0, 'final_account_not_halted_confirmed')
        observer = PaperSessionObserver(target / 'account.sqlite')
        captured = observer.read_evidence()
        _require(captured['snapshot'] == final['snapshot'], 'account_changed_after_completion')
        with localcontext(PAPER_CALCULATION_CONTEXT):
            expected = await PaperRunner(config).run(candles)
            _require(_canonical(expected) == _canonical(result['replay']), 'replay_mismatch')
            count, unresolved = recovery._check_account(final['payload'], manifest,
                before, expected, False, candles)
            _require(count == len(expected['trades']) and not unresolved, 'incomplete_fills')
            mark = final['payload']['book']['tickers'].get(config.symbol)
            _require(mark is not None and mark['timestamp'] == candles[-1].timestamp
                     and Decimal(mark['last']) == candles[-1].close, 'final_mark_mismatch')
            account = final_summary['account']
            _require(expected['cash'] == account['cash']
                and expected['total_fees'] == account['total_fees']
                and expected['equity'] == account['marked_equity'], 'final_account_mismatch')
            comparison = _compare_captured_evidence((before_raw, before_summary),
                                                   (final_raw, final_summary))
            # Generation time is nondeterministic, but every economic/source field is checked.
            recorded = {k:v for k,v in result['comparison'].items() if k != 'compared_at'}
            calculated = {k:v for k,v in comparison.items() if k != 'compared_at'}
            _require(_canonical(recorded) == _canonical(calculated), 'comparison_mismatch')
        if 'recovery' in result:
            _require(result['recovery']['existing_fills_skipped'] <= count, 'invalid_result')
        _sources_stable(target, directory_id, signatures, manifest['engine_sha256'])
        current = observer.read_evidence()
        _require(current['snapshot'] == captured['snapshot'], 'observation_changed')
        _sources_stable(target, directory_id, signatures, manifest['engine_sha256'])
        return _report('verified_completion', 'artifacts_and_account_match',
            snapshot=final['snapshot'], final_evidence=final_name,
            result_sha256=recovery._hash(result_raw), input_sha256=recovery._hash(input_raw),
            execution_sha256=manifest['engine_sha256'],
            counts=dict(input_bars=len(candles), fills=count, unresolved_orders=0),
            account=dict(quote_currency=account['quote_currency'], cash=account['cash'],
                         saved_mark_equity=account['marked_equity'], total_fees=account['total_fees']),
            checks=dict(original_report=True, complete_fills=True, evidence_comparison=True,
                halted_confirmed=True, current_snapshot_matches=True, stable_sources=True,
                recovery_counters_proven=False),
            boundaries=dict(account_writes=False, owner_availability='not_checked',
                authenticated_history=False, prices='historical_saved_marks',
                full_history_replayed_in_memory=True, profitability_verified=False))
    except _AuditConflict as error:
        return _report('blocked', str(error))
    except PaperSessionUnavailable:
        return _report('unavailable', 'account_unavailable')
    except PaperEvidenceInvalid:
        return _report('blocked', 'invalid_evidence')
    except PaperEvidenceComparisonError:
        return _report('blocked', 'comparison_mismatch')
    except (OSError, TypeError):
        return _report('unavailable', 'source_unavailable')
    except Exception as error:
        code = str(error)
        prefix = 'durable_resume_'
        reason = code[len(prefix):] if code.startswith(prefix) else ''
        if reason in _RECOVERY_REASONS:
            return _report('blocked', reason)
        return _report('unavailable', 'audit_failed')
