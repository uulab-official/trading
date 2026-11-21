"""Synthetic KIS observation -> offline review -> backup -> new recovery candidate."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal
from stock_order_review import review_orders
from tools.kis_vts_order_snapshot import ORDER_PATH, ORDER_TR_ID, fetch_order_snapshot
from tools.stock_order_backup import create_backup, restore_candidate, verify_backup
from tools.stock_order_journal import SafeArgumentParser, read_bounded_json
from tools.stock_order_review import render_review_html, write_reports

FIXTURE = ROOT / "config/examples/synthetic_stock_order_events.json"
KST = timezone(timedelta(hours=9))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _fake_rows(day):
    # Expected cumulative figures are stated independently of the journal reducer.
    rows = []
    for identity, side, quantity, price, gross, average, canceled in (
            ("0000000001", "02", "10", "10000", "49700", "9940", "5"),
            ("0000000002", "01", "5", "11000", "55200", "11040", "0")):
        rows.append({
            "ord_dt": day.replace("-", ""), "ord_gno_brno": "06010", "odno": identity,
            "orgn_odno": "0000000000", "pdno": "005930", "sll_buy_dvsn_cd": side,
            "ord_dvsn_cd": "00", "ord_qty": quantity, "ord_unpr": price,
            "tot_ccld_qty": "5", "tot_ccld_amt": gross, "avg_prvs": average,
            "rmn_qty": "0", "cncl_cfrm_qty": canceled, "rjct_qty": "0",
            "cncl_yn": "Y" if canceled == "5" else "N", "ord_tmd": "091000",
            "excg_id_dvsn_Cd": "KRX", "ctac_tlno": "SYNTHETIC-PRIVATE-CONTACT",
            "inqr_ip_addr": "SYNTHETIC-PRIVATE-IP", "ordr_empno": "SYNTHETIC-PRIVATE-EMPLOYEE",
        })
    return rows


def _fake_observations(day, clock):
    calls = []
    body = json.dumps({"rt_cd": "0", "output1": _fake_rows(day),
                       "output2": {}, "msg1": "SYNTHETIC-PRIVATE-MESSAGE"}).encode("utf-8")

    class SyntheticResponse:
        status = 200
        headers = {"tr_cont": "D", "tr_id": ORDER_TR_ID}

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def read(self, limit):
            return body[:limit]

    def fake_http(request, *, timeout):
        parsed = urlsplit(request.full_url)
        if (request.get_method() != "GET" or parsed.hostname != "openapivts.koreainvestment.com"
                or parsed.path != ORDER_PATH or timeout != 10):
            raise ValueError("Unexpected synthetic inquiry request")
        calls.append(True)
        return SyntheticResponse()

    observations = fetch_order_snapshot(
        day, account_ref="demo-stock-orders", token="SYNTHETIC-TOKEN",
        appkey="SYNTHETIC-APPKEY", appsecret="SYNTHETIC-APPSECRET",
        cano="12345678", acnt_prdt_cd="01", opener=fake_http, now=lambda: clock,
    )
    if len(calls) != 1 or "SYNTHETIC-PRIVATE" in _json(observations):
        raise ValueError("Synthetic inquiry filtering or request count disagrees")
    return observations, len(calls)


def _backup_hashes(directory):
    if {item.name for item in directory.iterdir()} != {"journal.sqlite", "manifest.json"}:
        raise ValueError("Unexpected synthetic backup contents")
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("journal.sqlite", "manifest.json")}


def _exercise(directory, clock, retained):
    fixture = read_bounded_json(FIXTURE)
    if (type(fixture) is not dict or fixture.get("mode") != "paper"
            or fixture.get("provenance") != "synthetic_stock_order_events"
            or fixture.get("account_ref") != "demo-stock-orders"
            or type(fixture.get("events")) is not list or len(fixture["events"]) != 14):
        raise ValueError("Bundled synthetic fixture is unavailable")
    day = clock.astimezone(KST).date().isoformat()
    events = deepcopy(fixture["events"])
    for event in events:
        if event["kind"] == "reserve":
            event["data"]["order_date"] = day
    journal = StockOrderJournal.create(directory / "journal.sqlite", account_ref="demo-stock-orders")
    for event in events:
        journal.record(event)
    original = journal.snapshot()
    if (original["event_count"] != 14 or original["execution_count"] != 4
            or original["cash_delta_krw"] != "5285" or original["position_deltas"] != {}
            or original["blocked"]):
        raise ValueError("Synthetic journal disagrees with expected economics")
    observations, calls = _fake_observations(day, clock)
    review = review_orders(journal, observations, now=clock)
    if (review["comparison_status"] != "observations_equal" or review["needs_attention"]
            or review["summary"]["matched_order_count"] != 2 or review["safe_to_submit"]):
        raise ValueError("Equal synthetic observations were not reviewed as expected")
    changed = deepcopy(observations)
    changed["rows"][0]["tot_ccld_qty"] = "4"
    changed["rows"][0]["tot_ccld_amt"] = "39700"
    difference = review_orders(journal, changed, now=clock)
    changed_fields = {item["field"] for item in difference["orders"][0]["differences"]}
    if (difference["comparison_status"] != "needs_review" or not difference["needs_attention"]
            or changed_fields != {"filled_quantity", "gross_krw"}):
        raise ValueError("Deliberate synthetic discrepancy was not found")
    if journal.snapshot() != original:
        raise ValueError("Observation review changed journal events")
    backup = directory / "backup"
    created = create_backup(directory / "journal.sqlite", backup, account_ref="demo-stock-orders")
    verified = verify_backup(backup, account_ref="demo-stock-orders")
    before = _backup_hashes(backup)
    restored = restore_candidate(backup, directory / "restored.sqlite", account_ref="demo-stock-orders")
    if any(result["snapshot"] != original for result in (created, verified, restored)):
        raise ValueError("Backup or recovery candidate changed synthetic journal state")
    if _backup_hashes(backup) != before:
        raise ValueError("Recovery changed the source backup")
    candidate = StockOrderJournal.open_existing(directory / "restored.sqlite", account_ref="demo-stock-orders")
    for event in events:
        candidate.record(event)
    if candidate.snapshot() != original:
        raise ValueError("Restored duplicate delivery changed journal state")
    if journal.snapshot() != original:
        raise ValueError("Recovery trial changed the source journal")
    summary = {
        "status": "synthetic_stock_order_review_verified", "mode": "paper",
        "live_orders_enabled": False, "provenance": "synthetic_stock_order_review",
        "account_binding_verified": False, "economic_reconciliation_verified": False,
        "lifecycle_confirmation_verified": False, "safe_to_submit": False,
        "events": 14, "executions": 4, "cash_delta_krw": "5285", "position_deltas": {},
        "actual_broker_requests": 0, "fake_http_requests": calls, "artifacts_retained": retained,
        "checks": {"observations_equal": True, "deliberate_difference_detected": True,
                   "private_response_fields_filtered": True, "source_journal_unchanged": True,
                   "backup_verified": True, "restored_state_equal": True,
                   "source_backup_unchanged": True, "restored_duplicate_replay_idempotent": True},
        "warning": "Synthetic prices, orders and observations; no authenticated account or execution evidence",
    }
    review["example_provenance"] = "synthetic_stock_order_review"
    difference["example_provenance"] = "synthetic_stock_order_review"
    write_reports([
        (directory / "synthetic-observations.json", _json(observations)),
        (directory / "review.json", _json(review)),
        (directory / "review.html", render_review_html(review)),
        (directory / "review-difference.json", _json(difference)),
        (directory / "review-difference.html", render_review_html(difference)),
        (directory / "smoke.json", _json(summary)),
    ])
    return summary


def run_smoke(output_dir=None, *, now=None):
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Synthetic trial clock must be aware")
    clock = clock.astimezone(timezone.utc)
    if output_dir is None:
        with TemporaryDirectory(prefix="trading-stock-review-") as temporary:
            return _exercise(Path(temporary), clock, False)
    destination = Path(output_dir)
    os.mkdir(destination, 0o700)
    return _exercise(destination, clock, True)


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-dir", type=Path,
                        help="Optional new private directory in an existing parent")
    try:
        args = parser.parse_args(argv)
        result = run_smoke(args.output_dir)
    except Exception:
        print("Synthetic stock review failed. No broker request sent. Retained evidence may be incomplete.",
              file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
