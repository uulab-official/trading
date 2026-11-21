"""Audit a paper journal and compare saved, unverified order observations.

Equality covers only the explicitly compared cumulative fields. It does not
authenticate an account, confirm individual executions or lifecycle state,
reconcile fees or balances, or authorize submission. No observation is applied
to the journal, and related order histories are never summed.
"""

import re
from datetime import date, datetime, timedelta, timezone

if __package__:
    from .stock_order_journal import StockOrderJournal
else:  # Operator tools must also work under Python -S without package startup.
    from stock_order_journal import StockOrderJournal


_KST = timezone(timedelta(hours=9))
_OBSERVATION_KEYS = frozenset(
    (
        "schema_version",
        "mode",
        "live_orders_enabled",
        "provider",
        "market",
        "currency",
        "account_ref",
        "account_binding_verified",
        "inquiry_date",
        "observed_at_utc",
        "pagination_complete",
        "pages_fetched",
        "row_count",
        "atomic_snapshot_verified",
        "economic_reconciliation_verified",
        "rows",
    )
)
_FIXED_METADATA = {
    "schema_version": 1,
    "mode": "paper",
    "live_orders_enabled": False,
    "provider": "kis_vts_readonly_order_snapshot",
    "market": "KRX",
    "currency": "KRW",
    "account_binding_verified": False,
    "pagination_complete": True,
    "atomic_snapshot_verified": False,
    "economic_reconciliation_verified": False,
}
_ROW_FIELDS = (
    "ord_dt",
    "ord_gno_brno",
    "odno",
    "orgn_odno",
    "pdno",
    "sll_buy_dvsn_cd",
    "ord_dvsn_cd",
    "ord_qty",
    "tot_ccld_qty",
    "rmn_qty",
    "cncl_cfrm_qty",
    "rjct_qty",
    "ord_unpr",
    "tot_ccld_amt",
    "avg_prvs",
    "cncl_yn",
    "ord_tmd",
)
_ROW_KEYS = frozenset(_ROW_FIELDS)


def _matches(value, pattern, maximum):
    return (
        type(value) is str
        and 0 < len(value) <= maximum
        and re.fullmatch(pattern, value) is not None
    )


def _require_number(value, maximum, *, fractional=False):
    pattern = r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?" if fractional else r"0|[1-9][0-9]*"
    if not _matches(value, pattern, maximum):
        raise ValueError("Invalid canonical observation number")


def _validate_row(supplied, inquiry_day):
    if type(supplied) is not dict or set(supplied) not in (
        _ROW_KEYS,
        _ROW_KEYS | {"excg_id_dvsn_cd"},
    ):
        raise ValueError("Invalid observation row fields")
    row = {field: supplied[field] for field in _ROW_FIELDS}
    if type(row["ord_dt"]) is not str or row["ord_dt"] != inquiry_day:
        raise ValueError("Observation row is outside the inquiry date")
    if not _matches(row["ord_gno_brno"], r"[0-9]+", 5):
        raise ValueError("Invalid observation branch")
    if not _matches(row["odno"], r"[0-9]+", 10) or int(row["odno"]) == 0:
        raise ValueError("Invalid observation order identity")
    original = row["orgn_odno"]
    if type(original) is not str or (original != "" and not _matches(original, r"[0-9]+", 10)):
        raise ValueError("Invalid original order reference")
    if not _matches(row["pdno"], r"[0-9]{6}", 6):
        raise ValueError("Invalid observation symbol")
    if type(row["sll_buy_dvsn_cd"]) is not str or row["sll_buy_dvsn_cd"] not in ("01", "02"):
        raise ValueError("Invalid observation side")
    if not _matches(row["ord_dvsn_cd"], r"[0-9]{2}", 2):
        raise ValueError("Invalid observation order division")
    for field in ("ord_qty", "tot_ccld_qty", "rmn_qty", "cncl_cfrm_qty", "rjct_qty"):
        _require_number(row[field], 10)
    for field in ("ord_unpr", "tot_ccld_amt"):
        _require_number(row[field], 19)
    _require_number(row["avg_prvs"], 19, fractional=True)
    if type(row["cncl_yn"]) is not str or row["cncl_yn"] not in ("", "Y", "N"):
        raise ValueError("Invalid observation cancellation flag")
    clock = row["ord_tmd"]
    if (
        not _matches(clock, r"[0-9]{6}", 6)
        or int(clock[:2]) > 23
        or int(clock[2:4]) > 59
        or int(clock[4:]) > 59
    ):
        raise ValueError("Invalid observation order time")
    if "excg_id_dvsn_cd" in supplied:
        exchange = supplied["excg_id_dvsn_cd"]
        if type(exchange) is not str or exchange != "KRX":
            raise ValueError("Invalid observation exchange")
        row["excg_id_dvsn_cd"] = "KRX"
    return row


