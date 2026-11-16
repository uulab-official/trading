"""Explicit recovery of opt-in historical paper runs, never live execution.

Recompute the original strategy in memory, verify the stored trade prefix and
forward only its missing suffix. Unknown intents without a saved fill are NOT
safe to retry. Source fingerprints are consistency guards, not authentication.
"""
from contextlib import contextmanager
from decimal import Decimal, localcontext
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import uuid

from trading_core.order_manager import OrderManager, OrderStatus
from trading_core.order_reconciliation import reconcile_order
from trading_core.order_state_store import _canonical, _identity, _pairs
from trading_core.paper_evidence import _publish, export_evidence, verify_evidence
from trading_core.paper_evidence_compare import compare_evidence
from trading_core.paper_exchange import PaperExchange, PAPER_CALCULATION_CONTEXT
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver
from trading_bot.paper_runner import PaperRunner, PaperRunConfig
from trading_bot.durable_paper_run import validate_bars, _halt

MANIFEST = 'resume.json'
MAX_INPUT = 4 * 1024 * 1024


def _require(value, reason):
    if not value:
        raise ValueError('durable_resume_' + reason)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _private_directory(path):
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and not info.st_mode & 0o077, 'private_directory_required')
    return info.st_dev, info.st_ino


@contextmanager
def recovery_lock(directory):
    """One cooperating run/resume owner across account close and result publish."""
    import fcntl
    path = Path(directory).absolute()
    identity = _private_directory(path)
    lock = path / '.resume.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        held = os.fstat(fd)
        _require(_identity(lock) == (held.st_dev, held.st_ino), 'lock_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def ensure():
            _require(_private_directory(path) == identity
                     and _identity(lock) == (held.st_dev, held.st_ino), 'lock_changed')
        ensure()
        yield ensure
        ensure()
    finally:
        os.close(fd)  # Do not unlink; do not unlock an inherited parent's lease.


def _stat_signature(info):
    # Read access may update atime; content/identity/permissions must stay fixed.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)


def _read_private(path, limit=MAX_INPUT):
    before = path.lstat()
    identity = _identity(path)
    _require(before.st_size <= limit, 'input_too_large')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        held = os.fstat(stream.fileno())
        _require(identity == (held.st_dev, held.st_ino), 'file_changed')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _require(len(raw) <= limit and _stat_signature(before) == _stat_signature(after)
             and _stat_signature(path.lstat()) == _stat_signature(before),
             'file_changed')
    value = json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    _require(raw == (_canonical(value) + '\n').encode(), 'noncanonical_input')
    return raw, value


def _engine_fingerprint():
    names = ('trading_bot.paper_runner', 'trading_bot.durable_paper_run',
        'trading_bot.durable_paper_resume', 'trading_core.paper_exchange',
        'trading_core.order_manager', 'trading_core.order_reconciliation',
        'trading_core.order_state_store', 'trading_core.paper_session',
        'trading_core.paper_admission', 'trading_core.paper_control',
        'trading_data.candle', 'trading_data.indicator', 'trading_data._validation',
        'trading_strategy.base', 'trading_strategy.ma_strategy', 'trading_strategy.rsi_strategy')
    hashes = {name: _hash(Path(importlib.import_module(name).__file__).read_bytes()) for name in names}
    return _hash(_canonical(dict(sources=hashes,
        python=list(sys.version_info[:2]), implementation=sys.implementation.name)).encode())


def prepare_recovery(directory, account_id, input_raw):
    """Called under the new-run lock, after initial evidence and before activation."""
    before_raw, _ = _read_private(directory/'before.paper-evidence.json', 20*1024*1024)
    manifest = dict(format='uulab.durable-paper-recovery', version=1,
        account_id=account_id, input_sha256=_hash(input_raw),
        before_sha256=_hash(before_raw), engine_sha256=_engine_fingerprint())
    _publish((_canonical(manifest)+'\n').encode(),directory/MANIFEST)


def _load(directory):
    manifest_raw, manifest = _read_private(directory/MANIFEST, 4096)
    _require(isinstance(manifest, dict) and set(manifest) == {
        'format','version','account_id','input_sha256','before_sha256','engine_sha256'}
        and manifest['format']=='uulab.durable-paper-recovery'
        and type(manifest['version']) is int and manifest['version']==1, 'invalid_manifest')
    input_raw, frozen = _read_private(directory/'input.json')
    _require(_hash(input_raw)==manifest['input_sha256'], 'input_changed')
    _require(_engine_fingerprint()==manifest['engine_sha256'], 'engine_changed')
    before_raw, before = _read_private(directory/'before.paper-evidence.json',20*1024*1024)
    _require(_hash(before_raw)==manifest['before_sha256'], 'initial_evidence_changed')
    from trading_core.paper_evidence import validate_evidence
    validate_evidence(before_raw)
    _require(set(frozen)=={'mode','config','candles'} and frozen['mode']=='paper', 'invalid_input')
    config=PaperRunConfig(**frozen['config'])
    _require(config.identity()==frozen['config'], 'configuration_changed')
    candles=validate_bars(frozen['candles'],config)
    seed=before['payload']
    _require(seed['account_id']==manifest['account_id'] and not seed['orders']
             and not seed['book']['trades'] and seed.get('control',{}).get('mode')=='halted'
             and seed['control']['generation']==0 and seed.get('policy') is None,
             'invalid_initial_account')
    expected_initial=dict(initial_balance=str(config.initial_balance),fee_pct=str(config.fee_pct),
        slippage_pct=str(config.slippage_pct),quote_currency=config.symbol.split('/')[1])
    _require(seed['initial']==expected_initial, 'configuration_changed')
    return manifest, input_raw, config, candles, before


def _check_account(data, manifest, before, expected, reconcile_saved_fills, candles):
    _require(data['account_id']==manifest['account_id'], 'account_changed')
    _require(data['initial']==before['payload']['initial'] and data.get('policy') is None
             and 'control' in data, 'configuration_changed')
    symbol = expected['assumptions']['symbol']
    marks = data['book']['tickers']
    _require(set(marks) <= {symbol} and data['book']['next_order_id'] == 1,
             'unexpected_book_state')
    if symbol in marks:
        mark = marks[symbol]
        allowed = {c.timestamp: (c.open, c.close) for c in candles}
        _require(mark['timestamp'] in allowed
                 and Decimal(mark['last']) in allowed[mark['timestamp']], 'mark_changed')
    trades=data['book']['trades']; orders=data['orders']
    _require(trades==expected['trades'][:len(trades)], 'fill_history_changed')
    # A no-fill UNKNOWN may have been accepted elsewhere. Never infer rejection.
    _require(set(orders)=={t['clientOrderId'] for t in trades}, 'missing_fill')
    _require(all(o['order_type']=='market' and o['price'] is None
        and o['strategy_id'] is None and not o['cancel_requested'] for o in orders.values()),
        'order_history_changed')
    unresolved=[key for key,o in orders.items() if o['status']!='filled']
    _require(not unresolved or reconcile_saved_fills, 'reconciliation_required')
    return len(trades), unresolved


class _ForwardSuffix:
    """Shadow manager for old decisions; only new requests reach the durable owner."""
    def __init__(self, shadow, session, expected, count, ensure):
        self.shadow=shadow
        self.owner=session
        self.manager=OrderManager(shadow)
        self.expected=expected
        self.saved=count
        self.index=0
        self.ensure=ensure

    async def get_open_orders(self):
        return await self.owner.manager.get_open_orders()

    async def place_order(self,*args,**kwargs):
        order=await self.manager.place_order(*args,**kwargs)
        state=self.shadow.snapshot()
        _require(len(state['trades'])==self.index+1
            and state['trades'][-1]==self.expected[self.index], 'replay_diverged')
        if self.index>=self.saved:
            self.ensure()
            ticker=state['tickers'][order.symbol]
            self.owner.exchange.set_price(order.symbol,ticker['last'],timestamp=ticker['timestamp'])
            result=await self.owner.manager.place_order(*args,**kwargs)
            _require(result.status==OrderStatus.FILLED, 'reconciliation_required')
            actual=self.owner.exchange.snapshot()
            for field in ('trades','balances','total_fees','orders','requests'):
                _require(actual[field]==state[field], 'account_replay_mismatch')
        self.index+=1
        return order


async def resume_durable_replay(directory, *, reconcile_saved_fills=False):
    """Explicit historical-only replay. No retries, automatic restarts or live adapters."""
    _require(type(reconcile_saved_fills) is bool, 'invalid_options')
    target=Path(directory).absolute()
    _private_directory(target)
    with recovery_lock(target) as ensure:
        _require(not os.path.lexists(target/'result.json'), 'already_complete')
        manifest,input_raw,config,candles,before=_load(target)
        with localcontext(PAPER_CALCULATION_CONTEXT):
            # No account writes during replay/prefix validation.
            expected=await PaperRunner(config).run(candles)
            ensure()
            observer=PaperSessionObserver(target/'account.sqlite')
            captured=observer.read_evidence()
            _check_account(captured['payload'],manifest,before,expected,reconcile_saved_fills,candles)
            _require(_load(target)[0]==manifest, 'manifest_changed')
            ensure()
            async with PaperTradingSession(target/'account.sqlite') as session:
                try:
                    # The acquired owner is authoritative; recheck after acquisition.
                    data,_,_=session._store.view()
                    count,unknown=_check_account(data,manifest,before,expected,reconcile_saved_fills,candles)
                    await _halt(session)
                    for key in unknown:
                        await reconcile_order(session.manager,key,allow_client_lookup=True)
                    _require(not await session.manager.get_open_orders(), 'reconciliation_required')
                    _require(_load(target)[0]==manifest, 'manifest_changed')
                    await session.set_operating_mode('active',
                        expected_generation=session.control_status['generation'],reason='operator')
                    shadow=PaperExchange(**data['initial'])
                    forward=_ForwardSuffix(shadow,session,expected['trades'],count,ensure)
                    replay=await PaperRunner(config)._run(candles,
                        _session=SimpleNamespace(exchange=shadow,manager=forward))
                    _require(replay==expected and forward.index==len(expected['trades']), 'replay_diverged')
                    final_mark=shadow.snapshot()['tickers'][config.symbol]
                    session.exchange.set_price(config.symbol,final_mark['last'],timestamp=final_mark['timestamp'])
                    actual=session.exchange.snapshot()
                    for field in ('trades','balances','total_fees','orders','requests','tickers'):
                        _require(actual[field]==shadow.snapshot()[field], 'account_replay_mismatch')
                    _require(_load(target)[0]==manifest, 'manifest_changed')
                finally:
                    await _halt(session)
                # A previous final export may exist after publication failure; preserve it.
                name='resume-'+uuid.uuid4().hex+'.paper-evidence.json'
                export_evidence(observer,target/name)
                final=verify_evidence(target/name)
                comparison=compare_evidence(target/'before.paper-evidence.json',target/name)
                _require(final['control']['mode']=='halted' and not final['orders']['unresolved']
                    and final['account']['cash']==replay['cash']
                    and final['account']['total_fees']==replay['total_fees']
                    and final['account']['marked_equity']==replay['equity'], 'final_evidence_mismatch')
                result=dict(schema_version=1,source='durable_historical_paper_run',mode='paper',
                    live_enabled=False,status='completed_halted',account_id=session.account_id,
                    input_sha256=_hash(input_raw),replay=replay,comparison=comparison,
                    final_evidence=name,recovery=dict(method='deterministic_prefix_replay',
                        existing_fills_skipped=count,saved_fills_reconciled=len(unknown)),
                    boundaries=dict(prices='historical_inputs',process_liveness='not_asserted',
                        resume_supported=True,risk_controls='original_paper_runner',
                        quote_freshness='not_applicable_to_historical_replay'))
            ensure()
            _publish((_canonical(result)+'\n').encode(),target/'result.json')
            return result
