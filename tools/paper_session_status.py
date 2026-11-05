"""Read an existing private local paper session without changing it."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='Existing local paper-session SQLite file')
    args = parser.parse_args()
    try:
        from trading_core.paper_session_observer import PaperSessionObserver
        report = PaperSessionObserver(args.database).read()
    except Exception:
        print(json.dumps({'error': 'paper_session_unavailable'}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
