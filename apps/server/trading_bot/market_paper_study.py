"""Fixed-plan historical experiments on original, independently audited paper accounts.

No optimizer, broker, automatic retry/recovery or new execution arithmetic.
Inspection describes sequential captured observations, never live authorization.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
import json
import os
from pathlib import Path
import re

from trading_bot import durable_paper_resume as recovery
from trading_bot.durable_paper_audit import audit_durable_replay, MAX_RESULT, _result_contract
from trading_bot.durable_paper_preflight import inspect_durable_replay
from trading_bot.market_paper_run import (
    _capture, _hash, _read_regular, _signature, run_market_replay, validate_bars,
)
from trading_bot.paper_runner import PaperRunConfig
from trading_core.order_state_store import _canonical, _identity
from trading_core.paper_evidence import _publish
from trading_core.paper_session_observer import PaperSessionObserver

MAX_PLAN_BYTES = 64 * 1024
MAX_CANDIDATE_BARS = 20000
COMMON = frozenset(('initial_balance', 'position_size_pct', 'fee_pct', 'slippage_pct',
                    'max_daily_loss_pct', 'max_drawdown_pct'))
_DIFF_CONTEXT = Context(prec=256)


class MarketStudyInvalid(ValueError):
    """Fixed public codes only; no input values or filesystem details."""


def _require(condition, code='market_study_invalid_plan'):
    if not condition:
        raise MarketStudyInvalid(code)


def _bytes(value):
    return (_canonical(value) + '\n').encode('utf-8')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _prepare(plan, data):
    """Freeze all effective configurations before starting any candidate."""
    try:
        raw = _bytes(plan)
        _require(len(raw) <= MAX_PLAN_BYTES)
        plan = json.loads(raw)
        _require(isinstance(plan, dict) and set(plan) == {'version', 'common', 'candidates'}
                 and type(plan['version']) is int and plan['version'] == 1)
        common, candidates = plan['common'], plan['candidates']
        _require(isinstance(common, dict) and set(common) == COMMON)
        for value in common.values():
            _require(isinstance(value, str) and len(value) <= 100)
            number = Decimal(value)
            _require(number.is_finite() and len(number.as_tuple().digits) <= 64
                     and -18 <= number.as_tuple().exponent <= 18)
        _require(Decimal(common['initial_balance']) <= Decimal('1e18'))
        _require(isinstance(candidates, list) and 2 <= len(candidates) <= 8)
        _require(len(data['candles']) * len(candidates) <= MAX_CANDIDATE_BARS)
        labels, effective, records = set(), set(), []
        for candidate in candidates:
            _require(isinstance(candidate, dict))
            label, strategy = candidate.get('id'), candidate.get('strategy')
            _require(isinstance(label, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', label)
                     and label not in labels)
            fields = {'fast_period', 'slow_period'} if strategy == 'ma_cross' else {'rsi_period'}
            _require(strategy in ('ma_cross', 'rsi') and set(candidate) == {'id', 'strategy'} | fields)
            config = PaperRunConfig(symbol=data['symbol'], timeframe=data['timeframe'],
                **common, **{k: v for k, v in candidate.items() if k != 'id'})
            identity = config.identity()
            key = _canonical({k: identity[k] for k in {'strategy'} | fields})
            _require(key not in effective)
            labels.add(label); effective.add(key)
            candles = validate_bars(data['candles'], config)
            frozen = dict(mode='paper', config=identity, candles=[
                [c.timestamp, *[str(getattr(c, k)) for k in ('open','high','low','close','volume')]]
                for c in candles])
            records.append(dict(id=label, config=identity, execution_input_sha256=_hash(_bytes(frozen))))
        return plan, records
    except MarketStudyInvalid:
        raise
    except Exception:
        raise MarketStudyInvalid('market_study_invalid_plan') from None


class _Pins:
    """Detect replacement/content changes, not authenticate a hostile local writer."""
    def __init__(self):
        self.directories, self.files, self.absences = {}, {}, []

    def directory(self, path):
        self.directories[path] = recovery._private_directory(path)

    def file(self, path):
        _identity(path)
        self.files[path] = _signature(path.lstat())

    def absent(self, path):
        _require(not os.path.lexists(path), 'market_study_changed')
        self.absences.append(path)

    def check(self):
        _require(all(recovery._private_directory(p) == value for p, value in self.directories.items())
            and all(_signature(p.lstat()) == value for p, value in self.files.items())
            and all(not os.path.lexists(p) for p in self.absences), 'market_study_changed')


def _manifest(source_raw, plan, records):
    return dict(format='uulab.market-paper-study', version=1, mode='paper', live_enabled=False,
        authenticated=False, execution_sha256=recovery._engine_fingerprint(),
        source_sha256=_hash(source_raw), plan_sha256=_hash(_bytes(plan)),
        candidates=records)


def _load(directory):
    target = Path(directory).absolute()
    pins = _Pins()
    pins.directory(target); pins.directory(target/'runs')
    for name in ('source.json', 'plan.json', 'study.json'):
        pins.file(target/name)
    source_raw, data, source = _capture(target/'source.json', None)
    plan_raw, plan = recovery._read_private(target/'plan.json', MAX_PLAN_BYTES)
    plan, records = _prepare(plan, data)
    manifest_raw, manifest = recovery._read_private(target/'study.json', MAX_PLAN_BYTES)
    _require(plan_raw == _bytes(plan) and manifest_raw == _bytes(_manifest(source_raw, plan, records)),
             'market_study_binding_invalid')
    pins.check()
    return dict(target=target, pins=pins, raw=source_raw, source=source, plan=plan,
                records=records, manifest_raw=manifest_raw, manifest=manifest)


def _check_study(study):
    _require(recovery._engine_fingerprint() == study['manifest']['execution_sha256'],
             'market_study_engine_changed')
    study['pins'].check()


def _row(record, status, reason, **extra):
    return dict(id=record['id'], config=record['config'], status=status, reason=reason,
        observed_at=_now(), run_directory='runs/'+record['id']+'/run', metrics=None,
        return_delta_pp=None, **extra)


def _bundle_manifest(study, record):
    return dict(format='uulab.market-paper-source', version=1, mode='paper',
        live_enabled=False, authenticated=False, source_file='source.json', run_directory='run',
        source_sha256=study['source']['source_sha256'],
        execution_input_sha256=record['execution_input_sha256'],
        provenance=study['source']['provenance'], config=record['config'])


async def _candidate(study, record):
    path = study['target']/'runs'/record['id']
    pins = _Pins()
    try:
        if not os.path.lexists(path):
            pins.absent(path)
            return _row(record, 'not_started', 'candidate_not_started'), (pins, None, None, None)
        pins.directory(path)
        for name in ('source.json', 'source-manifest.json'):
            pins.file(path/name)
        _require(_read_regular(path/'source.json') == study['raw'], 'candidate_binding_invalid')
        _, manifest = recovery._read_private(path/'source-manifest.json', MAX_PLAN_BYTES)
        _require(_canonical(manifest) == _canonical(_bundle_manifest(study, record)), 'candidate_binding_invalid')
        child = path/'run'
        if not os.path.lexists(child):
            pins.absent(child); pins.check()
            return _row(record, 'incomplete', 'candidate_setup_incomplete'), (pins, None, None, None)
        pins.directory(child)
        pins.file(child/'input.json')
        raw, value = recovery._read_private(child/'input.json')
        _require(_hash(raw) == record['execution_input_sha256']
                 and _canonical(value['config']) == _canonical(record['config']), 'candidate_binding_invalid')
        if not os.path.lexists(child/'result.json'):
            pins.absent(child/'result.json')
            report = await inspect_durable_replay(child)
            pins.check()
            status = report.get('status')
            _require(status in {'compatible_snapshot', 'reconciliation_required', 'blocked', 'unavailable'},
                     'candidate_diagnostic_invalid')
            return _row(record, status, report['reason']), (pins, None, None, None)
        for name in ('result.json', 'resume.json', 'before.paper-evidence.json'):
            pins.file(child/name)
        result_raw, result = recovery._read_private(child/'result.json', MAX_RESULT)
        final_name = _result_contract(result)
        pins.file(child/final_name)  # Pin BEFORE the asynchronous audit.
        audit = await audit_durable_replay(child)
        if audit.get('status') != 'verified_completion' or audit.get('completion_validated') is not True:
            return _row(record, 'blocked', 'candidate_audit_failed'), None
        _require(final_name == audit['final_evidence']
                 and _hash(result_raw) == audit['result_sha256'], 'candidate_changed')
        replay = result['replay']
        _require(_canonical(replay['assumptions']) == _canonical(record['config']), 'candidate_binding_invalid')
        row = _row(record, 'verified_completion', 'original_audit_and_source_match',
                   snapshot=audit['snapshot'], result_sha256=audit['result_sha256'],
                   input_sha256=audit['input_sha256'])
        row['metrics'] = dict(equity=replay['equity'], cash=replay['cash'],
            return_pct=replay['total_return_pct'], fees=replay['total_fees'],
            fills=len(replay['trades']), bars=replay['bars_processed'],
            daily_loss_halted=replay['daily_loss_halted'], drawdown_halted=replay['drawdown_halted'])
        pins.check()
        return row, (pins, child/'account.sqlite', audit['snapshot'], audit['execution_sha256'])
    except Exception:
        return _row(record, 'blocked', 'candidate_binding_or_observation_invalid'), None


def _recheck(guard):
    if guard is None:
        return
    pins, account, snapshot, engine = guard
    if engine is not None:
        _require(recovery._engine_fingerprint() == engine, 'candidate_engine_changed')
    if account is not None:
        _require(PaperSessionObserver(account).read_evidence()['snapshot'] == snapshot, 'candidate_changed')
    pins.check()


async def _collect(study):
    rows, guards = [], []
    for record in study['records']:
        row, guard = await _candidate(study, record)
        rows.append(row); guards.append(guard)
    # A later inspection must not silently leave an earlier success stale.
    for index, guard in enumerate(guards):
        try:
            _recheck(guard)
        except Exception:
            rows[index] = _row(study['records'][index], 'blocked', 'candidate_changed')
            guards[index] = None
    _check_study(study)
    all_verified = all(row['status'] == 'verified_completion' for row in rows)
    if all_verified:
        with localcontext(_DIFF_CONTEXT):
            baseline = Decimal(rows[0]['metrics']['return_pct'])
            for row in rows:
                row['return_delta_pp'] = str(Decimal(row['metrics']['return_pct']) - baseline)
    blocked = any(row['status'] in {'blocked', 'unavailable'} for row in rows)
    report = dict(schema_version=1, source='market_paper_study', mode='paper',
        read_only=True, operation='inspect_existing_study', live_enabled=False, resume_authorized=False,
        observed_at=_now(), status='blocked' if blocked else ('complete_without_receipt' if all_verified else 'incomplete_study'),
        reason='candidate_blocked' if blocked else ('receipt_missing' if all_verified else 'candidate_incomplete'),
        baseline=rows[0]['id'], source_sha256=study['source']['source_sha256'],
        symbol=study['source']['symbol'], timeframe=study['source']['timeframe'],
        bar_count=study['source']['bar_count'], common=study['plan']['common'],
        counts=dict(Counter(row['status'] for row in rows)), all_candidates_verified=all_verified,
        receipt_validated=False, candidates=rows,
        boundaries=dict(observations='sequential_not_simultaneous', returns='historical_simulation',
            out_of_sample=False, automatic_selection=False, live_authorization=False,
            source_authenticated=False, prices='historical_saved_marks', measured_mdd_included=False))
    stable_rows = [{k:v for k,v in row.items() if k != 'observed_at'} for row in rows]
    receipt = dict(format='uulab.market-paper-study-result', version=1,
                   study_sha256=_hash(study['manifest_raw']), candidates=stable_rows)
    return report, receipt, guards


def _verified(report):
    report['status'] = 'verified_study'
    report['reason'] = 'all_candidates_and_receipt_match'
    report['receipt_validated'] = True
    return report


async def inspect_market_study(directory) -> dict:
    """Read-only, no run lock/writer/recovery. Retain all planned candidate rows."""
    try:
        study = _load(directory)
        result_path = study['target']/'study-result.json'
        result_pins = _Pins()
        saved, receipt_invalid = None, False
        if os.path.lexists(result_path):
            try:
                result_pins.file(result_path)
                _, saved = recovery._read_private(result_path, MAX_RESULT)
            except Exception:
                receipt_invalid = True
        else:
            result_pins.absent(result_path)
        report, receipt, guards = await _collect(study)
        try:
            result_pins.check()
        except Exception:
            receipt_invalid = True
        _check_study(study)
        if receipt_invalid:
            report.update(status='blocked', reason='study_receipt_invalid', receipt_validated=False)
            for row in report['candidates']:
                row['return_delta_pp'] = None
        elif saved is not None:
            if report['all_candidates_verified'] and _canonical(saved) == _canonical(receipt):
                return _verified(report)
            report.update(status='blocked', reason='study_receipt_mismatch', receipt_validated=False)
            for row in report['candidates']:
                row['return_delta_pp'] = None
        return report
    except FileNotFoundError:
        status, reason = 'unavailable', 'study_source_unavailable'
    except Exception:
        status, reason = 'blocked', 'study_binding_or_observation_invalid'
    return dict(schema_version=1, source='market_paper_study', mode='paper', read_only=True,
        operation='inspect_existing_study',
        live_enabled=False, resume_authorized=False, status=status, reason=reason,
        receipt_validated=False, all_candidates_verified=False, candidates=[], observed_at=_now())


async def run_market_study(path, plan: dict, *, directory) -> dict:
    """Create once; execute sequentially; stop on failure without retrying a child."""
    source_raw, data, _ = _capture(path, None)
    plan, records = _prepare(plan, data)
    study_raw = _bytes(_manifest(source_raw, plan, records))
    target = Path(directory).absolute()
    target = target.parent.resolve(strict=True) / target.name
    target.mkdir(mode=0o700)
    root_identity = recovery._private_directory(target)
    (target/'runs').mkdir(mode=0o700)
    for name, raw in (('source.json', source_raw), ('plan.json', _bytes(plan)),
                      ('study.json', study_raw)):
        _require(recovery._private_directory(target) == root_identity, 'market_study_changed')
        _publish(raw, target/name)
    study = _load(target)
    for record in study['records']:
        _check_study(study)
        await run_market_replay(target/'source.json', directory=target/'runs'/record['id'],
                                config=PaperRunConfig(**record['config']))
        _check_study(study)
        row, guard = await _candidate(study, record)
        _require(row['status'] == 'verified_completion', 'market_study_candidate_failed')
        _recheck(guard)
    report, receipt, guards = await _collect(study)
    _require(report['all_candidates_verified'], 'market_study_candidate_failed')
    for guard in guards:
        _recheck(guard)
    _check_study(study)
    _publish(_bytes(receipt), target/'study-result.json')
    # If a later check fails, keep the possibly published result; never overwrite it.
    for guard in guards:
        _recheck(guard)
    _check_study(study)
    _require(_read_regular(target/'study-result.json', MAX_RESULT) == _bytes(receipt), 'market_study_changed')
    report.update(read_only=False, operation='execute_new_study')
    return _verified(report)
