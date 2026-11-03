"""Export or verify private, non-executable paper-account evidence (offline only)."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='Read one account revision into a NEW private file')
    export.add_argument('--database', required=True)
    export.add_argument('--output', required=True, help='New file in an existing private directory')
    export.add_argument('--revision', help='Optional displayed revision; requires --digest')
    export.add_argument('--digest', help='Optional displayed digest; requires --revision')
    verify = commands.add_parser('verify', help='Check saved evidence without the original DB')
    verify.add_argument('file')
    args = parser.parse_args(argv)
    try:
        from trading_core.paper_evidence import export_evidence, verify_evidence
        from trading_core.paper_session_observer import PaperSessionObserver
        if args.command == 'export':
            report = export_evidence(PaperSessionObserver(args.database), args.output,
                expected_revision=args.revision, expected_digest=args.digest)
        else:
            report = verify_evidence(args.file)
    except KeyboardInterrupt:
        print(json.dumps({'error': 'paper_evidence_interrupted_check_output'}), file=sys.stderr)
        return 130
    except Exception as error:
        # Only fixed public codes are emitted; no paths, account data or raw I/O.
        allowed = {'paper_evidence_published_check_required': 3,
                   'paper_session_changed': 4,
                   'paper_evidence_invalid': 2,
                   'paper_evidence_output_unavailable': 2,
                   'paper_session_unavailable': 2,
                   'invalid_order_page': 2}
        code = str(error) if str(error) in allowed else 'paper_evidence_operation_failed'
        print(json.dumps({'error': code}), file=sys.stderr)
        return allowed.get(code, 2)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
