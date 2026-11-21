"""Run a synthetic stock-order lifecycle with durable SQLite and zero broker calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/core/trading_core"))
from stock_order_journal import StockOrderJournal
from tools.stock_order_journal import SafeArgumentParser, read_bounded_json, write_new_json

FIXTURE = ROOT / "config/examples/synthetic_stock_order_events.json"


def _must_refuse_without_change(journal, event):
    before = journal.snapshot()
    try:
        journal.record(event)
    except ValueError:
        if journal.snapshot() != before:
            raise ValueError("Refused event changed journal state")
        return True
    raise ValueError("Invalid lifecycle event was unexpectedly accepted")


def _exercise(directory, fixture, retained):
    path = directory / "journal.sqlite"
    account = fixture["account_ref"]
    journal = StockOrderJournal.create(path, account_ref=account)
    events = fixture["events"]
    trace = []
    checks = {}
    for event in events:
        snapshot = journal.record(event)
        # Reopen after every committed step, including the uncertain state.
        journal = StockOrderJournal.open_existing(path, account_ref=account)
        if journal.snapshot() != snapshot:
            raise ValueError("Reopened journal diverged from the committed snapshot")
        order = next(row for row in snapshot["orders"]
                     if row["client_order_id"] == event["client_order_id"])
        trace.append({
            "event_id": event["event_id"], "kind": event["kind"],
            "client_order_id": event["client_order_id"], "status": order["status"],
            "filled_quantity": order["filled_quantity"],
            "working_quantity": order["working_quantity"],
            "cash_delta_krw": snapshot["cash_delta_krw"],
            "execution_count": snapshot["execution_count"],
        })
        if event["event_id"] in ("buy.uncertain", "buy.ack"):
            conflicting = deepcopy(events[0])
            conflicting["event_id"] = "probe-conflicting-reserve"
            conflicting["client_order_id"] = "blocked-new-intent"
            checks["uncertain_submission_blocked" if event["kind"] == "submission_uncertain"
                   else "accepted_order_blocks_new_intent"] = _must_refuse_without_change(journal, conflicting)
        if event["event_id"] == "buy.fill.1":
            overfill = deepcopy(event)
            overfill["event_id"] = "probe-overfill"
            overfill["data"].update(execution_id="probe-overfill", quantity=8)
            checks["overfill_refused"] = _must_refuse_without_change(journal, overfill)
            if snapshot["cash_delta_krw"] != "-29730" or snapshot["position_deltas"] != {"005930": 3}:
                raise ValueError("First partial fill did not retain exact costs and shares")
        if event["event_id"] == "buy.cancel.confirm":
            late = deepcopy(events[3])
            late["event_id"] = "probe-new-late-fill"
            late["data"].update(execution_id="probe-new-late-fill", quantity=1)
            checks["late_fill_after_cancel_refused"] = _must_refuse_without_change(journal, late)
            if (order["filled_quantity"], order["unfilled_quantity"], order["working_quantity"]) != (5, 5, 0):
                raise ValueError("Cancellation erased prior fills or left working shares")
        if event["event_id"] == "sell.fill.2":
            canceled = {
                "event_id": "probe-cancel-after-full-fill", "kind": "canceled",
                "client_order_id": "stock-sell-001", "data": {"evidence_id": "sim-too-late"},
            }
            checks["cancel_after_full_fill_refused"] = _must_refuse_without_change(journal, canceled)

    final = journal.snapshot()
    # Re-deliver the entire fixture; old semantic events must not regress state.
    for event in events:
        journal.record(event)
    if journal.snapshot() != final:
        raise ValueError("Replay duplicated events or execution economics")
    checks["duplicate_replay_idempotent"] = True
    checks["reopen_after_each_event"] = True
    reader = StockOrderJournal.open_existing(path, account_ref=account, read_only=True)
    if reader.snapshot() != final:
        raise ValueError("Read-only replay disagrees with the writer")
    try:
        reader.record(events[0])
    except PermissionError:
        checks["read_only_write_refused"] = True
    else:
        raise ValueError("Read-only journal accepted a write")

    states = {row["client_order_id"]: row["status"] for row in final["orders"]}
    if (final["event_count"] != 14 or final["execution_count"] != 4
            or final["cash_delta_krw"] != "5285" or final["position_deltas"] != {}
            or final["blocked"] or states != {"stock-buy-001": "canceled", "stock-sell-001": "filled"}):
        raise ValueError("Synthetic lifecycle did not match its independent expected result")
    summary = {
        "status": "synthetic_stock_order_lifecycle_verified", "mode": "paper",
        "live_orders_enabled": False, "provenance": "synthetic_stock_order_events",
        "recorded_events": final["event_count"], "executions": final["execution_count"],
        "cash_delta_krw": final["cash_delta_krw"], "position_deltas": final["position_deltas"],
        "order_states": states, "checks": checks, "artifacts_retained": retained,
        "actual_broker_requests": 0,
        "warning": "Synthetic events; deltas are not account balances or investment performance",
    }
    return {"summary": summary, "snapshot": final, "trace": trace}


def run_smoke(output_dir=None):
    fixture = read_bounded_json(FIXTURE)
    if (type(fixture) is not dict or fixture.get("schema_version") != 1
            or fixture.get("mode") != "paper"
            or fixture.get("provenance") != "synthetic_stock_order_events"
            or fixture.get("account_ref") != "demo-stock-orders"
            or type(fixture.get("events")) is not list or len(fixture["events"]) != 14):
        raise ValueError("Expected the bundled synthetic stock-order scenario")
    if output_dir is None:
        with TemporaryDirectory(prefix="trading-stock-order-lifecycle-") as temporary:
            return _exercise(Path(temporary), fixture, False)
    destination = Path(output_dir)
    # Exclusive private directory; retain any partial evidence after later failure.
    os.mkdir(destination, 0o700)
    report = _exercise(destination, fixture, True)
    write_new_json(destination / "report.json", report)
    return report


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--output-dir", type=Path,
                        help="Optional new directory in an existing parent for synthetic DB/report")
    try:
        args = parser.parse_args(argv)
        result = run_smoke(args.output_dir)
    except Exception:
        print("Synthetic stock lifecycle failed; no broker order sent. Retained output may be incomplete.",
              file=sys.stderr)
        return 2
    print(json.dumps(result["summary"], sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
