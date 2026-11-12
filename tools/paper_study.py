"""Run a fixed historical MA/RSI study, or inspect it without changing accounts."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for part in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT/part))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('market_study_arguments_invalid')


def _html_output(raw, study_directory):
    """Validate an explicit new private output, never an inspected source path."""
    try:
        target = Path(raw).absolute()
        parent = target.parent.resolve(strict=True)
        info = parent.stat()
        target = parent / target.name
        source = Path(study_directory).resolve()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or os.path.lexists(target)
                or target.is_relative_to(source)):
            raise ValueError('invalid_output')
        return target, (parent, info.st_dev, info.st_ino)
    except Exception:
        raise ValueError('market_study_report_output_invalid') from None


async def _inspect(directory, output):
    from trading_bot.market_paper_study import inspect_market_study
    report = await inspect_market_study(directory)
    if output is not None:
        from trading_bot.market_study_html import render_study_report
        from trading_core.paper_evidence import _publish
        document = render_study_report(report).encode('utf-8')
        # Inspection may take time: reject a newly created target or source alias.
        target, parent_binding = output
        target, current_parent = _html_output(target, directory)
        if current_parent != parent_binding:
            raise ValueError('market_study_report_output_invalid')
        # Bind the publisher's actual directory to the one checked above. A
        # pathname-only recheck leaves a symlink-swap gap before publication.
        _publish(document, target, expected_parent=parent_binding)
    return report


def main(argv=None):
    interruption = None
    try:
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
        run = commands.add_parser('run', help='Create a NEW study from one imported local fixture')
        run.add_argument('fixture'); run.add_argument('--plan', required=True)
        run.add_argument('--directory', required=True)
        check = commands.add_parser('check', help='Read-only verification of every planned candidate')
        check.add_argument('directory')
        check.add_argument('--html', help='NEW private HTML report outside the inspected study')
        args = parser.parse_args(argv)
        from trading_bot.market_paper_study import run_market_study, MAX_PLAN_BYTES
        from trading_bot.market_paper_run import _read_regular
        from trading_bot.paper_process import run_paper_operation, PaperProcessInterrupted
        from trading_data.upbit_history import decode_json
        interruption = PaperProcessInterrupted
        if args.command == 'run':
            plan = decode_json(_read_regular(args.plan, MAX_PLAN_BYTES), decimal_numbers=False)
            result = run_paper_operation(lambda: run_market_study(
                args.fixture, plan, directory=args.directory))
        else:
            output = _html_output(args.html, args.directory) if args.html is not None else None
            with redirect_stdout(sys.stderr):
                result = run_paper_operation(lambda: _inspect(args.directory, output))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps({'error': 'market_study_interrupted_inspect_directory'}))
        return 130
    except Exception as error:
        if interruption is not None and isinstance(error, interruption):
            print(json.dumps({'error': 'market_study_interrupted_inspect_directory',
                              'signal': error.signal_name, 'account_state': 'inspect_saved_record'}))
            return error.exit_code
        codes = {'paper_evidence_published_check_required': 3, 'durable_replay_stop_unconfirmed': 3,
                 'market_study_arguments_invalid': 2, 'market_study_invalid_plan': 2,
                 'market_study_report_output_invalid': 2,
                 'market_study_changed': 2, 'market_study_candidate_failed': 2}
        code = str(error) if str(error) in codes else 'market_study_failed_inspect_directory'
        print(json.dumps({'error': code}))
        return codes.get(code, 2)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    if result['status'] == 'verified_study':
        return 0
    return 4 if result['status'] in {'incomplete_study', 'complete_without_receipt'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
