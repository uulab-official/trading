"""Explicit public closed-candle collection, observation and historical export."""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/data', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid capture arguments')


def _arguments(argv):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='Create a NEW capture; public closed candles only')
    run.add_argument('--directory', required=True)
    run.add_argument('--market', required=True)
    run.add_argument('--unit', type=int, default=1)
    run.add_argument('--history', type=int, default=120)
    run.add_argument('--retention', type=int, default=5000)
    run.add_argument('--poll-seconds', type=int, default=15)
    run.add_argument('--max-polls', type=int)
    resume = commands.add_parser('resume', help='Resume saved configuration after validating prior state')
    resume.add_argument('directory')
    resume.add_argument('--max-polls', type=int)
    check = commands.add_parser('check', help='Read saved data and freshness; no acquisition')
    check.add_argument('directory')
    export = commands.add_parser('export', help='Publish a NEW historical fixture outside the capture')
    export.add_argument('directory')
    export.add_argument('--output', required=True)
    export.add_argument('--count', type=int)
    return parser.parse_args(argv)


def _collect(args, module):
    received, previous = [], {}

    def on_signal(signum, frame):
        # No Event, I/O, close or lock acquisition in an asynchronous handler.
        if not received:
            received.append(signum)

    config = None if args.command == 'resume' else dict(market=args.market,
        unit_minutes=args.unit, bootstrap_bars=args.history,
        retention_bars=args.retention, poll_seconds=args.poll_seconds)
    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, on_signal)
        result = module.collect_capture(args.directory, config=config, max_polls=args.max_polls,
            stop=lambda: received[0] if received else 0)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return result['exit_code'], dict(operation='collect', mode='paper',
        read_only=False, live_enabled=False, **result)


def main(argv=None):
    try:
        args = _arguments(argv)
    except ValueError:
        print(json.dumps({'error': 'market_capture_arguments_invalid'}))
        return 2
    try:
        with redirect_stdout(sys.stderr):
            from trading_bot import market_capture as module
            if args.command in {'run', 'resume'}:
                code, report = _collect(args, module)
            elif args.command == 'check':
                report = module.capture_status(args.directory)
                code = 0 if report['status'] == 'current' else 1
            else:
                report = module.export_capture(args.directory, args.output, count=args.count)
                code = 0
    except ValueError:
        code, report = 2, {'error': 'market_capture_unavailable'}
    except OSError:
        code, report = 4, {'error': 'market_capture_storage_failed'}
    except KeyboardInterrupt:
        code, report = 130, {'error': 'market_capture_interrupted_check_capture'}
    except Exception:
        code, report = 4, {'error': 'market_capture_failed_check_capture'}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
