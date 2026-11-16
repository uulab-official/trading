"""Check completed opt-in historical paper results without changing any account."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
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
                completion_validated=False, resume_authorized=False, live_enabled=False)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('directory', help='Existing completed --resumable run; never modified')
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print(json.dumps(_failure('invalid_arguments')))
        return 2
    try:
        with redirect_stdout(sys.stderr):
            from trading_bot.durable_paper_audit import audit_durable_replay
            report = asyncio.run(audit_durable_replay(args.directory))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(json.dumps(_failure('audit_interrupted')))
        return 130
    except Exception:
        print(json.dumps(_failure('audit_failed')))
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if report['status'] == 'verified_completion' else 2


if __name__ == '__main__':
    raise SystemExit(main())
