"""Pure, offline KIS VTS limit-order templates and unverified ACK classification.

Nothing in this module reads credentials, opens a connection, submits an order,
or reconciles a ledger. A syntactically valid caller-supplied acknowledgment is
neither authenticated broker evidence nor evidence of an executed fill.
"""

import re

VTS_BASE = "https://openapivts.koreainvestment.com:29443"
ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"

# Application input bounds, not promises of broker/instrument/account limits.
MAX_QUANTITY = 9_999_999_999
MAX_PRICE_DIGITS = 19
MAX_NOTIONAL_DIGITS = 29
MAX_CLIENT_ORDER_ID_LENGTH = 64


def _positive_krw_integer(value, *, max_digits, label):
    if (
        type(value) is not str
        or not 1 <= len(value) <= max_digits
        or re.fullmatch(r"[1-9][0-9]*", value) is None
    ):
        raise ValueError(label + " must be a bounded canonical positive KRW integer string")
    return int(value)


def build_order_plan(
    *,
    client_order_id,
    symbol,
    side,
    quantity,
    limit_price,
    max_notional,
    market="KRX",
    currency="KRW",
):
    """Prepare an unsigned request template without consulting external state.

    ``max_notional`` caps exact gross ``quantity * limit_price`` in KRW. Fees,
    taxes, reserves, eligibility, price ticks and available funds are not checked.
    ``client_order_id`` is a local label and is never a broker idempotency key.
    """
    if (
        type(client_order_id) is not str
        or not 1 <= len(client_order_id) <= MAX_CLIENT_ORDER_ID_LENGTH
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", client_order_id) is None
    ):
        raise ValueError("Client order ID must be a bounded local ASCII correlation label")
    if type(symbol) is not str or re.fullmatch(r"[0-9]{6}", symbol) is None:
        raise ValueError("Stock symbol must contain exactly six ASCII digits")
    if type(side) is not str or side not in ("buy", "sell"):
        raise ValueError("Only buy and general sell are supported")
    if type(market) is not str or market != "KRX":
        raise ValueError("Only KRX order preparation is supported")
    if type(currency) is not str or currency != "KRW":
        raise ValueError("Only KRW order preparation is supported")
    if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
        raise ValueError("Quantity must be a bounded positive integer share count")
    price_value = _positive_krw_integer(
        limit_price,
        max_digits=MAX_PRICE_DIGITS,
        label="Limit price",
    )
    budget_value = _positive_krw_integer(
        max_notional,
        max_digits=MAX_NOTIONAL_DIGITS,
        label="Maximum notional",
    )
    notional = quantity * price_value
    if notional > budget_value:
        raise ValueError("Gross order notional exceeds the explicit per-order maximum")

    return {
        "mode": "paper",
        "provider": "kis_vts",
        "submitted": False,
        "live_orders_enabled": False,
        "client_order_id": client_order_id,
        "client_order_id_scope": "local_correlation_only",
        "order": {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "limit_price": limit_price,
            "max_notional": max_notional,
            "notional": str(notional),
            "market": market,
            "currency": currency,
        },
        "request_template": {
            "method": "POST",
            "base_url": VTS_BASE,
            "path": ORDER_PATH,
            "headers": {
                "content-type": "application/json; charset=utf-8",
                "authorization": "Bearer ${KIS_VTS_ACCESS_TOKEN}",
                "appkey": "${KIS_VTS_APPKEY}",
                "appsecret": "${KIS_VTS_APPSECRET}",
                "tr_id": "VTTC0012U" if side == "buy" else "VTTC0011U",
                "custtype": "P",
            },
            "body": {
                "CANO": "${KIS_VTS_CANO}",
                "ACNT_PRDT_CD": "${KIS_VTS_ACNT_PRDT_CD}",
                "PDNO": symbol,
                "ORD_DVSN": "00",
                "ORD_QTY": str(quantity),
                "ORD_UNPR": limit_price,
                "SLL_TYPE": "01" if side == "sell" else "",
                "EXCG_ID_DVSN_CD": "KRX",
                "CNDT_PRIC": "",
            },
        },
    }


def _bounded_digits(value, maximum):
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and re.fullmatch(r"[0-9]+", value) is not None
    )


def _valid_acknowledgment(output):
    if type(output) is not dict:
        return False
    # Canonical response keys are uppercase. Do not normalize conflicting aliases.
    if any(key in output for key in ("odno", "krx_fwdg_ord_orgno", "ord_tmd")):
        return False
    order_id = output.get("ODNO")
    organization = output.get("KRX_FWDG_ORD_ORGNO")
    order_time = output.get("ORD_TMD")
    if not _bounded_digits(order_id, 10) or int(order_id) == 0:
        return False
    if not _bounded_digits(organization, 5):
        return False
    if not _bounded_digits(order_time, 6) or len(order_time) != 6:
        return False
    return int(order_time[:2]) < 24 and int(order_time[2:4]) < 60 and int(order_time[4:]) < 60


def classify_order_response(payload, *, http_status):
    """Classify caller-supplied parsed JSON; never authenticate or reconcile it.

    Every result leaves fill state unknown and automatic retry forbidden. The
    reported ``business_rejected`` state is not independent proof of no fill.
    The caller must decode JSON strictly; duplicate JSON keys are not recoverable
    after a normal decoder has collapsed them into a dictionary.
    """
    result = {
        "status": "unknown",
        "fill_state": "unknown",
        "verified_fill": False,
        "safe_to_retry": False,
        "response_source": "caller_supplied_unverified_response",
    }
    if type(http_status) is not int or http_status != 200 or type(payload) is not dict:
        return result
    rt_cd = payload.get("rt_cd")
    if type(rt_cd) is not str:
        return result
    if rt_cd == "1":
        result["status"] = "business_rejected"
        message_code = payload.get("msg_cd")
        if (
            type(message_code) is str
            and 1 <= len(message_code) <= 8
            and re.fullmatch(r"[A-Z0-9]+", message_code) is not None
        ):
            result["msg_cd"] = message_code
        return result
    if rt_cd != "0":
        return result
    output = payload.get("output")
    if _valid_acknowledgment(output):
        result["status"] = "accepted_acknowledgment"
        result["acknowledgment"] = {
            "order_id": output["ODNO"],
            "forwarding_org_no": output["KRX_FWDG_ORD_ORGNO"],
            "order_time": output["ORD_TMD"],
        }
    return result
