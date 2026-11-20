"""One-shot Korea Investment Securities VTS QUOTE-only probe.

NEVER sends a broker order. Uses ONLY the virtual-trading host and the
domestic stock inquire-price GET route. All credentials/tokens must already
be held in the operator's process environment; no token issuance or disk cache.

This is a current-price observation, NOT a completed OHLCV bar. It must
never be fed directly into historical or forward candle strategies.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

VTS_BASE = "https://openapivts.koreainvestment.com:29443"
QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
QUOTE_TR_ID = "FHKST01010100"
MAX_RESPONSE_BYTES = 65536


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, target):
        raise ValueError("KIS VTS quote redirects are forbidden")


def _open_vts_quote(request, *, timeout):
    # Never forward bearer/app-secret headers through an environment proxy.\n    return build_opener(ProxyHandler({}), _DenyRedirect()).open(request, timeout=timeout)


def _text(label, value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(label + " must be a nonempty bounded string")
    if "\r" in value or "\n" in value:
        raise ValueError("Newlines forbidden in " + label)
    return value


def fetch_quote(symbol, *, token, appkey, appsecret,
                market="J", opener=_open_vts_quote,
                now=None):
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}", symbol):
        raise ValueError("KIS stock symbol must be a 6-digit domestic code")
    if market != "J":
        raise ValueError("Only explicit KRX quote market J is supported")
    # No credentials are ever used as URL params, persisted, or printed.
    token = _text("VTS access token", token)
    appkey = _text("VTS appkey", appkey)
    appsecret = _text("VTS appsecret", appsecret)
    query = urlencode({
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": symbol,
    })
    request = Request(
        VTS_BASE + QUOTE_PATH + "?" + query,
        headers={
            "accept": "application/json",
            "authorization": "Bearer " + token,
            "appkey": appkey,
            "appsecret": appsecret,
            "tr_id": QUOTE_TR_ID,
        }, method="GET",
    )
    with opener(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("KIS VTS quote request was not successful")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Unexpectedly large KIS VTS quote response")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("rt_cd") != "0":
        raise ValueError("KIS VTS did not confirm a valid quotation")
    values = result.get("output")
    if not isinstance(values, dict):
        raise ValueError("KIS VTS quote output is missing")
    try:
        price = Decimal(str(values["stck_prpr"]))
    except (KeyError, InvalidOperation, TypeError) as error:
        raise ValueError("Invalid KIS spot quote") from error
    if not price.is_finite() or price <= 0:
        raise ValueError("KIS spot quote must be finite and positive")
    observed = now() if now is not None else datetime.now(timezone.utc)
    if (not isinstance(observed, datetime) or observed.tzinfo is None\n            or observed.utcoffset() is None):
        raise ValueError("Observation time must be timezone aware")
    return {
        "mode": "paper", "live_orders_enabled": False,
        "provider": "kis_vts_readonly_quote",
        "market": "KRX", "symbol": symbol,
        "quote_currency": "KRW", "price": str(price),
        "observed_at_utc": observed.astimezone(timezone.utc).isoformat(),
        "quote_type": "current_spot_not_completed_candle",
        "validated_completed_bars": False,
        "warning": "VTS quote only; not an order, fill, account balance or strategy bar",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--allow-vts-readonly", action="store_true")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.allow_vts_readonly:
        parser.error("Explicit --allow-vts-readonly is required")
    target = args.output.resolve()
    if target.exists():
        parser.error("Output already exists; refusing to replace quote evidence")
    try:
        result = fetch_quote(
            args.symbol, token=os.environ.get("KIS_VTS_ACCESS_TOKEN"),
            appkey=os.environ.get("KIS_VTS_APPKEY"),
            appsecret=os.environ.get("KIS_VTS_APPSECRET"),
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(result, output, sort_keys=True, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    except Exception:
        # Avoid exposing token, app secrets, account data, response or URL.
        print("VTS read-only quote refused; no broker order sent.", file=sys.stderr)
        return 2
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
