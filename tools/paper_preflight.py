"""Inspect historical paper recovery conditions without resuming or modifying it."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

# A read-only command must not create optional package bytecode on import.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
for component in ('packages/core', 'packages/data', 'packages/strategy', 'apps/server'):
    sys.path.insert(0, str(ROOT / component))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('directory', help='Existing --resumable historical run; never modified')
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print(json.dumps({'status': 'unavailable', 'reason': 'invalid_arguments',
                          'read_only': True, 'resume_authorized': False}))
        return 2
    try:
        # Existing optional-module diagnostics cannot pollute machine stdout.
        with redirect_stdout(sys.stderr):
            from trading_bot.durable_paper_preflight import inspect_durable_replay
            report = asyncio.run(inspect_durable_replay(args.directory))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps({'status': 'unavailable', 'reason': 'inspection_interrupted',
                          'read_only': True, 'resume_authorized': False}))
        return 130
    except Exception:
        print(json.dumps({'status': 'unavailable', 'reason': 'inspection_failed',
                          'read_only': True, 'resume_authorized': False}))
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return {'compatible_snapshot': 0, 'reconciliation_required': 4}.get(report['status'], 2)


if __name__ == '__main__':
    raise SystemExit(main())
