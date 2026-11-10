"""Inspect explicit historical paper runs without changing their accounts."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def _failure(reason):
    return dict(status='unavailable', reason=reason, read_only=True,
                live_enabled=False, resume_authorized=False)


def _output_path(raw, targets):
    target = Path(raw).absolute()
    parent = target.parent.resolve(strict=True)
    resolved = parent / target.name
    if os.path.lexists(resolved) or any(
            resolved.is_relative_to(path.resolve()) for _, path in targets):
        raise ValueError('invalid_output')
    return resolved


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--run', action='append', required=True, metavar='LABEL=DIRECTORY',
                        help='1–8 explicit unique local runs; no directory scanning')
    parser.add_argument('--html', help='NEW private HTML output; outside all source runs')
    try:
        args = parser.parse_args(argv)
        entries = [entry.split('=', 1) for entry in args.run]
        with redirect_stdout(sys.stderr):
            from trading_bot.durable_paper_overview import validate_runs, inspect_runs, exit_code
            targets = validate_runs(entries)
    except ValueError:
        print(json.dumps(_failure('invalid_arguments')))
        return 2
    except Exception:
        print(json.dumps(_failure('overview_unavailable')))
        return 2
    try:
        output = _output_path(args.html, targets) if args.html else None
        with redirect_stdout(sys.stderr):
            report = asyncio.run(inspect_runs(targets))
            if output is not None:
                # Check again after all inspections; do not write into a source alias.
                output = _output_path(output, targets)
                from trading_bot.paper_overview_html import render_overview
                from trading_core.paper_evidence import _publish
                _publish(render_overview(report).encode('utf-8'), output)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps(_failure('overview_interrupted_check_output')))
        return 130
    except Exception as error:
        published = str(error) == 'paper_evidence_published_check_required'
        print(json.dumps(_failure('overview_published_check_required' if published
                                  else 'overview_failed')))
        return 3 if published else 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return exit_code(report)


if __name__ == '__main__':
    raise SystemExit(main())
