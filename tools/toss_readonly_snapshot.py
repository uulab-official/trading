"""Explicitly opted-in READ-ONLY Toss Securities 1m snapshot.

Only two hard-coded URLs are reachable:
POST /oauth2/token for OAuth and GET /api/v1/candles for quotes.
No account identifiers, holdings access, private order endpoints, or trading.
Credentials must be provided via environment, never CLI flags or saved files.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data.toss_stock_candles import parse_toss_minute_candles

HOST = "https://openapi.tossinvest.com"
MAX_RESPONSE = 2_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, target):
        raise ValueError("Cross-endpoint redirects are forbidden for Toss OAuth")


def _open_no_redirect(request, *, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def _read_json(request, opener):
    # Deliberately do not return token-bearing error bodies or URLs in CLI errors.
    with opener(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Expected HTTP 200 from Toss Securities")
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValueError("Toss response exceeded size limit")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("Unexpected Toss JSON payload")
    return parsed


def fetch_readonly_minute(symbol, currency, *, count=100, adjusted=True,
                          client_id=None, client_secret=None,
                          opener=_open_no_redirect, observed_at=None):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,20}", symbol):
        raise ValueError("Invalid symbol")
    if currency not in ("KRW", "USD"):
        raise ValueError("Only KRW/USD stock paper snapshots supported")
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("Toss candle count must be 1..200")
    if type(adjusted) is not bool:
        raise ValueError("Adjusted must be an explicit boolean")
    if not client_id or not client_secret:
        raise ValueError("Toss OAuth client credentials are required")
    token_request = Request(
        HOST + "/oauth2/token",
        data=urlencode({
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }).encode("utf-8"),
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    issued = _read_json(token_request, opener)
    token = issued.get("access_token")
    if issued.get("token_type") != "Bearer" or not isinstance(token, str) or not token:
        raise ValueError("Toss did not issue a valid Bearer token")
    query = urlencode({
        "symbol": symbol, "interval": "1m", "count": count,
        "adjusted": str(adjusted).lower(),
    })
    # Do not supply X-Tossinvest-Account: market data has no account scope.
    candle_request = Request(
        HOST + "/api/v1/candles?" + query,
        headers={"Accept": "application/json",
                 "Authorization": "Bearer " + token},
        method="GET",
    )
    page = _read_json(candle_request, opener)
    now = observed_at or datetime.now(timezone.utc)
    normalized = parse_toss_minute_candles(
        page, symbol=symbol, quote_currency=currency,
        observed_at=now,
    )
    normalized["source"] = "toss_readonly_authenticated_quote"
    normalized["adjusted"] = adjusted
    normalized["observed_at_utc"] = now.astimezone(timezone.utc).isoformat()
    normalized["warning"] = (
        "No broker orders; quote source was authenticated but fills remain hypothetical"
    )
    return normalized


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("paper",), required=True)
    parser.add_argument("--allow-network-readonly", action="store_true",
                        help="Explicitly authorize OAuth and one GET market-data request")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--currency", required=True, choices=("KRW", "USD"))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--adjusted", choices=("true", "false"), default="true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.allow_network_readonly:
        parser.error("Opt in to read-only OAuth market data with --allow-network-readonly")
    # Checking the destination before OAuth avoids issuing a new token
    # (which can invalidate the current token) for a duplicate snapshot.
    target = args.output.resolve()
    if target.exists():
        parser.error("Output already exists: use a new snapshot filename")
    try:
        snapshot = fetch_readonly_minute(
            args.symbol, args.currency, count=args.count,
            adjusted=args.adjusted == "true",
            client_id=os.environ.get("TOSS_CLIENT_ID"),
            client_secret=os.environ.get("TOSS_CLIENT_SECRET"),
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        handle = os.open(target, flags, 0o600)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, sort_keys=True, indent=2, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    except Exception:
        # Never print OAuth client ID/secret, access tokens, request bodies,
        # URL headers, or upstream response text.
        print("Read-only Toss market snapshot failed; no order API was called.",
              file=sys.stderr)
        return 2
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
