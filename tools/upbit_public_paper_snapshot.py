"""One-shot read-only Upbit public-candle snapshot for offline paper replay.

No exchange credentials, no orders, no auto-polling, and no live trading mode.
The latest/incomplete candle is always excluded.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data import Candle, CandleBuffer

ALLOWED_UNITS = (1, 3, 5, 10, 15, 30, 60, 240)
ENDPOINT = "https://api.upbit.com/v1/candles/minutes/"
MAX_BYTES = 1024 * 1024


def _time_ms(utc_text):
    if not isinstance(utc_text, str):
        raise ValueError("Missing UTC candle opening time")
    try:
        moment = datetime.strptime(utc_text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Candle opening timestamp must be UTC ISO-8601") from exc
    return int(moment.timestamp() * 1000)


def _request_candles(unit, market, count):
    query = urlencode({"market": market, "count": count})
    request = Request(
        ENDPOINT + str(unit) + "?" + query,
        headers={"Accept": "application/json", "User-Agent": "uulab-offline-paper/1"},
        method="GET",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Public candle API returned non-200 status")
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Public candle API response exceeds size limit")
    try:
        return json.loads(raw, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Public candle API did not return valid JSON") from exc


def build_snapshot(market, unit, *, count=200, now_ms=None, safety_seconds=5,
                   allow_gaps=False, fetcher=None):
    if not isinstance(market, str) or not market.startswith("KRW-"):
        raise ValueError("Only explicitly selected KRW-BASE public Upbit markets are accepted")
    base = market[4:]
    if not base or not base.isascii() or not base.isalnum() or base.upper() != base:
        raise ValueError("Invalid KRW market symbol")
    if type(unit) is not int or unit not in ALLOWED_UNITS:
        raise ValueError("Unsupported minute timeframe")
    if type(count) is not int or not (1 <= count <= 200):
        raise ValueError("Upbit count must be between 1 and 200")
    if type(safety_seconds) is not int or not (0 <= safety_seconds <= 300):
        raise ValueError("Invalid safety delay")
    if now_ms is None:
        import time
        now_ms = time.time_ns() // 1_000_000
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("Invalid observation clock")
    rows = _request_candles(unit, market, count) if fetcher is None else fetcher(unit, market, count)
    if not isinstance(rows, list) or not rows or len(rows) > count:
        raise ValueError("Upbit response has an unexpected candle count")
    duration = CandleBuffer(timeframe=f"{unit}m").duration_ms
    seen = set()
    candles = []
    for item in rows:
        if not isinstance(item, dict) or item.get("market") != market or item.get("unit") != unit:
            raise ValueError("Unexpected market or timeframe in candle response")
        stamp = _time_ms(item.get("candle_date_time_utc"))
        if stamp % duration:
            raise ValueError("Candle timestamp is not aligned")
        if stamp in seen:
            raise ValueError("Upbit returned duplicate opening timestamps")
        seen.add(stamp)
        if stamp + duration > now_ms - safety_seconds * 1000:
            # A forming candle is never strategy input.
            continue
        candle = Candle(stamp, item["opening_price"], item["high_price"], item["low_price"],
                        item["trade_price"], item["candle_acc_trade_volume"])
        candles.append([stamp, *[format(getattr(candle, attr), "f")
                                for attr in ("open", "high", "low", "close", "volume")]])
    if not candles:
        raise ValueError("No completed candles available; fail closed")
    candles.sort(key=lambda row: row[0])
    gaps = 0
    for left, right in zip(candles, candles[1:]):
        delta = right[0] - left[0]
        if delta != duration:
            gaps += 1
            if not allow_gaps:
                raise ValueError("Public candle history has gaps; require explicit --allow-gaps")
    latest_age_ms = now_ms - (candles[-1][0] + duration)
    if latest_age_ms > 2 * duration:
        raise ValueError("Latest completed public candle is stale; paper replay refused")
    return {
        "mode": "paper",
        "symbol": f"{base}/KRW",
        "timeframe": f"{unit}m",
        "description": "Read-only public Upbit candle snapshot; no order/account endpoint",
        "market_data": {
            "provider": "upbit_public_quotation",
            "market": market,
            "observed_at_ms": now_ms,
            "last_completed_open_ms": candles[-1][0],
            "safety_seconds": safety_seconds,
            "gap_count": gaps,
            "gaps_acknowledged": bool(allow_gaps),
            "execution": "offline_paper_fixture_only",
            "live_orders_enabled": False,
        },
        "candles": candles,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="KRW-BTC", help="Upbit public KRW pair (e.g. KRW-BTC)")
    parser.add_argument("--unit", type=int, choices=ALLOWED_UNITS, default=60)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--safety-seconds", type=int, default=5)
    parser.add_argument("--allow-gaps", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(args.market, args.unit, count=args.count,
                                  safety_seconds=args.safety_seconds, allow_gaps=args.allow_gaps)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                             prefix=".upbit-public-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump(snapshot, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    except (ValueError, OSError, KeyError, TypeError, DecimalException, HTTPError, URLError):
        parser.exit(2, "Public paper snapshot rejected. No trading or account action was attempted.\n")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
