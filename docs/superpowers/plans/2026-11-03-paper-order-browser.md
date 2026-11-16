# Revision-pinned paper order browser

> Execute inline with test-driven development. No independent subagent is available.

**Goal:** Navigate every persisted paper order without mixing account revisions.
**Architecture:** Extend the existing read-only observer through one shared validated-row reader. Add bounded order pages with a revision+digest precondition; expose a localhost GET route and a Korean read-only browser. No cached historical account snapshots or database migration.
**Tech stack:** Existing Python 3.10+ standard library, SQLite, browser ES modules, Node test runner.

## Design decisions
The 100-row summary remains unchanged. A separate page lists all current orders in deterministic unresolved-first / updated-time / ID order. Filters are all, unresolved, submitting, unknown, filled. Each response is bounded to 100 orders; the account's existing 10,000-order limit is retained. Offset > 0 requires the original revision and digest. Any intervening account commit produces HTTP 409, not an implicitly refreshed page. The user explicitly restarts from page one. No archive retention is promised. All reads use the existing identity, schema, checksum and accounting checks. Invalid inputs are 400, unavailable sources 503. Amounts remain strings; file paths never enter HTTP responses. The UI never infers process liveness, current quotes, broker confirmation or authorization.

## Tasks
- [ ] Observer: failure-first pagination, filtering, exact quantity, lost acknowledgement, conflicting revisions, identity replacement and no-write tests; share the existing reader, do not duplicate its accounting rules.
- [ ] HTTP: bounded parameters, explicit 409, existing 403/405/no-store/CSP behavior and fixed configured source. Preserve `/api/paper-session`.
- [ ] Browser: failure-first state tests, explicit revision reset, busy/timeout protection, prior-result warnings, detached state, plain-text rows, responsive exact-value display. Add a link from the summary.
- [ ] Verify focused existing/new tests and real localhost HTTP; attempt actual browser rendering only where permitted. Review diff, package focused patch and commit only tested files on existing draft branch. Do not merge main.

## Review focus
No-token continuation; changes between page reads; stale client replies after reset; empty filters; quantities too precise for Number. Include tests for each. Source corruption must not be classified as ordinary revision change.

## Constraints and rulings
No credentials, external venue, execution/control writes, file exports or automatic trading. No external package added. Full clone is unavailable due DNS; baseline is scoped artifact/connector reconstruction with Git blob checks, not a complete clone. Full remote CI remains separate evidence. The user has repeatedly authorized continuing implementation and commits on this draft branch; no further design checkpoint is required.
