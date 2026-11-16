"""Create an explicitly SYNTHETIC paper lifecycle journal; never overwrite a file.

This is a UI/recovery exercise, not a strategy, backtest or exchange simulator.
"""
import argparse
from decimal import Decimal as D
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', required=True, type=Path, help='New local SQLite destination')
    args = parser.parse_args(argv)
    path = args.journal.expanduser().absolute()
    try:
        # O_EXCL makes refusal atomic; never clear an existing real or demo journal.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        parser.error('Destination already exists; choose a new path. Existing data was not changed.')
    except OSError:
        parser.error('Cannot create the journal. Use an existing, writable local directory.')
    spec = importlib.util.spec_from_file_location('paper_demo_journal', ROOT / 'packages/core/order_journal.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.OrderJournal(str(path), source='synthetic_demo') as journal:
        for number, amount in enumerate(('100000', '80000', '60000', '40000', '10000', '20000'), 1):
            journal.reserve(f'demo-{number:03d}', {'symbol': 'SYNTHETIC', 'currency': 'KRW'}, D(amount), D('1000000'))
        journal.mark_submitting('demo-002')
        journal.confirm_accepted('demo-002', 'synthetic:002')
        journal.mark_submitting('demo-003')
        journal.mark_unknown('demo-003')
        journal.mark_submitting('demo-004')
        journal.confirm_accepted('demo-004', 'synthetic:004')
        journal.request_cancel('demo-004')
        journal.confirm_cancelled('demo-005')
        journal.mark_submitting('demo-006')
        journal.confirm_rejected('demo-006')
        print(json.dumps({'mode':'paper', 'source':'synthetic_demo', 'orders':6,
                          'reserved':str(journal.reserved_total()), 'currency':'KRW',
                          'needs_reconciliation':len(journal.pending_reconciliation())}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
