# Durable stock-order simulation and KIS read-only inquiry

## Goal

Continue the approved stock paper work with durable partial-fill/cancel history,
restart and duplicate-delivery checks, and a bounded read-only KIS order observer.
No order transport, actual authentication or broker reconciliation is performed
by the offline journal or demonstration. Keep Python 3.10+ and standard-library
entrypoints. Existing stock/crypto cash ledgers remain separate accounting domains.

## Durable journal contract

Create `packages/core/trading_core/stock_order_journal.py` exposing:

```python
StockOrderJournal.create(path, *, account_ref)
StockOrderJournal.open_existing(path, *, account_ref, read_only=False)
journal.record(event)  # returns the resulting snapshot
journal.snapshot()     # consistent read-only full replay audit
```

`account_ref` is an opaque local alias starting with an ASCII letter, maximum
64 characters (letters/digits/._-). It does not authenticate a broker account.
Create is exclusive, mode 0600, existing parent required; existing regular files
and final symlinks are never replaced. Open-existing never creates a missing DB.
Read-only instances reject `record` before opening a write connection.

Each event has exactly `event_id`, `kind`, `client_order_id`, `data`. IDs are
bounded ASCII correlation labels (1..64, first alphanumeric, remaining ._-).
Event kinds and exact data fields:

| Kind | Data |
| --- | --- |
| reserve | order_date YYYY-MM-DD, symbol, side, quantity, limit_price, max_notional |
| submission_uncertain | empty object |
| acknowledge | broker_order_id (1..10 ASCII digits, nonzero), broker_org_no (1..5 ASCII digits) |
| fill | execution_id, quantity, price, fee_krw, tax_krw |
| cancel_requested | empty object |
| canceled | evidence_id |
| rejected | evidence_id |

Reserve validates the same KRX/KRW six-digit stock, positive integer shares,
canonical positive integer prices and gross cap as `build_order_plan`. Event
amounts are canonical integer KRW strings: price positive <=19 digits, fee/tax
nonnegative <=29 digits. Bool/float/decimal/Unicode/oversized values are rejected.
All arithmetic uses integers. This deliberately restricted model does not infer
fractional fees, instrument validity, ticks, account funds or real executions.

States are reserved, uncertain, accepted, partially_filled, cancel_pending,
filled, canceled, rejected. A different new client intent is blocked while an
unresolved order exists. An acknowledgment is required before new fills.
Uncertain submission never authorizes resend. A fill may arrive during cancel
pending; full fill wins, and a later new cancellation confirmation is refused.
Cancellation preserves already executed shares/costs; rejection requires zero
fills. Newly reported fills after terminal cancellation/rejection are refused.
Buy fills cannot exceed limit price; sell fills cannot be below it; overfill is
refused. These checks model a simulation, not handling of irrevocable real fills.

Identical event IDs are idempotent; changed payload with the same ID is refused.
Broker identities are unique per (order_date, broker_org_no, broker_order_id).
Execution identities are unique per (order_date, execution_id). A redelivered
execution with identical order/economic terms never counts twice, even with a
new event ID; changed terms fail. Duplicate delivery after terminal state must
not regress status. Distinct event IDs may record harmless duplicate-delivery
events, so event_count and execution_count need not match.

Store canonical events in SQLite WAL with synchronous FULL. Each append,
sequence/hash-chain update and committed tail metadata update is one immediate
transaction. Replay validates exact schema/configuration, contiguous sequences,
hashes, committed tail and all state transitions before reads/writes succeed.
This detects corruption/partial edits; it is not authenticated or tamper-proof
against coordinated rewriting. Config binds schema_version=1, paper mode,
account_ref, KRW and KRX. Read operations use a consistent SQLite read snapshot.

Snapshot JSON has mode=paper, live_orders_enabled=false,
provenance=synthetic_or_caller_supplied_unverified, account_ref, currency=KRW,
market=KRX, event_count, execution_count, pending_client_order_ids, blocked,
cash_delta_krw, position_deltas, orders. Orders include original terms, status,
broker identity if known, filled_quantity, unfilled_quantity, working_quantity,
gross_krw, fee_krw, tax_krw, cash_delta_krw and executions. Cash/position deltas
describe recorded fills; they are not balances, PnL or proof of actual holdings.
For terminal states working_quantity=0; unfilled_quantity remains quantity minus
filled_quantity so cancellation does not erase unexecuted shares.

## KIS observer contract

Create `tools/kis_vts_order_snapshot.py` with a bounded read-only GET collector.
Fixed VTS host, daily-inquiry path and VTTC0081R; no production host option,
token issuance, order endpoint, redirects, environment proxy or retries.
Inputs: one ISO inquiry date within the past 30 KST calendar days including today,
opaque account_ref, existing env credentials and account fields, max_pages 1..20.
CLI requires paper mode, explicit --allow-vts-readonly and a new output path.

Query all records for the selected date with explicit uppercase parameters.
First request tr_cont is empty; response M/F continues with both exact cursors
and request tr_cont=N. D/E is terminal. Missing/unknown signals, repeated cursor
pairs, missing continuation cursors, non-200/nonzero rt_cd, oversized responses
or bounds reached before termination refuse the entire snapshot. Never return
partial results as complete. VTS pages allow at most 15 rows, max 300 total.

Normalize casing with conflicting-case keys refused. Allowlist order identity,
date/time, symbol, side/order-type codes, cumulative quantity/amount observations,
remaining/canceled/rejected quantities and cancellation/original-order flags.
Require well-formed documented fields; preserve zero-padded IDs. Never emit
credentials, account values, cursor strings, contacts/IPs, free-text messages or
unneeded raw fields. Do not sum linked rows or repeated output2 summaries.
Rows with original-order links are related history; no row becomes a fill or
changes the journal. Report complete pagination separately from unverified
economic reconciliation. Account_ref is a label, not authenticated binding.

Official contract research: pinned KIS example at commit
74b0a082fa67a0be5bf53f561719c853890ed2df, daily-inquiry public portal schema.
The sample's recursive argument-order bug and mapping typo cnc_cfrm_qty are not
copied; canonical cancellation quantity is cncl_cfrm_qty. A zero remaining
quantity alone does not establish a fill, and order time is not execution time.

## CLI and demonstration

Add `tools/stock_order_journal.py`: init, record an event JSON, and read-only
status. Decode bounded JSON strictly, rejecting duplicate keys; use static
errors. Default output never contains real credentials or raw broker bodies.
Add `tools/stock_order_lifecycle_smoke.py` and a clearly synthetic bundled event
fixture. Demonstrate uncertain submission blocking, restart, partial fill,
duplicate delivery, cancel/fill race, cancellation preserving filled costs,
sell fill, replay and audit. Use a new SQLite DB in a temporary directory;
optional new output directory may retain the synthetic DB/report for review.

## Verification

Meaningful regressions: real SQLite reopen/concurrent writers/rollback and a
child-process crash before COMMIT; event/fill duplicates; deleted/changed tail;
wrong account; read-only mutations; exact huge integer costs; invalid lifecycle.
Observer tests exercise request/page boundaries, malformed/private responses,
no retries, cursor loops, completion limits and CLI secret/output preservation.
Run existing full pytest, socket-forbidden suite with backtest, compileall,
fatal lint, independent review, then exact-head GitHub CI after publication.
