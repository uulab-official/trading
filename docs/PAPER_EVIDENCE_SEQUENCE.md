# Ordered paper evidence sequence (v30)

## Design
Read 2–32 explicit v28 evidence files in caller-supplied revision order. Reuse
v29 comparison on every adjacent pair AND the first/last captures. Do not sort
by filenames, capture clocks or observed profitability. Refuse any conflicting
intermediate observation; do not emit partial successes or drop duplicate files.
Capture each argument once, validate that exact byte stream and never reopen it
for comparison. A 64 MiB cumulative input budget bounds the operation; the
existing individual-file bounds still apply. Retain only baseline and adjacent
captures, not all full account payloads.

The report contains compact per-interval economic deltas, newly recorded fills,
new orders, observed acknowledgement recoveries and control changes. Overall
cash, fees and position changes come from full endpoint comparison, NOT sums of
truncated display arrays. Exact interval cash/fee sums must reconcile to endpoint
differences. Repeated snapshots are explicit zero-change intervals; they never
charge another fee or invent a fill. Recoveries observed only in an intermediate
snapshot remain in interval statistics even when the endpoint has a new order.

This is captured historical evidence, not a live feed, continuous audit trail,
realized P&L, a portfolio return or authorization to trade. Inputs and output
contain private identifiers. No original DB, network, restore, order action,
new scheduler or browser endpoint is involved.

## CLI
`python tools/paper_evidence.py sequence BEFORE MIDDLE AFTER`

Success writes one JSON document to stdout (exit 0). An invalid input/count/budget
returns fixed error JSON and exit 2. A valid but incompatible adjacent pair returns
exit 5 with its existing fixed reason and 1-based interval index; no partial report
or file paths. Interrupted calls preserve the existing exit-130 behavior.

## Validation targets
Actual PaperTradingSession, SQLite export, single-read replacement, lost-ack
recovery, repeated captures, invalid middle observations, multiple assets, KRW,
finite-precision residuals, missing marks, output privacy, invocation limits and
compatibility of the original two-file comparison and export/verify commands.

## Output interpretation
`overall` is the full first-to-last comparison. Its detailed fill/position/order
arrays retain the existing 100-item cap, while counts use the complete inventory.
`interval_totals.observed_fill_confirmations` counts recoveries visible between
adjacent captures, including an order absent from the very first capture. It can
therefore differ from `overall.orders.confirmed_existing_fills`, whose baseline
is only that first capture. `changed_order_observations` counts observed changes,
not distinct orders. `control_change_intervals` counts intervals, not unrecorded
mode transitions. Repeated captures are reported rather than discarded.

The 64 MiB accepted-input budget is checked after each existing bounded private
read/validation; one read can cross the limit before refusal. Account validation
replays bounded stored history, so large accounts are not a high-frequency feed.

Commands do not schedule future observations or monitor the original account.
For example, create separate exports before and after an explicitly run paper
operation, then pass those filenames in saved-revision order. Do not infer live
readiness, strategy quality or realized returns from a successful sequence.

## Checks
```bash
python -m unittest discover -s tests -p 'test_order_paper_sequence.py' -v
python -m unittest discover -s tests -p 'test_order_paper_evidence*.py' -v
```
