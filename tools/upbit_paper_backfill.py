"""Bounded, read-only Upbit public candle gap recovery proposal.

Never mutates source archive, checkpoint or orders. Writes a distinct candidate
fixture for manual audit. Uses only public quotation with exclusive UTC cutoffs.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import DecimalException
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/data", ROOT / "apps/server"):
    sys.path.insert(0, str(source))

from trading_data import Candle, CandleBuffer

from tools.upbit_paper_archive import _validated_rows, merge_archive
from tools.upbit_public_paper_snapshot import (
    ALLOWED_UNITS,
    _request_candles,
    _time_ms,
    build_snapshot,
)


def _historical_page(rows, market, unit, cutoff, max_count):
    if not isinstance(rows, list) or len(rows) > max_count:
        raise ValueError("Invalid historical response row count")
    duration = CandleBuffer(timeframe=f"{unit}m").duration_ms
    validated = []
    timestamps = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("market") != market or row.get("unit") != unit:
            raise ValueError("Historical page market/timeframe mismatch")
        stamp = _time_ms(row.get("candle_date_time_utc"))
        if stamp >= cutoff or stamp % duration:
            raise ValueError("Historical candles must precede the exclusive cutoff")
        if stamp in timestamps:
            raise ValueError("Duplicate historical candle")
        timestamps.add(stamp)
        candle = Candle(
            stamp,
            row["opening_price"],
            row["high_price"],
            row["low_price"],
            row["trade_price"],
            row["candle_acc_trade_volume"],
        )
        validated.append(
            [
                stamp,
                *[
                    format(getattr(candle, attr), "f")
                    for attr in ("open", "high", "low", "close", "volume")
                ],
            ]
        )
    return sorted(validated, key=lambda row: row[0])


def plan_backfill(
    archive,
    *,
    market="KRW-BTC",
    unit=60,
    pages=3,
    count=200,
    now_ms=None,
    fetcher=None,
    newest=None,
):
    """Create fully verified candidate archive; caller controls writing separately.

    fetcher receives (unit, market, count, to_ms), returning raw Upbit rows.
    A missing overlap, empty page, inconsistency or exhausted budget refuses.
    """
    if type(pages) is not int or not (1 <= pages <= 10):
        raise ValueError("Backfill page budget must be 1..10")
    if type(count) is not int or not (1 <= count <= 200):
        raise ValueError("Each historical request must be 1..200")
    if type(unit) is not int or unit not in ALLOWED_UNITS:
        raise ValueError("Unsupported Upbit interval")
    previous, duration = _validated_rows(archive)
    if archive.get("symbol") != market[4:] + "/KRW" or archive.get("timeframe") != f"{unit}m":
        raise ValueError("Backfill identity does not match saved archive")
    newest = (
        newest if newest is not None else build_snapshot(market, unit, count=count, now_ms=now_ms)
    )
    current, newest_duration = _validated_rows(newest)
    if newest_duration != duration or newest.get("symbol") != archive["symbol"]:
        raise ValueError("Newest public snapshot identity mismatch")
    if newest.get("mode") != "paper":
        raise ValueError("Only paper snapshots accepted")
    all_rows = {row.timestamp: row for row in current}
    known = {row.timestamp: row for row in previous}
    last_saved = previous[-1].timestamp

    def has_overlap():
        return any(stamp in known for stamp in all_rows)

    cutoff = min(all_rows)
    requests_made = 0
    while not has_overlap():
        if requests_made >= pages:
            raise ValueError("Public quotation history still does not overlap archive")
        # Stop once the oldest retrieved bar is older than all archived data:
        # no overlap means contradictory history or missing material.
        if cutoff < last_saved:
            raise ValueError("Reached past saved archive without a matching candle")
        raw = (
            _request_candles(unit, market, count, to=cutoff)
            if fetcher is None
            else fetcher(unit, market, count, cutoff)
        )
        page = _historical_page(raw, market, unit, cutoff, count)
        requests_made += 1
        if not page:
            raise ValueError("Public history backfill returned an empty page")
        for row in page:
            candle = Candle(*row)
            if candle.timestamp in all_rows and all_rows[candle.timestamp] != candle:
                raise ValueError("Conflicting candle across public pages")
            all_rows[candle.timestamp] = candle
        next_cutoff = min(row[0] for row in page)
        if next_cutoff >= cutoff:
            raise ValueError("Backfill pagination did not move backwards")
        cutoff = next_cutoff
    ordered = [row for _, row in sorted(all_rows.items())]
    # Legacy archive merger verifies that the overlapping completed OHLCV bars
    # are byte-equivalent economically, no future data is inserted in the past,
    # and no missing intervals are silently manufactured.
    combined = {
        "mode": "paper",
        "symbol": newest["symbol"],
        "timeframe": newest["timeframe"],
        "candles": [
            [
                bar.timestamp,
                *[
                    format(getattr(bar, attr), "f")
                    for attr in ("open", "high", "low", "close", "volume")
                ],
            ]
            for bar in ordered
        ],
    }
    candidate, added = merge_archive(archive, combined, allow_gaps=False)
    if not added:
        raise ValueError("No new complete bars found in historical recovery")
    candidate["description"] = "Offline paper archive recovery CANDIDATE; manual review required"
    candidate["backfill_review"] = {
        "public_pages_fetched": requests_made,
        "appended_bars": added,
        "live_orders_enabled": False,
        "manual_promotion_required": True,
    }
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--unit", type=int, default=60)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--count", type=int, default=200)
    args = parser.parse_args(argv)
    if args.archive.resolve() == args.candidate.resolve():
        parser.error("Candidate must not replace the original archive")
    try:
        original = args.archive.read_bytes()
        old = json.loads(original)
        candidate = plan_backfill(
            old, market=args.market, unit=args.unit, pages=args.pages, count=args.count
        )
        if args.archive.read_bytes() != original:
            raise ValueError("Original archive changed during data collection")
        target = args.candidate.resolve()
        if target.exists():
            raise FileExistsError("Recovery candidate already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive file creation: unlike os.replace, never overwrite a
        # concurrently created recovery proposal.
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(candidate, output, indent=2, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
        except Exception:
            target.unlink(missing_ok=True)
            raise
    except (ValueError, OSError, KeyError, TypeError, DecimalException):
        print("Public candle recovery refused; original paper archive unchanged.", file=sys.stderr)
        return 2
    print(json.dumps(candidate["backfill_review"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
