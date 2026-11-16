"""Source-preserving offline handoff from imported history to durable paper runs.

No acquisition, provider orders or alternative fill model. Metadata/checksums
record supplied bytes; they are not authenticated exchange evidence.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import stat

from trading_core.order_state_store import _canonical
from trading_core.paper_evidence import _publish
from trading_data.upbit_history import (CLOSE_MARGIN_MS, decode_json,
    parse_end, validate_market_fixture, _milliseconds)
from trading_bot.durable_paper_run import run_durable_replay, validate_bars
from trading_bot.paper_runner import PaperRunConfig

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_BARS = 5000
_SOURCE_FIELDS = {'mode', 'symbol', 'timeframe', 'candles', 'data_provenance'}


class MarketPaperInputInvalid(ValueError):
    """Unusable local market input; no new account may be created."""


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _signature(info):
    # atime may legitimately change because of this read.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)


def _read_regular(path, limit=MAX_INPUT_BYTES):
    """One captured byte stream, no FIFO wait or symlink following."""
    target = Path(path).absolute()
    before = target.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= limit):
        raise ValueError('invalid_regular_input')
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if _signature(os.fstat(stream.fileno())) != _signature(before):
            raise ValueError('input_changed')
        raw = stream.read(limit + 1)
        if (_signature(os.fstat(stream.fileno())) != _signature(before)
                or len(raw) != before.st_size or len(raw) > limit):
            raise ValueError('input_changed')
    if _signature(target.lstat()) != _signature(before):
        raise ValueError('input_changed')
    return raw


def _capture(path, clock):
    try:
        raw = _read_regular(path)
        data = decode_json(raw)
        if (not isinstance(data, dict) or set(data) != _SOURCE_FIELDS
                or not isinstance(data['candles'], list)
                or not 1 <= len(data['candles']) <= MAX_BARS):
            raise ValueError('unsupported_market_input')
        provenance = validate_market_fixture(data)
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        now_ms = _milliseconds(now)  # Also rejects naive or non-datetime clocks.
        retrieved = parse_end(provenance['retrieved_at'])
        if (retrieved > now or provenance['end_exclusive_ms'] > now_ms - CLOSE_MARGIN_MS):
            raise ValueError('future_market_history')
        report = dict(schema_version=1, source='market_paper_input', mode='paper',
            scope='historical_simulation_input', live_enabled=False, authenticated=False,
            source_sha256=_hash(raw), source_bytes=len(raw), symbol=data['symbol'],
            timeframe=data['timeframe'], bar_count=len(data['candles']),
            first_open_ms=data['candles'][0][0], last_open_ms=data['candles'][-1][0],
            end_exclusive_ms=provenance['end_exclusive_ms'],
            historical_age_ms=now_ms-provenance['end_exclusive_ms'],
            inspected_at=now.astimezone(timezone.utc).isoformat(), provenance=provenance,
            warnings=['source_metadata_not_authenticated', 'historical_prices_not_live',
                      'simulation_costs_not_broker_quotes'])
        return raw, data, report
    except Exception:
        raise MarketPaperInputInvalid('market_paper_input_invalid') from None


def inspect_market_input(path, *, clock=None):
    """Validate imported closed history without a durable account or any writes."""
    return _capture(path, clock)[2]


def _directory_id(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise ValueError('market_paper_directory_changed')
    return info.st_dev, info.st_ino


def _check_sources(target, identity, raw, manifest_raw):
    paths = (target/'source.json', target/'source-manifest.json')
    signatures = []
    for path in paths:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError('market_paper_source_changed')
        signatures.append(_signature(info))
    if (_directory_id(target) != identity
            or _read_regular(paths[0]) != raw
            or _read_regular(paths[1]) != manifest_raw
            or [_signature(path.lstat()) for path in paths] != signatures
            or _directory_id(target) != identity):
        raise ValueError('market_paper_source_changed')


async def run_market_replay(path, *, directory, config=None, clock=None, config_options=None):
    """Validate once, freeze provenance, run the ORIGINAL engine in a NEW child.

    The original source need not remain at its pathname after capture. Only those
    captured bytes are used. Failed bundles remain for inspection; rerun is not
    recovery. Explicit existing recovery commands target the nested run/ folder.
    """
    raw, data, source = _capture(path, clock)
    if config_options is not None:
        if (config is not None or not isinstance(config_options, dict)
                or set(config_options) - {'strategy','fast_period','slow_period','rsi_period',
                    'initial_balance','position_size_pct','fee_pct','slippage_pct',
                    'max_daily_loss_pct','max_drawdown_pct'}):
            raise ValueError('market_paper_config_mismatch')
        config = PaperRunConfig(symbol=data['symbol'], timeframe=data['timeframe'], **config_options)
    if config is None:
        config = PaperRunConfig(symbol=data['symbol'], timeframe=data['timeframe'],
                                initial_balance=Decimal('1000000'))
    if (not isinstance(config, PaperRunConfig) or config.symbol != data['symbol']
            or config.timeframe != data['timeframe']):
        raise ValueError('market_paper_config_mismatch')
    config = PaperRunConfig(**config.identity())
    candles = validate_bars(data['candles'], config)
    frozen = dict(mode='paper', config=config.identity(), candles=[
        [c.timestamp, *[str(getattr(c, key)) for key in ('open','high','low','close','volume')]]
        for c in candles])
    execution_raw = (_canonical(frozen)+'\n').encode('utf-8')
    if len(execution_raw) > MAX_INPUT_BYTES:
        raise MarketPaperInputInvalid('market_paper_input_invalid')
    manifest = dict(format='uulab.market-paper-source', version=1, mode='paper',
        live_enabled=False, authenticated=False, source_file='source.json',
        run_directory='run', source_sha256=source['source_sha256'],
        execution_input_sha256=_hash(execution_raw), provenance=source['provenance'],
        config=config.identity())
    manifest_raw = (_canonical(manifest)+'\n').encode('utf-8')
    target = Path(directory).absolute()
    target = target.parent.resolve(strict=True) / target.name
    target.mkdir(mode=0o700)  # No overwrite or reuse of a partial/existing bundle.
    identity = _directory_id(target)
    _publish(raw, target/'source.json')
    if _directory_id(target) != identity:
        raise ValueError('market_paper_directory_changed')
    _publish(manifest_raw, target/'source-manifest.json')
    _check_sources(target, identity, raw, manifest_raw)
    result = await run_durable_replay(candles, config=config, directory=target/'run', resumable=True)
    child_identity = _directory_id(target/'run')
    if (_read_regular(target/'run/input.json') != execution_raw
            or result['input_sha256'] != manifest['execution_input_sha256']):
        raise ValueError('market_paper_execution_input_changed')
    _check_sources(target, identity, raw, manifest_raw)
    if _directory_id(target/'run') != child_identity:
        raise ValueError('market_paper_directory_changed')
    return dict(schema_version=1, source=source, mode='paper', live_enabled=False,
        status=result['status'], run_directory='run', source_manifest_sha256=_hash(manifest_raw),
        run=result)
