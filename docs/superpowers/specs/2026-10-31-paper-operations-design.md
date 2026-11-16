# Reliable local paper operations

Continue the approved paper-first architecture on `feat/smc-market-structure`. Make the existing order journal and dashboard useful together; do not create another trading/accounting engine.

## Scope and decisions
- Preserve original reservation identity after cancellation. Add non-destructive SQLite migration and transactional audit records. Unknown submissions remain reserved; no automatic resubmission.
- Add explicit acknowledgement/rejection/cancel-request transitions to the existing journal, with idempotent confirmations. Do not add fill accounting without integrating the existing `PaperRunner`/`PaperExchange` later.
- Read the journal through a transaction-consistent, read-only snapshot. Validate actual schema/data for health, not just file existence. Group reservation amounts by currency; never imply cash, equity or performance.
- Connect the dashboard status, reconciliation queue, filters, pagination and audit history to the snapshot. Keep last successful data visible with a stale warning on failure. No overlapping polling requests.
- Bind only to loopback; enforce local Host and same-origin requests; deny writes; use strict CSP and external assets. This is not authentication or public-server readiness.
- Provide an explicitly synthetic, non-overwriting local lifecycle demo and reproducible tests. No broker credentials, external orders or claimed returns.

## Alternatives considered
1. Rewrite around an upstream full engine: rejected for this increment; would duplicate existing replay/accounting and change the project too broadly.
2. Add more isolated indicators: rejected; does not repair operational integration.
3. Extend the existing journal + dashboard: selected as the incremental implementation decision under the user's delegated request.

## Compatibility
Existing `reserve`, `mark_submitting`, `mark_unknown`, `confirm_cancelled`, `pending_reconciliation`, `reserved_total` callers remain supported. Legacy terminal rows with lost original notional cannot be safely reconstructed; retries must fail closed. Reader can show legacy four-column test/read views without mutating them. Migration belongs to the writer, not the HTTP server.

## Acceptance
Regressions must demonstrate the prior cancellation-identity and false-health bugs before fixes. Exercise restart, atomic audit rollback, competing reservations, special-character paths, invalid inputs, unsupported schema, untrusted origin/host, last-good-data retention and mobile layout. Report local targeted tests separately from full repository CI. Preserve draft PR and do not enable live trading.
