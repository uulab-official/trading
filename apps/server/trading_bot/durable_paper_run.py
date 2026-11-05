"""Bounded original-engine replay into a new, durable local paper account.

No existing account, resume, credentials, provider, automatic reconciliation or
live mode. In-memory/checkpoint replay remains the original PaperRunner path.
"""
from datetime import datetime, timezone
from decimal import localcontext
import hashlib
from pathlib import Path

from trading_core.order_state_store import _canonical
from trading_core.paper_evidence import _publish, export_evidence, verify_evidence
from trading_core.paper_evidence_compare import compare_evidence
from trading_core.paper_exchange import PAPER_CALCULATION_CONTEXT
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver
from trading_data import Candle, CandleBuffer
from trading_bot.paper_runner import PaperRunConfig, PaperRunner

MAX_BARS = 5000


class DurableReplayStopUnconfirmed(RuntimeError):
    """Persisted halt could not be confirmed; retain the directory for inspection."""


def validate_bars(fixture, config):
    """Freeze all input before creating any output; reject rather than sort/fill gaps."""
    if not isinstance(config, PaperRunConfig) or not isinstance(fixture, (list, tuple)):
        raise ValueError('invalid_durable_replay_input')
    if not 1 <= len(fixture) <= MAX_BARS:
        raise ValueError('invalid_durable_replay_size')
    duration = CandleBuffer(timeframe=config.timeframe).duration_ms
    candles = []
    for row in fixture:
        candle = row if isinstance(row, Candle) else Candle(*row)
        datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc)
        if candles and candle.timestamp < candles[-1].timestamp + duration:
            raise ValueError('invalid_durable_replay_chronology')
        candles.append(candle)
    return tuple(candles)


async def _halt(session):
    try:
        await session.set_operating_mode('halted',
            expected_generation=session.control_status['generation'], reason='maintenance')
    except BaseException:
        # Do not turn an attempted halt into a successful stopped result.
        raise DurableReplayStopUnconfirmed('durable_replay_stop_unconfirmed') from None


async def run_durable_replay(fixture, *, config=None, directory):
    """Explicit NEW-run operation. Failures preserve artifacts; never auto-resume.

    A catchable exit attempts halt. SIGKILL, power loss, repeated cancellation or
    storage failures cannot guarantee that finalizer; saved mode is not liveness.
    Completion is published only after final halt and independent evidence checks.
    """
    config = config or PaperRunConfig()
    candles = validate_bars(fixture, config)
    frozen = dict(mode='paper', config=config.identity(), candles=[
        [c.timestamp, *[str(getattr(c, k)) for k in ('open','high','low','close','volume')]]
        for c in candles])
    raw = (_canonical(frozen) + '\n').encode('utf-8')
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('durable_replay_input_too_large')
    target = Path(directory).absolute()
    target = target.parent.resolve(strict=True) / target.name
    target.mkdir(mode=0o700)  # Exclusive ownership; no existing/partial run reuse.
    _publish(raw, target / 'input.json')
    database = target / 'account.sqlite'
    quote = config.symbol.split('/')[1]
    with localcontext(PAPER_CALCULATION_CONTEXT):
        async with PaperTradingSession(database, create=True,
                initial_balance=config.initial_balance, fee_pct=config.fee_pct,
                slippage_pct=config.slippage_pct, quote_currency=quote,
                enable_control=True) as session:
            observer = PaperSessionObserver(database)
            export_evidence(observer, target / 'before.paper-evidence.json')
            try:
                await session.set_operating_mode('active', expected_generation=0, reason='operator')
                replay = await PaperRunner(config)._run(candles, _session=session)
            finally:
                await _halt(session)
            export_evidence(observer, target / 'after.paper-evidence.json')
            final = verify_evidence(target / 'after.paper-evidence.json')
            comparison = compare_evidence(target / 'before.paper-evidence.json',
                                          target / 'after.paper-evidence.json')
            snapshot = session.exchange.snapshot()
            if (final['control']['mode'] != 'halted' or final['orders']['unresolved']
                    or replay['trades'] != snapshot['trades']
                    or replay['cash'] != final['account']['cash']
                    or replay['total_fees'] != final['account']['total_fees']
                    or replay['equity'] != final['account']['marked_equity']
                    or len(replay['trades']) != comparison['new_fills']['count']):
                raise ValueError('durable_replay_result_mismatch')
            result = dict(schema_version=1, source='durable_historical_paper_run',
                mode='paper', live_enabled=False, status='completed_halted',
                account_id=session.account_id, input_sha256=hashlib.sha256(raw).hexdigest(),
                replay=replay, comparison=comparison,
                boundaries=dict(prices='historical_inputs', process_liveness='not_asserted',
                    resume_supported=False, risk_controls='original_paper_runner',
                    quote_freshness='not_applicable_to_historical_replay'))
        # The owner is closed before publishing completion. No live activation.
        _publish((_canonical(result) + '\n').encode('utf-8'), target / 'result.json')
    return result
