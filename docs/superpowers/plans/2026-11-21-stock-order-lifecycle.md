# Stock Order Lifecycle Implementation Plan

> **For agentic workers:** Execute the already-authorized parallel workflow;
> root integrates, independently verifies and publishes the complete result.

**Goal:** Durable stock-order simulation and read-only KIS order observation.
**Architecture:** Separate event journal, bounded read-only HTTP observer, and
offline CLI/scenario runner. No automatic bridge from observations to fills.
**Tech Stack:** Python 3.10+, SQLite, urllib, hashlib/json, unittest/pytest.
**Spec:** `docs/superpowers/specs/2026-11-21-stock-order-lifecycle-design.md`

## Global Constraints

- No actual order transport, credentials in files/logs, or existing cash-ledger writes.
- Preserve baseline c5add10070e8a3270ee34721dcd56e55b3bc729b and its regressions.
- Root owns integration/README/checklists/verification and atomic remote commits.

## Review Focus

- Out-of-order/double deliveries after terminal state cannot change economics.
- Partial cancellation and late fills cannot erase executed quantity or costs.
- Corruption, rollback and process death cannot create valid-looking partial history.
- Pagination completeness is separate from execution evidence/account reconciliation.
- No source record, error, cursor or path leaks credential/account values.

## Task 1: Durable stock event journal

**Files:** `packages/core/trading_core/stock_order_journal.py`,
`packages/core/tests/test_stock_order_journal.py`.
**Interface:** create/open_existing/record/snapshot exactly as specified.
- [x] Read the existing code and spec; add meaningful failing regressions.
- [x] Implement replay reducer, idempotency and atomic SQLite event/tail storage.
- [x] Verify restart, concurrency, corruption, rollback and real process crash.
- [x] Report exact interface/result shape and tests to root; do not commit alone.

## Task 2: KIS VTS read-only order snapshot

**Files:** `tools/kis_vts_order_snapshot.py`,
`apps/server/tests/test_kis_vts_order_snapshot.py`, `docs/KIS_VTS_ORDER_SNAPSHOT.md`.
- [x] Pin the official request/response/paging contract in failing tests.
- [x] Implement strict normalization, bounded pagination and no-secret transport/CLI.
- [x] Verify malformed responses, private fields, wrong-date/duplicate rows,
  continuation failures, redirects, no retry and output collisions.
- [x] Document observational limits and official sources; report to root.

## Task 3: Operator CLI and offline lifecycle demonstration

**Files:** `tools/stock_order_journal.py`, `tools/stock_order_lifecycle_smoke.py`,
`apps/server/tests/test_stock_order_lifecycle.py`,
`config/examples/synthetic_stock_order_events.json`, `docs/STOCK_ORDER_LIFECYCLE.md`.
- [x] Add strict bounded event input and explicit init/record/read-only status.
- [x] Build/run synthetic uncertainty/partial-fill/cancellation/replay scenario.
- [x] Test actual CLI under socket/DNS guards, no-site-packages and output failure.
- [x] Add suites to offline_checks; update README and accurate milestone checklists.

## Task 4: Review and final verification

- [x] Independent review of final source and operator docs; fix concrete findings.
- [x] Full pytest, extended offline checks, compileall, fatal lint and diff check.
- [ ] Record real results and source limitations; publish with expected-SHA lease.
- [ ] Verify the exact main commit's complete CI including Docker.

The user repeatedly requested direct continuation. Implementation choices use
the existing Python stack; no new approval stop is introduced. The local git
history is a tracking snapshot; remote history is preserved through GitHub API
tree/commit/ref updates. Artifact code and docs stay in this repository.

Local final verification: 629 pytest tests and 1,077 subtests passed; extended
offline checks ran 619 tests successfully. Independent review found sidecar
preservation and output replacement issues; red/green regressions cover both.
Publication/CI checkboxes above describe the post-document remote steps. Their
exact-commit evidence is provided by GitHub Actions and the completion report.
