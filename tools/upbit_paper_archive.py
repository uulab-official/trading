"""Append-only public-candle paper archive with overlap verification.

Captures one new public snapshot per invocation. No live orders,
credentials, daemon, background worker, or exchange private endpoints.
"""
import argparse
from decimal import DecimalException
import json
import os
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/data", ROOT / "apps/server"):
    sys.path.insert(0, str(source))

from tools.upbit_public_paper_snapshot import build_snapshot
from trading_bot.paper_checkpoint import locked_checkpoint
from trading_data import Candle, CandleBuffer


def _validated_rows(payload):
    if not isinstance(payload, dict) or payload.get("mode") != "paper":
        raise ValueError("Only paper archives are supported")
    rows = payload.get("candles")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Archive must contain bars")
    duration = CandleBuffer(timeframe=payload["timeframe"]).duration_ms
    if len(rows) > 100000:
        raise ValueError("Archive exceeded 100,000-bar limit")
    normalized = []
    for raw in rows:
        if not isinstance(raw, list) or len(raw) != 6:
            raise ValueError("Invalid paper bar shape")
        candle = Candle(*raw)
        if candle.timestamp % duration:
            raise ValueError("Misaligned paper timestamp")
        if normalized and candle.timestamp < normalized[-1].timestamp + duration:
            raise ValueError("Overlapping or unordered paper bars")
        normalized.append(candle)
    return normalized, duration


def merge_archive(archive, snapshot, *, allow_gaps=False):
    """Return (new_archive, appended_count) or reject discontinuous revisions."""
    fresh, duration = _validated_rows(snapshot)
    symbol, timeframe = snapshot["symbol"], snapshot["timeframe"]
    if not isinstance(symbol, str) or symbol.count("/") != 1 or not symbol.endswith("/KRW"):
        raise ValueError("Only KRW spot paper symbols are accepted")
    if archive is None:
        metadata = {
            "provider": "upbit_public_quotation", "snapshots": 1,
            "source": "public_upbit_completed_candles", "live_orders_enabled": False,
        }
        return {
            "mode": "paper", "symbol": symbol, "timeframe": timeframe,
            "description": "Append-only public Upbit closed candles for offline paper replay",
            "archive_metadata": metadata,
            "candles": [list(row) for row in snapshot["candles"]],
        }, len(fresh)

    old, old_duration = _validated_rows(archive)
    if old_duration != duration or archive["symbol"] != symbol or archive["timeframe"] != timeframe:
        raise ValueError("Cannot mix market, currency or timeframe in a saved archive")
    meta = archive.get("archive_metadata")
    if not isinstance(meta, dict) or meta.get("provider") != "upbit_public_quotation" or meta.get("live_orders_enabled") is not False:
        raise ValueError("Unrecognized paper archive provenance")
    if type(meta.get("snapshots")) is not int or meta["snapshots"] < 1:
        raise ValueError("Invalid archive snapshot counter")
    known = {bar.timestamp: bar for bar in old}
    matches = 0
    new_bars = []
    last = old[-1].timestamp
    for bar in fresh:
        if bar.timestamp in known:
            if known[bar.timestamp] != bar:
                raise ValueError("Previously completed candle was revised; refusing archive rewrite")
            matches += 1
        elif bar.timestamp < last:
            # Older bars outside the saved window cannot silently be inserted.
            raise ValueError("Unknown historical candle found in previously archived range")
        else:
            new_bars.append(bar)
    if matches == 0:
        raise ValueError("No overlapping candle; cannot verify continuity of public archive")
    new_bars.sort(key=lambda bar: bar.timestamp)
    appended = []
    for bar in new_bars:
        if bar.timestamp - last < duration:
            raise ValueError("Noncausal appended bar")
        if bar.timestamp - last > duration and not allow_gaps:
            raise ValueError("Missing archive interval; explicit --allow-gaps required")
        last = bar.timestamp
        appended.append([bar.timestamp, *[format(getattr(bar, field), "f")
                       for field in ("open", "high", "low", "close", "volume")]])
    if len(old) + len(appended) > 100000:
        raise ValueError("Archive would exceed 100,000 bars")
    if not appended:
        return archive, 0
    merged = dict(archive)
    merged["candles"] = archive["candles"] + appended
    merged["archive_metadata"] = dict(meta, snapshots=meta["snapshots"] + 1)
    return merged, len(appended)


def capture_once(path: Path, *, market="KRW-BTC", unit=60, count=200,
                 allow_gaps=False, fetcher=None, now_ms=None):
    snapshot = build_snapshot(market, unit, count=count, allow_gaps=allow_gaps,
                              fetcher=fetcher, now_ms=now_ms)
    with locked_checkpoint(path) as canonical:
        prior = json.loads(canonical.read_text("utf-8")) if canonical.exists() else None
        merged, added = merge_archive(prior, snapshot, allow_gaps=allow_gaps)
        if added:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=canonical.parent,
                                                 prefix=".upbit-archive-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    os.chmod(temporary, 0o600)
                    json.dump(merged, stream, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, canonical)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        return {"mode": "paper", "appended_bars": added, "archived_bars": len(merged["candles"]),
                "live_orders_enabled": False, "archive": str(canonical)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--unit", type=int, default=60)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--allow-gaps", action="store_true")
    args = parser.parse_args(argv)
    try:
        outcome = capture_once(args.archive, market=args.market, unit=args.unit,
                               count=args.count, allow_gaps=args.allow_gaps)
    except (ValueError, OSError, KeyError, TypeError, DecimalException):
        parser.exit(2, "Paper archive refused public data; no orders or account access attempted.\n")
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
