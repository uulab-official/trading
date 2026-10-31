"""Export existing offline PaperRunner results to a read-only dashboard report.

No new trading engine, network client or live mode. POSIX local filesystems only,
matching the existing runner checkpoint locking contract.
"""
import argparse
import asyncio
from decimal import Decimal, DecimalException
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in [ROOT/'packages/core', ROOT/'packages/data', ROOT/'packages/strategy', ROOT/'apps/server']:
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
from trading_bot.replay_report import build_report, read_report, write_report

MAX_FIXTURE_BYTES = 32 * 1024 * 1024


def validate_paths(fixture, checkpoint, report):
    """Reject aliasing before invoking a runner that could write a checkpoint."""
    original_report = Path(report)
    if original_report.is_symlink():
        raise ValueError('report symlinks are not supported')
    fixture, checkpoint, report = [Path(path).resolve() for path in (fixture, checkpoint, report)]
    paths = [fixture, checkpoint, report,
             checkpoint.with_name('.'+checkpoint.name+'.lock'),
             report.with_name('.'+report.name+'.lock')]
    if len(set(paths)) != len(paths):
        raise ValueError('input, checkpoint, report and locks must be distinct')
    for index, path in enumerate(paths):
        for other in paths[:index]:
            if path.exists() and other.exists() and os.path.samefile(path, other):
                raise ValueError('aliased input/output files')
    if not fixture.is_file():
        raise ValueError('local fixture file required')
    if report.exists():
        read_report(report)  # Never overwrite arbitrary files or broken reports.
    return fixture, checkpoint, report


def export_replay(fixture, checkpoint, report, **options):
    fixture, checkpoint, report = validate_paths(fixture, checkpoint, report)
    # Delayed import keeps path validation available without engine dependencies.
    from trading_bot.paper_checkpoint import locked_checkpoint
    from trading_bot.paper_runner import PaperRunner, PaperRunConfig
    with locked_checkpoint(report):
        # Repeat preflight under the report lock; a competing cooperative exporter
        # cannot change the destination between the check and publication.
        validate_paths(fixture, checkpoint, report)
        with fixture.open('rb') as stream:
            raw = stream.read(MAX_FIXTURE_BYTES + 1)
        if len(raw) > MAX_FIXTURE_BYTES:
            raise ValueError('fixture exceeds the export size limit')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('mode', 'paper') != 'paper':
            raise ValueError('paper fixture required')
        candles = data.get('candles')
        if (not isinstance(candles, list) or not 1 <= len(candles) <= 10000
                or any(not isinstance(row, list) or len(row) != 6 for row in candles)):
            raise ValueError('one to 10000 six-column OHLCV rows required')
        config = PaperRunConfig(symbol=data.get('symbol', 'BTC/USDT'),
                               timeframe=data.get('timeframe', '1h'), **options)
        digest = hashlib.sha256(raw).hexdigest()
        if report.exists():
            old = read_report(report)
            if old['fixture_sha256'] != digest or old['configuration'] != config.identity():
                raise ValueError('use a new report filename for another experiment')
        # The original engine owns fills, cost application, risk state and
        # checkpoint validation. The dashboard never imports unvalidated checkpoints.
        result = asyncio.run(PaperRunner(config).run(candles, checkpoint_path=checkpoint))
        projected = build_report(result, fixture_sha256=digest, first_bar_ms=candles[0][0],
                                 last_bar_ms=candles[-1][0], mark_price=str(candles[-1][4]))
        return write_report(report, projected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--strategy', choices=('ma_cross', 'rsi'), default='ma_cross')
    for name, default in [('initial-balance','10000'), ('position-size-pct','20'),
                          ('fee-pct','0.1'), ('slippage-pct','0.05'),
                          ('max-daily-loss-pct','3'), ('max-drawdown-pct','10')]:
        parser.add_argument('--'+name, type=Decimal, default=Decimal(default))
    for name, default in [('fast-period',3), ('slow-period',5), ('rsi-period',14)]:
        parser.add_argument('--'+name, type=int, default=default)
    args = vars(parser.parse_args(argv))
    report = export_replay(args.pop('fixture'), args.pop('checkpoint'), args.pop('report'), **args)
    print(json.dumps({'mode':'paper', 'run_id':report['run_id'], 'fill_count':report['fill_count'],
                      'status':'report_available', 'source':'local_fixture'}, allow_nan=False))
    return 0


def cli(argv=None):
    from trading_bot.paper_checkpoint import CheckpointInUseError
    try:
        return main(argv)
    except CheckpointInUseError:
        print('Another exporter or replay owns a required lock; preserve lock files and retry.', file=sys.stderr)
        return 3
    except (ValueError, DecimalException):
        print('Invalid replay input, configuration or report destination. Preserve the checkpoint; use distinct paths and a new report filename for another experiment.', file=sys.stderr)
        return 2
    except OSError:
        print('Local file access failed. Preserve the checkpoint and any previous report; verify permissions and retry.', file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(cli())