def _row_identity(row):
    return row["ord_dt"], int(row["ord_gno_brno"]), int(row["odno"])


def _validate_observations(supplied, account_ref):
    if type(supplied) is not dict or set(supplied) != _OBSERVATION_KEYS:
        raise ValueError("Invalid observation document fields")
    for field, expected in _FIXED_METADATA.items():
        value = supplied[field]
        if type(value) is not type(expected) or value != expected:
            raise ValueError("Unsupported observation metadata")
    if type(supplied["account_ref"]) is not str or supplied["account_ref"] != account_ref:
        raise ValueError("Observation account alias does not match")
    inquiry = supplied["inquiry_date"]
    if not _matches(inquiry, r"[0-9]{4}-[0-9]{2}-[0-9]{2}", 10):
        raise ValueError("Invalid observation inquiry date")
    inquiry_date = date.fromisoformat(inquiry)
    timestamp = supplied["observed_at_utc"]
    if not _matches(
        timestamp,
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:" r"[0-9]{2}(?:\.[0-9]{6})?\+00:00",
        32,
    ):
        raise ValueError("Invalid observation timestamp")
    observed = datetime.fromisoformat(timestamp)
    if observed.isoformat() != timestamp or inquiry_date > observed.astimezone(_KST).date():
        raise ValueError("Invalid observation date or timestamp")
    pages, count, supplied_rows = supplied["pages_fetched"], supplied["row_count"], supplied["rows"]
    if type(pages) is not int or not 1 <= pages <= 20:
        raise ValueError("Invalid observation page count")
    if type(count) is not int or not 0 <= count <= 300 or count > pages * 15:
        raise ValueError("Invalid observation row count")
    if type(supplied_rows) is not list or len(supplied_rows) != count:
        raise ValueError("Inconsistent observation rows")
    rows, identities = [], set()
    for supplied_row in supplied_rows:
        row = _validate_row(supplied_row, inquiry.replace("-", ""))
        identity = _row_identity(row)
        if identity in identities:
            raise ValueError("Duplicate numeric observation order identity")
        identities.add(identity)
        rows.append(row)
    return {
        "inquiry_date": inquiry,
        "observed_at_utc": timestamp,
        "pages_fetched": pages,
        "row_count": count,
        "rows": sorted(rows, key=_row_identity),
    }, observed


def _differences(local, observed):
    # Quantities are bounded integers; money stays exact canonical KRW strings.
    fields = (
        ("symbol", local["symbol"], observed["pdno"]),
        ("side", local["side"], "buy" if observed["sll_buy_dvsn_cd"] == "02" else "sell"),
        ("order_division", "00", observed["ord_dvsn_cd"]),
        ("quantity", local["quantity"], int(observed["ord_qty"])),
        ("limit_price", local["limit_price"], observed["ord_unpr"]),
        ("filled_quantity", local["filled_quantity"], int(observed["tot_ccld_qty"])),
        ("gross_krw", local["gross_krw"], observed["tot_ccld_amt"]),
        ("working_quantity", local["working_quantity"], int(observed["rmn_qty"])),
    )
    return [
        {"field": field, "local": expected, "observed": actual}
        for field, expected, actual in fields
        if expected != actual
    ]


