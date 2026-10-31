# SQLite paper order-admission journal, schema 2

`packages/core/order_journal.py` persists immutable reservation identity and order-state audit records in one SQLite transaction. `BEGIN IMMEDIATE` serializes competing reservation writers; each cooperating writer checks the same capacity inside that transaction. One journal has one reservation currency. Decimal inputs are finite, nonnegative (positive for a new notional), at most 1e18, with at most 18 fractional places; aggregation uses an explicit precision context.

## Lifecycle

`reserve` -> `mark_submitting` -> `confirm_accepted` -> `request_cancel` -> `confirm_cancelled`. A submission/cancel timeout uses `mark_unknown`, preserving reserved capacity. `confirm_rejected` requires a definitive rejection, never just a timeout. These methods record facts supplied by a trusted caller; they do not query or call any broker.

Confirmation replay is idempotent; conflicting payload, original notional or venue identifier is rejected. Repeating `mark_submitting` is intentionally forbidden: no blind resend. Confirmed cancellation/rejection releases capacity but retains the original amount. A same-ID replay returns the terminal state, not a new order. Audit insertion failure rolls back the corresponding order change.

## Migration

Opening an old journal through the writer adds `original_notional`, metadata and audit tables in one transaction. It preserves existing rows. Legacy cancelled/filled/rejected rows have lost their original notional; this cannot be guessed and their reservation replay fails closed. Legacy partial-fill rows also do not get a guessed original notional. A migration event is a snapshot of imported state, not a reconstructed historical trail. Back up important journals before the first writer upgrade.

The read-only dashboard can display legacy tables without migrating. Unknown future versions are rejected.

## Limits

Not an account ledger, fill simulator, risk authorization or live execution service. Cash, fees, commissions, positions, authoritative broker reconciliation and partial-fill reservation adjustments are not implemented here. The existing `PaperRunner` / `PaperExchange` remains the separate economic replay implementation. The admission journal deliberately cannot settle fills until the accounting integration exists. Recovery means durable state/uncertainty is preserved, not that broker-side reconciliation is complete. Audit records are not cryptographically tamper-evident against direct database modification.
