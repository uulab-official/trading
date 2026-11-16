# Read-only durable paper session monitor

> Execute inline with test-driven development. No independent subagent is available.

**Goal:** Observe a running or stopped durable paper account without acquiring its
writer lease, restoring/normalizing its orders, or authorizing execution.

**Architecture:** A short, private SQLite read-only transaction copies one verified
row. Reuse the existing bundle validator after closing that transaction. Pin file,
account, configuration and revision identity for the observer lifetime. Expose a
bounded read model through the existing localhost server and a read-only CLI.
Render one responsive Korean operating page with stale-result handling.

**Tech:** Python >=3.10 stdlib, existing PaperExchange validators, SQLite, vanilla
ES modules, existing local Host/Origin/CSP controls. No new runtime dependencies.

## Design decisions / constraints
- Continue the user's implementation direction without asking again.
- No credentials, broker traffic, orders, control writes or public deployment.
- Stored control is NOT a heartbeat. Quote receipts are owner-memory state and
  cannot be inferred from a saved quote timestamp. No ready-to-trade badge.
- New read-only mode must not open PaperTradingSession/SQLiteOrderStateStore.
- Versions 1/2/3 remain unchanged; legacy controls are explicitly not enabled.
- Reuse original accounting, count all orders; bound displayed details to 100.
- The short read can temporarily conflict with rollback-journal commits; report
  unavailable on contention, never return zero balances or silently stale success.
- Reject missing/private-file failures, schema/digest/accounting mismatches,
  identity replacement and observed revision regression. Hash is not a signature.
- No `immutable=1`: the writer may change the database. No lock file modification.
- Scope is the current committed branch, not earlier cumulative local patches.

## Tasks and review focus
- [ ] Failing test: observer availability, then short read and projection tests.
- [ ] Reader: existing locked writer, lost acknowledgement, stored versus fresh
  marks, precise numbers, bounded details, schema and file identity failures.
- [ ] Existing server integration and JSON CLI; lazy optional report loading so
  an unrelated absent reader cannot prevent this source being observed.
- [ ] Client contract tests, defensive text-only rendering and stale/error recovery.
- [ ] Actual local HTTP, browser attempt, mobile/desktop rendering, related suites.
- [ ] Independent patch application, remote hash verification, CI and evidence.
