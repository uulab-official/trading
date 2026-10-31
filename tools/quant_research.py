"""Run bounded local walk-forward research. No credentials, orders or network."""
import argparse
from decimal import DecimalException
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'packages/core', ROOT / 'packages/data', ROOT / 'packages/strategy', ROOT / 'apps/server', ROOT / 'tools'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from trading_bot.walk_forward_research import run_research
from trading_bot.research_report import read_report, write_report, unique_object
MAX_INPUT = 32 * 1024 * 1024

def load_input(raw):
    from paper_replay_dashboard import load_fixture
    return load_fixture(raw)

def validate_paths(source, output):
    source, output = (Path(source), Path(output))
    if not source.is_file() or output.is_symlink():
        raise ValueError('invalid local path')
    if source.resolve() == output.resolve() or (output.exists() and os.path.samefile(source, output)):
        raise ValueError('input and output must differ')
    if output.exists():
        read_report(output)
    return (source, output)

def export_research(source, output, *, candidates=None, settings=None, robustness=False):
    source, output = validate_paths(source, output)
    with source.open('rb') as stream:
        raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError('input too large')
    data = load_input(raw)
    result = run_research(data, hashlib.sha256(raw).hexdigest(), candidates=candidates, settings=settings, **({"robustness": True} if robustness else {}))
    return write_report(output, result)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidates', type=Path, help='Optional JSON array, maximum eight explicit strategy candidates')
    for name, default in [('train-size', 120), ('test-size', 60), ('gap', 1), ('warmup', 100), ('min-training-fills', 2)]:
        parser.add_argument('--' + name, type=int, default=default)
    parser.add_argument('--anchored', action='store_true')
    parser.add_argument('--robustness', action='store_true',
                        help='Freeze base training selection; compare cash/buy-hold and 1x/2x/3x costs')
    for name, default in [('initial-balance', '10000'), ('position-size-pct', '20'), ('fee-pct', '0.1'), ('slippage-pct', '0.05'), ('max-daily-loss-pct', '3'), ('max-drawdown-pct', '10')]:
        parser.add_argument('--' + name, default=default)
    args = vars(parser.parse_args(argv))
    source = args.pop('fixture')
    output = args.pop('output')
    path = args.pop('candidates')
    robust = args.pop('robustness')
    candidates = None
    if path is not None:
        if path.resolve() == output.resolve() or (output.exists() and path.exists() and os.path.samefile(path, output)):
            raise ValueError('candidate input aliases output')
        if not path.is_file():
            raise ValueError('regular candidate file required')
        with path.open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('candidate file too large')
        candidates = json.loads(raw, object_pairs_hook=unique_object)
    report = export_research(source, output, candidates=candidates, settings=args, robustness=robust)
    print(json.dumps({'mode': 'paper', 'status': 'research_available', 'run_id': report['run_id'], 'folds': report['summary']['total_folds']}, allow_nan=False))
    return 0

def cli(argv=None):
    try:
        return main(argv)
    except (ValueError, DecimalException, UnicodeError, RecursionError):
        print('Invalid research inputs or existing report. Check history, candidate periods and window sizes; use a new output for a different experiment.', file=sys.stderr)
        return 2
    except OSError:
        print('Local file access failed; existing files were not overwritten.', file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
if __name__ == '__main__':
    raise SystemExit(cli())
