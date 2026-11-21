"""Explicitly opted-in READ-ONLY Toss Securities 1m snapshot.

Reuses an existing access token for one GET /api/v1/candles request.
POST /oauth2/token is reachable only with explicit token-issuance opt-in
and no supplied access token. A failed request never retries or refreshes.
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
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/data"))
from trading_data.toss_stock_candles import parse_toss_minute_candles

HOST = "https://openapi.tossinvest.com"
MAX_RESPONSE = 2_000_000
MAX_TOKEN_LENGTH = 4096
MAX_CREDENTIAL_LENGTH = 4096


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, target):
        raise ValueError("Toss request redirects are forbidden")


def _open_no_redirect(request, *, timeout):
    # Never send client credentials or bearer headers through environment proxies.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _bearer_token(value):
    # RFC 6750 section 2.1 b64token; padding is permitted only at the end.
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_TOKEN_LENGTH
            or not re.fullmatch(r"[-A-Za-z0-9._~+/]+=*", value)):
        raise ValueError("A bounded, valid Bearer token is required")
    return value


def _credential(value):
    if (not isinstance(value, str) or not value.strip()
            or len(value) > MAX_CREDENTIAL_LENGTH
            or any(not 32 <= ord(character) <= 126 for character in value)):
        raise ValueError("Toss OAuth credentials must be bounded printable ASCII strings")
    return value


def _observation_time(value):
    if value is None:
        return None
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ValueError("Observation time must be timezone aware")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as error:
        raise ValueError("Observation time cannot be represented in UTC") from error


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
                          access_token=None, issue_token=False,
                          opener=_open_no_redirect, observed_at=None):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,20}", symbol):
        raise ValueError("Invalid symbol")
    if currency not in ("KRW", "USD"):
        raise ValueError("Only KRW/USD stock paper snapshots supported")
    if type(count) is not int or not 1 <= count <= 200:
        raise ValueError("Toss candle count must be 1..200")
    if type(adjusted) is not bool:
        raise ValueError("Adjusted must be an explicit boolean")
    if type(issue_token) is not bool:
        raise ValueError("Token issuance must be an explicit boolean")
    observed = _observation_time(observed_at)
    if access_token is not None:
        # Even an invalid supplied token must never fall back to issuing a new one.
        token = _bearer_token(access_token)
    else:
        if not issue_token:
            raise ValueError("Supply an access token or explicitly opt in to token issuance")
        client_id = _credential(client_id)
        client_secret = _credential(client_secret)
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
        if issued.get("token_type") != "Bearer":
            raise ValueError("Toss did not issue a valid Bearer token")
        token = _bearer_token(issued.get("access_token"))
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
    now = observed if observed is not None else datetime.now(timezone.utc)
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
                        help="Explicitly authorize read-only market-data network access")
    parser.add_argument("--issue-token", action="store_true",
                        help="Allow new OAuth issuance if no access token is supplied; "
                             "this invalidates the client's previous token")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--currency", required=True, choices=("KRW", "USD"))
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--adjusted", choices=("true", "false"), default="true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.allow_network_readonly:
        parser.error("Opt in to read-only market data with --allow-network-readonly")
    try:
        # Resolve the parent only: a dangling final symlink is existing evidence.
        # Refuse impossible destinations before token issuance can revoke a token.
        target = args.output.parent.resolve() / args.output.name
        if target.exists() or target.is_symlink():
            parser.error("Output already exists: use a new snapshot filename")
        for ancestor in target.parents:
            if ancestor.exists():
                if not ancestor.is_dir():
                    parser.error("Output ancestors must be directories")
                break
        snapshot = fetch_readonly_minute(
            args.symbol, args.currency, count=args.count,
            adjusted=args.adjusted == "true",
            client_id=os.environ.get("TOSS_CLIENT_ID"),
            client_secret=os.environ.get("TOSS_CLIENT_SECRET"),
            access_token=os.environ.get("TOSS_ACCESS_TOKEN"),
            issue_token=args.issue_token,
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