def _build_review(journal, observations, now, max_age_seconds):
    if type(journal) is not StockOrderJournal:
        raise ValueError("An actual stock-order journal is required")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
        raise ValueError("Invalid observation freshness bound")
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("An aware review clock is required")
    generated = clock.astimezone(timezone.utc)
    snapshot = journal.snapshot()
    summary = None
    observed_rows, related, findings, matched = {}, set(), [], set()

    def finding(code, local=None, observed=None):
        findings.append(
            {
                "code": code,
                "client_order_id": local["client_order_id"] if local is not None else None,
                "broker_order_id": (
                    local["broker_order_id"]
                    if local is not None
                    else observed["odno"] if observed is not None else None
                ),
            }
        )

    if observations is not None:
        document, observed_at = _validate_observations(observations, snapshot["account_ref"])
        age = (generated - observed_at).total_seconds()
        freshness = "future" if age < 0 else "stale" if age > max_age_seconds else "fresh"
        summary = {
            field: document[field]
            for field in (
                "inquiry_date",
                "observed_at_utc",
                "pages_fetched",
                "row_count",
            )
        }
        summary.update(age_seconds=age, freshness=freshness)
        if freshness != "fresh":
            finding("observation_" + freshness)
        for row in document["rows"]:
            identity = _row_identity(row)
            observed_rows[identity] = row
            original = int(row["orgn_odno"] or "0")
            if original:
                # References are scoped to this date and branch. They identify
                # reviewable related history, never additional executions.
                related.add(identity)
                related.add((identity[0], identity[1], original))

    orders, scoped_count, different_count = [], 0, 0
    pending = set(snapshot["pending_client_order_ids"])
    for local in snapshot["orders"]:
        entry = {"local": local, "observed": None, "comparison": "not_supplied", "differences": []}
        orders.append(entry)
        if local["client_order_id"] in pending:
            finding("unresolved_local_order", local)
        if summary is None:
            continue
        if local["order_date"] != summary["inquiry_date"]:
            entry["comparison"] = "out_of_scope"
            continue
        scoped_count += 1
        if local["broker_order_id"] is None:
            entry["comparison"] = "not_acknowledged"
            finding("local_order_not_acknowledged", local)
            continue
        identity = (
            local["order_date"].replace("-", ""),
            int(local["broker_org_no"]),
            int(local["broker_order_id"]),
        )
        row = observed_rows.get(identity)
        if row is None:
            entry["comparison"] = "not_observed"
            finding("local_order_not_observed", local)
        else:
            matched.add(identity)
            entry["observed"] = row
            entry["differences"] = _differences(local, row)
            if entry["differences"]:
                different_count += 1
                entry["comparison"] = "different"
                finding("order_fields_differ", local)
            else:
                entry["comparison"] = (
                    "related_history" if identity in related else "observations_equal"
                )
        if identity in related:
            finding("related_order_history", local)

    unmatched = [row for identity, row in observed_rows.items() if identity not in matched]
    for row in unmatched:
        finding("unmatched_observation", observed=row)
        if _row_identity(row) in related:
            finding("related_order_history", observed=row)
    comparison = "not_supplied"
    if summary is not None:
        comparison = (
            "needs_review"
            if any(item["code"] != "unresolved_local_order" for item in findings)
            else "observations_equal"
        )
    return {
        "schema_version": 1,
        "kind": "stock_order_review",
        "mode": "paper",
        "live_orders_enabled": False,
        "account_ref": snapshot["account_ref"],
        "generated_at_utc": generated.isoformat(),
        "account_binding_verified": False,
        "economic_reconciliation_verified": False,
        "lifecycle_confirmation_verified": False,
        "safe_to_submit": False,
        "comparison_status": comparison,
        "needs_attention": bool(findings),
        "journal_blocked": snapshot["blocked"],
        "journal": snapshot,
        "observation_summary": summary,
        "orders": orders,
        "unmatched_observations": unmatched,
        "findings": findings,
        "summary": {
            "order_count": len(orders),
            "scoped_order_count": scoped_count,
            "matched_order_count": len(matched),
            "different_order_count": different_count,
            "unmatched_observation_count": len(unmatched),
            "unresolved_order_count": len(pending),
            "findings_count": len(findings),
        },
    }


def review_orders(journal, observations=None, *, now=None, max_age_seconds=300):
    """Return one complete audited report, or raise a static ``ValueError``.

    ``journal`` must be a ``StockOrderJournal``. ``observations`` is an optional
    already-decoded, normalized KIS VTS observation document, not a raw response.
    Callers decoding JSON must reject duplicate keys and nonfinite numbers.
    ``now`` is an aware datetime; observation age over ``max_age_seconds`` or
    any future timestamp requires review. Historical inquiries remain valid.

    Journal-only monitoring uses ``not_supplied``. Unresolved local orders
    always require attention, including orders outside the inquiry date and
    orders whose compared fields are equal. Every safety flag remains false.
    """
    try:
        return _build_review(journal, observations, now, max_age_seconds)
    except Exception:
        # Never echo untrusted payloads, file paths or upstream error messages.
        raise ValueError("Stock-order review refused") from None
