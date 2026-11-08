"""Resume one explicitly opted-in historical paper run; never a live account."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
for component in ('packages/core','packages/data','packages/strategy','apps/server'):
    sys.path.insert(0,str(ROOT/component))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',help='Existing incomplete --resumable run directory')
    parser.add_argument('--reconcile-saved-fills',action='store_true',
        help='Explicit read-back of already-saved simulated fills, never resubmit unknown intents')
    args=parser.parse_args(argv)
    try:
        from trading_bot.durable_paper_resume import resume_durable_replay
        result=asyncio.run(resume_durable_replay(args.directory,
            reconcile_saved_fills=args.reconcile_saved_fills))
    except (KeyboardInterrupt,asyncio.CancelledError):
        print(json.dumps({'error':'durable_paper_resume_interrupted_check_directory'}))
        return 130
    except Exception as error:
        fixed={
            'durable_replay_stop_unconfirmed':3,
            'durable_resume_reconciliation_required':4,
            'durable_resume_missing_fill':4,
            'durable_resume_already_complete':2,
        }
        code=str(error) if str(error) in fixed else 'durable_paper_resume_failed_check_directory'
        print(json.dumps({'error':code}))
        return fixed.get(code,2)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
