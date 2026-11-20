"""Convert a saved Toss 1m market-data JSON response to paper-only candles.

No Toss tokens, HTTP access, account information or order endpoints are used.
Never pass a 1d response as one-minute data; this tool accepts --interval 1m only.
"""
import argparse
from datetime import datetime
from decimal import InvalidOperation
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data.toss_stock_candles import parse_toss_minute_candles


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="Saved response JSON from Toss GET /api/v1/candles")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--currency", choices=("KRW", "USD"), required=True)
    parser.add_argument("--interval", choices=("1m",), required=True)
    parser.add_argument("--observed-at", required=True,
                        help="Actual capture timestamp in ISO8601 including timezone")
    parser.add_argument("--safety-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output must differ")
    try:
        with args.input.open("rb") as file:
            content = file.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("Saved API response exceeds size limit")
        raw = json.loads(content)
        observed = datetime.fromisoformat(args.observed_at.replace("Z", "+00:00"))
        result = parse_toss_minute_candles(
            raw, symbol=args.symbol, quote_currency=args.currency,
            observed_at=observed, safety_seconds=args.safety_seconds,
        )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(output, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(result, file, sort_keys=True, indent=2, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    except (ValueError, TypeError, OSError, KeyError, InvalidOperation):
        print("Saved Toss paper candle import rejected; no account/order request was made.",
              file=sys.stderr)
        return 2
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
