"""Collect bounded, READ-ONLY KIS VTS daily-order observations.

Only the fixed virtual-trading daily-inquiry GET endpoint is reachable. Reuse
existing environment credentials; never issue a token, send an order, retry a
request, or update an accounting journal. Cumulative order observations are
not individual executions, reconciled balances, or inferred lifecycle events.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VTS_BASE = "https://openapivts.koreainvestment.com:29443"
ORDER_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
ORDER_TR_ID = "VTTC0081R"
MAX_RESPONSE_BYTES = 2_000_000
MAX_PAGE_ROWS = 15
MAX_TOTAL_ROWS = 300
KST = timezone(timedelta(hours=9))


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, target):
        raise ValueError("KIS VTS order-inquiry redirects are forbidden")

    def http_error_308(self, request, file_pointer, code, message, headers):
        # Python 3.10 has no inherited HTTP 308 redirect handler.
        raise ValueError("KIS VTS order-inquiry redirects are forbidden")


def _open_vts_readonly(request, *, timeout):
    # An environment proxy must never receive account queries or credentials.
    return build_opener(ProxyHandler({}), _DenyRedirect()).open(request, timeout=timeout)


def _matches(value, pattern, maximum):
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and re.fullmatch(pattern, value) is not None
    )


def _validate_inputs(
    inquiry_date, account_ref, token, appkey, appsecret, cano, acnt_prdt_cd, max_pages, now
):
    if not _matches(account_ref, r"[A-Za-z][A-Za-z0-9._-]*", 64):
        raise ValueError("Invalid local account alias")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("Invalid page limit")
    if not _matches(cano, r"[0-9]{8}", 8):
        raise ValueError("Invalid account number")
    if not _matches(acnt_prdt_cd, r"[0-9]{2}", 2):
        raise ValueError("Invalid account product code")
    if not _matches(token, r"[A-Za-z0-9._~+/-]+=*", 4096):
        raise ValueError("Invalid existing VTS access token")
    for credential in (appkey, appsecret):
        if not _matches(credential, r"[\x21-\x7e]+", 4096):
            raise ValueError("Invalid existing VTS application credential")
    if any(private in account_ref for private in (cano, token, appkey, appsecret)):
        raise ValueError("Local alias cannot contain account or credential values")
    if not _matches(inquiry_date, r"[0-9]{4}-[0-9]{2}-[0-9]{2}", 10):
        raise ValueError("Invalid inquiry date")
    selected = date.fromisoformat(inquiry_date)
    observed = datetime.now(timezone.utc) if now is None else now()
    if (
        not isinstance(observed, datetime)
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        raise ValueError("Observation time must be timezone aware")
    observed_utc = observed.astimezone(timezone.utc)
    today_kst = observed.astimezone(KST).date()
    if not 0 <= (today_kst - selected).days < 30:
        raise ValueError("Inquiry date must be within the past 30 KST dates")
    return selected.strftime("%Y%m%d"), observed_utc.isoformat()


def _normalized_pairs(pairs, *, json_object=False):
    normalized = {}
    exact_keys = set()
    for key, value in pairs:
        if not isinstance(key, str):
            raise ValueError("Malformed response keys")
        if json_object and key in exact_keys:
            raise ValueError("Duplicate JSON keys are forbidden")
        exact_keys.add(key)
        lowered = key.lower()
        if lowered in normalized:
            # JSON equality is type-sensitive, unlike Python's True == 1.
            previous = json.dumps(normalized[lowered], sort_keys=True, allow_nan=False)
            current = json.dumps(value, sort_keys=True, allow_nan=False)
            if previous != current:
                raise ValueError("Conflicting response key casing")
        else:
            normalized[lowered] = value
    return normalized


def _json_object(pairs):
    return _normalized_pairs(pairs, json_object=True)


def _reject_json_constant(unused):
    raise ValueError("Nonfinite JSON is forbidden")


def _json_integer(value):
    # Python 3.10 lacks the integer conversion limit of newer interpreters.
    if len(value) > 128:
        raise ValueError("Oversized JSON number")
    return int(value)


def _json_float(value):
    if len(value) > 128:
        raise ValueError("Oversized JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result


def _read_page(request, opener):
    with opener(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("KIS VTS inquiry HTTP status refused")
        headers = _normalized_pairs(response.headers.items())
        if "tr_id" in headers and headers["tr_id"] != ORDER_TR_ID:
            raise ValueError("Unexpected response transaction")
        continuation = headers.get("tr_cont")
        if continuation not in ("M", "F", "D", "E"):
            raise ValueError("Missing or unknown pagination signal")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("KIS VTS inquiry response size refused")
    body = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_json_object,
        parse_constant=_reject_json_constant,
        parse_int=_json_integer,
        parse_float=_json_float,
    )
    if not isinstance(body, dict) or body.get("rt_cd") != "0":
        raise ValueError("KIS VTS did not confirm successful inquiry")
    rows = body.get("output1")
    if not isinstance(rows, list) or len(rows) > MAX_PAGE_ROWS:
        raise ValueError("Malformed or oversized VTS order page")
    return body, rows, continuation


def _digits(value, maximum, *, nonzero=False):
    if not _matches(value, r"[0-9]+", maximum):
        raise ValueError("Malformed order observation")
    if nonzero and not any(character != "0" for character in value):
        raise ValueError("Invalid zero order identity")
    return value


def _number(value, maximum, *, fractional=False):
    pattern = r"[0-9]+(?:\.[0-9]+)?" if fractional else r"[0-9]+"
    if not _matches(value, pattern, maximum):
        raise ValueError("Malformed numeric order observation")
    integer, separator, fraction = value.partition(".")
    integer = integer.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return integer + ("." + fraction if separator and fraction else "")


def _normalize_row(row, inquiry_day):
    if not isinstance(row, dict):
        raise ValueError("Malformed order record")
    if row.get("ord_dt") != inquiry_day:
        raise ValueError("Order date does not match inquiry")
    result = {
        "ord_dt": inquiry_day,
        "ord_gno_brno": _digits(row.get("ord_gno_brno"), 5),
        "odno": _digits(row.get("odno"), 10, nonzero=True),
    }
    original = row.get("orgn_odno")
    result["orgn_odno"] = "" if original == "" else _digits(original, 10)
    symbol = row.get("pdno")
    if not _matches(symbol, r"[0-9]{6}", 6):
        raise ValueError("Unsupported domestic stock identity")
    result["pdno"] = symbol
    side = row.get("sll_buy_dvsn_cd")
    if side not in ("01", "02"):
        raise ValueError("Unsupported order side")
    result["sll_buy_dvsn_cd"] = side
    division = row.get("ord_dvsn_cd")
    if not _matches(division, r"[0-9]{2}", 2):
        raise ValueError("Malformed order division code")
    result["ord_dvsn_cd"] = division
    for field in ("ord_qty", "tot_ccld_qty", "rmn_qty", "cncl_cfrm_qty", "rjct_qty"):
        result[field] = _number(row.get(field), 10)
    for field in ("ord_unpr", "tot_ccld_amt"):
        result[field] = _number(row.get(field), 19)
    result["avg_prvs"] = _number(row.get("avg_prvs"), 19, fractional=True)
    cancellation = row.get("cncl_yn")
    if cancellation not in ("", "Y", "N"):
        raise ValueError("Unsupported cancellation flag")
    result["cncl_yn"] = cancellation
    order_time = row.get("ord_tmd")
    if not _matches(order_time, r"[0-9]{6}", 6):
        raise ValueError("Malformed order time")
    if int(order_time[:2]) > 23 or int(order_time[2:4]) > 59 or int(order_time[4:]) > 59:
        raise ValueError("Invalid order time")
    result["ord_tmd"] = order_time
    # The portal's older response example omits this newer field. Do not invent
    # it when absent, and refuse an exchange incompatible with the VTS query.
    if "excg_id_dvsn_cd" in row:
        if row["excg_id_dvsn_cd"] != "KRX":
            raise ValueError("Unsupported observation exchange")
        result["excg_id_dvsn_cd"] = "KRX"
    return result


def _cursor(value):
    if not _matches(value, r"[\x20-\x7e]+", 100) or not value.strip():
        raise ValueError("Missing or malformed continuation cursor")
    # Spaces are part of the broker's opaque cursor. Never strip or emit it.
    return value


def fetch_order_snapshot(
    inquiry_date,
    *,
    account_ref,
    token,
    appkey,
    appsecret,
    cano,
    acnt_prdt_cd,
    max_pages=20,
    opener=_open_vts_readonly,
    now=None,
):
    """Return complete allowlisted observations, or raise a static ValueError.

    ``now`` is an optional callable returning an aware datetime. ``opener`` is
    injectable for fake-HTTP verification and must honor the transport safety
    contract. The built-in opener disables redirects and environment proxies.
    No broker transport is called until every request input has been validated.
    No partial result is returned when any page or bound fails.
    """
    try:
        inquiry_day, observed_at = _validate_inputs(
            inquiry_date,
            account_ref,
            token,
            appkey,
            appsecret,
            cano,
            acnt_prdt_cd,
            max_pages,
            now,
        )
        parameters = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": inquiry_day,
            "INQR_END_DT": inquiry_day,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_1": "",
            "INQR_DVSN_3": "00",
            "EXCG_ID_DVSN_CD": "KRX",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        headers = {
            "content-type": "application/json; charset=utf-8",
            "accept": "application/json",
            "authorization": "Bearer " + token,
            "appkey": appkey,
            "appsecret": appsecret,
            "tr_id": ORDER_TR_ID,
            "custtype": "P",
            "tr_cont": "",
        }
        observations = []
        identities = set()
        cursors = set()
        for page_index in range(max_pages):
            request = Request(
                VTS_BASE + ORDER_PATH + "?" + urlencode(parameters), headers=headers, method="GET"
            )
            body, rows, continuation = _read_page(request, opener)
            for raw_row in rows:
                row = _normalize_row(raw_row, inquiry_day)
                identity = (row["ord_dt"], int(row["ord_gno_brno"]), int(row["odno"]))
                if identity in identities:
                    raise ValueError("Repeated broker order identity")
                identities.add(identity)
                observations.append(row)
            if len(observations) > MAX_TOTAL_ROWS:
                raise ValueError("Total observation bound exceeded")
            if continuation in ("D", "E"):
                return {
                    "schema_version": 1,
                    "mode": "paper",
                    "live_orders_enabled": False,
                    "provider": "kis_vts_readonly_order_snapshot",
                    "market": "KRX",
                    "currency": "KRW",
                    "account_ref": account_ref,
                    "account_binding_verified": False,
                    "inquiry_date": inquiry_date,
                    "observed_at_utc": observed_at,
                    "pagination_complete": True,
                    "pages_fetched": page_index + 1,
                    "row_count": len(observations),
                    "atomic_snapshot_verified": False,
                    "economic_reconciliation_verified": False,
                    "rows": observations,
                }
            pair = (_cursor(body.get("ctx_area_fk100")), _cursor(body.get("ctx_area_nk100")))
            if pair in cursors:
                raise ValueError("Repeated continuation cursors")
            cursors.add(pair)
            parameters["CTX_AREA_FK100"], parameters["CTX_AREA_NK100"] = pair
            headers["tr_cont"] = "N"
        raise ValueError("Page bound reached before completion")
    except Exception:
        # Suppress URLs (which contain account values), raw bodies, credentials,
        # cursor values, malformed input values and upstream exception chains.
        raise ValueError("KIS VTS read-only order snapshot refused") from None


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid read-only order-snapshot arguments")


def _page_limit(value):
    if not _matches(value, r"[1-9][0-9]?", 2):
        raise ValueError("Invalid page limit")
    number = int(value)
    if number > 20:
        raise ValueError("Invalid page limit")
    return number


def _remove_owned_output(target, identity):
    try:
        current = target.lstat()
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            target.unlink()
    except OSError:
        pass


def _require_owned_output(target, identity):
    current = target.lstat()
    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
        raise ValueError("Output destination changed during collection")


def main(argv=None):
    parser = _SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("paper",))
    parser.add_argument("--allow-vts-readonly", action="store_true")
    parser.add_argument("--date", required=True, dest="inquiry_date")
    parser.add_argument("--account-ref", required=True)
    parser.add_argument("--max-pages", default=20, type=_page_limit)
    parser.add_argument(
        "--output", required=True, type=Path, help="New private JSON file in an existing directory"
    )
    try:
        args = parser.parse_args(argv)
        if not args.allow_vts_readonly:
            raise ValueError("Explicit read-only opt-in is required")
        # Resolve only the parent. O_EXCL must reject even a dangling final
        # symlink. Reserve the output before HTTP to detect permissions, invalid
        # paths, collisions and non-directory ancestors without account access.
        target = args.output.parent.resolve(strict=True) / args.output.name
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        identity = os.fstat(descriptor)
        try:
            os.fchmod(descriptor, 0o600)
            result = fetch_order_snapshot(
                args.inquiry_date,
                account_ref=args.account_ref,
                max_pages=args.max_pages,
                token=os.environ.get("KIS_VTS_ACCESS_TOKEN"),
                appkey=os.environ.get("KIS_VTS_APPKEY"),
                appsecret=os.environ.get("KIS_VTS_APPSECRET"),
                cano=os.environ.get("KIS_VTS_CANO"),
                acnt_prdt_cd=os.environ.get("KIS_VTS_ACNT_PRDT_CD"),
            )
            serialized = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
            _require_owned_output(target, identity)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                descriptor = None
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
                _require_owned_output(target, identity)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            _remove_owned_output(target, identity)
            raise
    except Exception:
        # Static diagnostics also cover argparse errors and sensitive paths.
        print("VTS read-only order snapshot refused; no broker order sent.", file=sys.stderr)
        return 2
    print("VTS read-only order snapshot created; no broker order sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
