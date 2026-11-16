"""Validate imported history or run it offline in a NEW source-bound paper bundle."""
import argparse
import asyncio
from contextlib import redirect_stdout
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('market_paper_arguments_invalid')


def main(argv=None):
    interrupted = None
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True)
        inspect = commands.add_parser('inspect', help='Validate a saved imported fixture; no writes')
        inspect.add_argument('fixture')
        run = commands.add_parser('run', help='Freeze source metadata and create a new resumable run')
        run.add_argument('fixture')
        run.add_argument('--directory', required=True, help='NEW bundle directory; actual account lives in run/')
        run.add_argument('--strategy', choices=('ma_cross','rsi'), default='ma_cross')
        for name, default in [('initial-balance','1000000'), ('position-size-pct','20'),
                ('fee-pct','0.1'), ('slippage-pct','0.05'),
                ('max-daily-loss-pct','3'), ('max-drawdown-pct','10')]:
            run.add_argument('--'+name, default=default)
        for name, default in [('fast-period',3), ('slow-period',5), ('rsi-period',14)]:
            run.add_argument('--'+name, type=int, default=default)
        args = parser.parse_args(argv)
        with redirect_stdout(sys.stderr):
            from trading_bot.market_paper_run import inspect_market_input, run_market_replay
            if args.command == 'inspect':
                report = inspect_market_input(args.fixture)
            else:
                from trading_bot.paper_process import run_paper_operation, PaperProcessInterrupted
                interrupted = PaperProcessInterrupted
                # Build configuration inside the same validated read as execution;
                # the orchestrator, not a second file read, supplies symbol/timeframe.
                options = {key: getattr(args,key) for key in ('strategy','fast_period','slow_period','rsi_period')}
                for key in ('initial_balance','position_size_pct','fee_pct','slippage_pct',
                            'max_daily_loss_pct','max_drawdown_pct'):
                    options[key] = Decimal(getattr(args,key))
                report = run_paper_operation(lambda: run_market_replay(
                    args.fixture, directory=args.directory, config_options=options))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps({'error':'market_paper_interrupted_check_bundle'}))
        return 130
    except Exception as error:
        if interrupted is not None and isinstance(error,interrupted):
            print(json.dumps({'error':'market_paper_interrupted_check_bundle', 'signal':error.signal_name}))
            return error.exit_code
        codes = {'market_paper_input_invalid':2, 'market_paper_arguments_invalid':2,
                 'market_paper_config_mismatch':2, 'market_paper_source_changed':2,
                 'market_paper_directory_changed':2, 'market_paper_execution_input_changed':2,
                 'durable_replay_stop_unconfirmed':3, 'paper_evidence_published_check_required':3}
        code = str(error) if str(error) in codes else 'market_paper_failed_check_bundle'
        print(json.dumps({'error':code}))
        return codes.get(code,2)
    print(json.dumps(report,ensure_ascii=False,allow_nan=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
