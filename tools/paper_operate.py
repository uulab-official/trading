"""Run original historical strategies into a NEW durable local paper account."""
import argparse
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))


def _read_input(path):
    # Bounded read of a regular local file: no FIFO, socket, remote fetch or symlink.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular_input_required')
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('input_too_large')
    from trading_core.order_state_store import _pairs
    data = json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    if (not isinstance(data, dict) or not {'symbol','timeframe','candles'} <= data.keys()
            or data.keys() - {'symbol','timeframe','candles','mode'}
            or data.get('mode','paper') != 'paper'):
        raise ValueError('invalid_fixture')
    return data


def _demo_input():
    prices = [100 + abs((i % 40) - 20) for i in range(100)]
    return dict(mode='paper', symbol='BTC/USDT', timeframe='1h', candles=[
        [1700000000000 + i * 3600000, str(p), str(p+2), str(p-2), str(p+1), '1']
        for i, p in enumerate(prices)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', nargs='?', help='Local JSON with symbol, timeframe and six-column candles')
    parser.add_argument('--demo', action='store_true', help='Use explicitly synthetic local candles')
    parser.add_argument('--directory', required=True, help='NEW private run directory; never reused')
    parser.add_argument('--strategy', choices=('ma_cross','rsi'), default='ma_cross')
    for name, default in [('initial-balance','10000'),('position-size-pct','20'),
                          ('fee-pct','0.1'),('slippage-pct','0.05'),
                          ('max-daily-loss-pct','3'),('max-drawdown-pct','10')]:
        parser.add_argument('--'+name, default=default)
    for name, default in [('fast-period',3),('slow-period',5),('rsi-period',14)]:
        parser.add_argument('--'+name, type=int, default=default)
    args = parser.parse_args(argv)
    try:
        from trading_bot.durable_paper_run import run_durable_replay
        from trading_bot.paper_runner import PaperRunConfig
        if bool(args.fixture) == args.demo:
            raise ValueError('choose_local_input_or_synthetic_demo')
        data = (_demo_input() if args.demo else _read_input(args.fixture))
        config = PaperRunConfig(symbol=data['symbol'], timeframe=data['timeframe'],
            strategy=args.strategy, initial_balance=Decimal(args.initial_balance),
            position_size_pct=Decimal(args.position_size_pct), fee_pct=Decimal(args.fee_pct),
            slippage_pct=Decimal(args.slippage_pct), max_daily_loss_pct=Decimal(args.max_daily_loss_pct),
            max_drawdown_pct=Decimal(args.max_drawdown_pct), fast_period=args.fast_period,
            slow_period=args.slow_period, rsi_period=args.rsi_period)
        result = asyncio.run(run_durable_replay(data['candles'],config=config,directory=args.directory))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps({'error':'durable_paper_run_interrupted_check_directory'}))
        return 130
    except Exception as error:
        # Do not leak source paths, identifiers, raw errors or account contents.
        stop_unknown = str(error) == 'durable_replay_stop_unconfirmed'
        print(json.dumps({'error':'durable_replay_stop_unconfirmed' if stop_unknown
                         else 'durable_paper_run_failed_check_directory'}))
        return 3 if stop_unknown else 2
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
