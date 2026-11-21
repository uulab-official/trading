"""Validate a saved Toss Securities 1-minute candle response for PAPER research.

Toss's 1m candle timestamp is the *closing* boundary, unlike Upbit's
opening timestamp. No authentication, API access, account, or order calls.
Never treat 1d local-midnight timestamps as completed 1m bars.
KRW 15:30 source bars may aggregate trades until 16:00 Korea time and
are excluded because this fixed-minute format cannot express that interval.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

KST = timezone(timedelta(hours=9))


def parse_toss_minute_candles(document, *, symbol, quote_currency, observed_at, safety_seconds=5):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,20}", symbol):
        raise ValueError("Explicit Toss stock symbol required")
    if quote_currency not in ("KRW", "USD"):
        raise ValueError("Only KRW/USD paper stock denominations supported")
    if not isinstance(document, dict):
        raise ValueError("Toss response must be JSON object")
    result = document.get("result")
    if not isinstance(result, dict):
        raise ValueError("Toss response must contain a result object")
    records = result.get("candles")
    if not isinstance(records, list) or not (1 <= len(records) <= 200):
        raise ValueError("Expected 1..200 one-minute candle rows")
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise ValueError("Explicit timezone-aware observation timestamp required")
    if type(safety_seconds) is not int or not 0 <= safety_seconds <= 300:
        raise ValueError("Invalid safety delay")
    observed = observed_at.astimezone(timezone.utc)
    rows = []
    times = set()
    for raw in records:
        if not isinstance(raw, dict) or raw.get("currency") != quote_currency:
            raise ValueError("Candle currency mismatch")
        timestamp = raw.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("Missing candle closing timestamp")
        try:
            closed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Invalid candle closing timestamp") from error
        if closed_at.tzinfo is None or closed_at.utcoffset() is None:
            raise ValueError("Candle must state an explicit timezone offset")
        closed_at = closed_at.astimezone(timezone.utc)
        if closed_at.second or closed_at.microsecond:
            raise ValueError("One-minute candle end must align to a minute")
        stamp = int(closed_at.timestamp() * 1000)
        if stamp in times:
            raise ValueError("Duplicate minute candle")
        times.add(stamp)
        korean_close = closed_at.astimezone(KST)
        if quote_currency == "KRW" and (korean_close.hour, korean_close.minute) == (15, 30):
            # The official FAQ allows non-NXT 15:30 bars to change until 16:00.
            # Without exchange / availability metadata, a fixed-minute replay
            # would expose later trades too early. Omit these aggregates entirely.
            continue
        if closed_at > observed - timedelta(seconds=safety_seconds):
            # A just-ending / future bar cannot influence a paper decision.
            continue
        numeric = []
        for field in ("openPrice", "highPrice", "lowPrice", "closePrice", "volume"):
            try:
                value = Decimal(str(raw[field]))
            except (InvalidOperation, KeyError, TypeError) as error:
                raise ValueError("Invalid stock OHLCV field") from error
            if not value.is_finite() or (value < 0 if field == "volume" else value <= 0):
                raise ValueError("Invalid stock OHLCV field")
            numeric.append(value)
        op, hi, lo, close, volume = numeric
        if not lo <= op <= hi or not lo <= close <= hi:
            raise ValueError("Invalid OHLC bounds")
        rows.append([stamp - 60000, *[str(value) for value in numeric]])
    if not rows:
        raise ValueError("No sufficiently completed minute bars")
    rows.sort(key=lambda row: row[0])
    if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("Unexpected minute order")
    return {
        "mode": "paper",
        "provider": "tossinvest_caller_supplied_json",
        "source": "operator_supplied_response_not_authenticated",
        "symbol": symbol,
        "quote_currency": quote_currency,
        "interval": "1m",
        "timestamp_semantics": "source_close_to_utc_open",
        "no_live_orders": True,
        "candles": rows,
        "warning": "Session gaps and real market provenance require independent verification",
    }
