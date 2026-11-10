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
    interruption_type = None
    try:
        from trading_bot.paper_process import run_paper_operation, PaperProcessInterrupted
        interruption_type = PaperProcessInterrupted
        from trading_bot.durable_paper_resume import resume_durable_replay
        result=run_paper_operation(lambda: resume_durable_replay(args.directory,
            reconcile_saved_fills=args.reconcile_saved_fills))
    except (KeyboardInterrupt,asyncio.CancelledError):
        print(json.dumps({'error':'durable_paper_resume_interrupted_check_directory'}))
        return 130
    except Exception as error:
        if interruption_type is not None and isinstance(error, interruption_type):
            print(json.dumps({'error':'durable_paper_resume_interrupted_check_directory',
                'signal':error.signal_name, 'account_state':'inspect_saved_record'}))
            return error.exit_code
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
