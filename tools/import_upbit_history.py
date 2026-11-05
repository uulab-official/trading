"""Download closed public Upbit KRW spot candles for a later OFFLINE replay.

This explicit command is the network boundary. It has no broker credentials,
account access, trading methods or arbitrary endpoint option.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/data'))
from trading_data.upbit_history import (
    MarketDataUnavailable, UNITS, UpbitHistoryClient, check_destination, parse_end, write_fixture,
)


def main(argv=None, *, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', required=True, help='KRW spot pair, e.g. KRW-BTC')
    parser.add_argument('--unit', type=int, choices=UNITS, default=60)
    parser.add_argument('--count', type=int, default=720, help='1–10000 closed consecutive bars')
    parser.add_argument('--end', type=str, help='Exclusive ISO 8601 time with explicit timezone')
    parser.add_argument('--output', type=Path, required=True, help='NEW local fixture filename')
    args = parser.parse_args(argv)
    check_destination(args.output)  # Refuse existing data before contacting the provider.
    end = parse_end(args.end) if args.end is not None else None
    fixture = (client or UpbitHistoryClient()).download(args.market, unit=args.unit, count=args.count, end=end)
    write_fixture(args.output, fixture)
    print(json.dumps({'mode': 'paper', 'live_enabled': False, 'status': 'fixture_published',
                      'bars': len(fixture['candles']), 'symbol': fixture['symbol'],
                      'data_provenance': fixture['data_provenance']}, allow_nan=False))
    return 0


def cli(argv=None):
    try:
        return main(argv)
    except (ValueError, FileExistsError):
        print('Invalid or incomplete market history, or output already exists. Use a new filename; no partial fixture was published.', file=sys.stderr)
        return 2
    except MarketDataUnavailable:
        print('Public data acquisition stopped. Respect provider cooldowns and retry later; no partial fixture was published.', file=sys.stderr)
        return 3
    except OSError:
        print('Local publication failed. Preserve existing files and check output permissions.', file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(cli())
