"""Strict, offline OHLCV CSV importer for the paper/backtest fixture format.

Input is never fetched from an exchange. CSV provenance is caller-supplied;
no claim of historical authenticity or candle completeness is made.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from decimal import Decimal, DecimalException
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data import Candle, CandleBuffer

HEADER = ["timestamp", "open", "high", "low", "close", "volume"]


def convert(path: Path, symbol: str, timeframe: str, *, allow_gaps=False, max_bars=100000):
    if not isinstance(symbol, str) or symbol.count("/") != 1:
        raise ValueError("Symbol must be BASE/USDT or BASE/KRW")
    base, quote = symbol.split("/")
    if not base or quote not in ("USDT", "KRW") or base == quote:
        raise ValueError("Only single spot USDT/KRW quotes are supported")
    duration = CandleBuffer(timeframe=timeframe).duration_ms
    raw = path.read_bytes()
    if len(raw) > 30_000_000:
        raise ValueError("CSV exceeds 30 MB offline import limit")
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8") from exc
    reader = csv.DictReader(decoded.splitlines(), strict=True)
    if reader.fieldnames != HEADER:
        raise ValueError("CSV must have exactly timestamp,open,high,low,close,volume columns")
    candles = []
    gaps = 0
    previous = None
    for record in reader:
        if len(candles) >= max_bars:
            raise ValueError("CSV exceeds maximum bar count")
        if None in record or any(value is None or not value.strip() for value in record.values()):
            raise ValueError("Missing or extra CSV fields")
        stamp = record["timestamp"].strip()
        if not stamp.isascii() or not stamp.isdigit():
            raise ValueError("Timestamp must be nonnegative epoch milliseconds")
        timestamp = int(stamp)
        if timestamp % duration:
            raise ValueError("Bar opening timestamp must align to the timeframe")
        candle = Candle(timestamp, *[Decimal(record[key]) for key in HEADER[1:]])
        if previous is not None:
            delta = timestamp - previous
            if delta < duration:
                raise ValueError("Duplicate, descending, or overlapping candle")
            if delta > duration:
                gaps += 1
                if not allow_gaps:
                    raise ValueError("Missing candles; pass --allow-gaps to acknowledge gaps")
        previous = timestamp
        candles.append([timestamp, *[format(getattr(candle, key), "f") for key in HEADER[1:]]])
    if not candles:
        raise ValueError("No OHLCV rows in CSV")
    return {
        "mode": "paper",
        "symbol": symbol,
        "timeframe": timeframe,
        "description": "Caller-supplied local CSV; authenticity/completeness not established",
        "import_metadata": {
            "csv_sha256": hashlib.sha256(raw).hexdigest(),
            "bars": len(candles),
            "gap_count": gaps,
            "gaps_acknowledged": bool(allow_gaps),
            "timestamp_unit": "epoch_milliseconds",
            "origin": "caller_supplied_csv",
        },
        "candles": candles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-gaps", action="store_true")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.csv.resolve():
        parser.error("Output must not replace original CSV")
    try:
        payload = convert(args.csv, args.symbol, args.timeframe, allow_gaps=args.allow_gaps)
        target = args.output.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target.parent,
                prefix=".offline-import-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    except (ValueError, OSError, csv.Error, OverflowError, DecimalException):
        parser.exit(2, "CSV import rejected; existing input was not modified.\n")
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
