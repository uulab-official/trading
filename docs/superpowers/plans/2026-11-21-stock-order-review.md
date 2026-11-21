# Stock Order Review and Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the already-authorized
> parallel workflow. Root integrates and publishes; agents do not commit separately.

**Goal:** Compare stock-order observations, preserve/recover journal snapshots,
and provide a private offline operator report and reproducible integrated trial.

**Architecture:** Audited read-only comparison in core; independent backup CLI;
root-owned static report CLI and smoke integration. No authenticated transport or
automatic journal reconciliation.

**Tech Stack:** Python 3.10+, SQLite, hashlib, json, HTML/CSS; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-21-stock-order-review-design.md`.

## Global Constraints

- Keep paper mode and false account/economic/lifecycle verification and submit flags.
- Preserve baseline `2e78574fe8b6a9c9095080e5120ec754e0475618` and existing tests.
- Never infer/append fills from cumulative observations or restore over active data.
- Standard-library direct import and actual `python -B -S` CLI execution must work.
- Existing private parent directories, exclusive output, static errors, no secrets.

## Review Focus

- Same numeric broker ID across dates/branches and padded aliases cannot mismatch.
- Related orders, absent records, and stale/future collection do not grant retry.
- WAL content and source changes cannot yield a falsely verified backup.
- Existing sidecars, symlinks and output replacements survive refusal.
- Forged provenance/verification fields and HTML metacharacters stay untrusted.

## Task 1: audited comparison module

**Files:** core `stock_order_review.py`, `test_stock_order_review.py`.
**Interface:** `review_orders(journal, observations=None, *, now=None,
max_age_seconds=300)` and the exact report shape in Task A of the spec.

- [x] Add failing tests for equal/different/missing/unknown/related observations,
  date scope, uncertainty, future/stale data, strict schema and false safety flags.
- [x] Implement complete validation and nonmutating comparison under the spec.
- [x] Verify actual SQLite source unchanged, malformed-input refusal and standalone import.
- [x] Freeze only assigned files and report exact targeted results to root.

## Task 2: journal backup and restore candidate

**Files:** tool `stock_order_backup.py`, server `test_stock_order_backup.py`,
`docs/STOCK_ORDER_BACKUP.md`.
**Interfaces:** Task B `create_backup`, `verify_backup`, `restore_candidate`;
all return the verified semantic `snapshot` for integration.

- [x] Write failing native SQLite/WAL, pending restoration, tamper/manifest and
  preservation tests; include actual CLI subprocess tests with socket/DNS guards.
- [x] Implement bounded strict manifest, native snapshot capture and isolated replay.
- [x] Implement exclusive new-path restore and exact source/candidate comparison.
- [x] Document consistency/recovery guarantees and limits, run focused tests and freeze.

## Task 3: operator report and integrated trial

**Files:** root tools `stock_order_review.py`, `stock_order_review_smoke.py`,
server `test_stock_order_review_cli.py`, guides, README, checklist, sample artifacts.
**Consumes:** Task 1 exact review schema and Task 2 APIs; existing synthetic fixture.

- [x] Add meaningful failing CLI/schema/exit-code/output/escaping/integration tests.
- [x] Implement private JSON/HTML reports and temporary/retained integrated smoke.
- [x] Run equality, intentional mismatch, backup, verify and restored-audit workflow.
- [x] Add suites to offline_checks and accurate operator/readiness documentation.

## Task 4: review, regression and publication

- [x] Independently review source/operator claims; reproduce and fix concrete findings.
- [x] Run full pytest, extended offline checks, compileall, fatal lint and diff checks.
- [x] Save synthetic report artifacts and actual verification evidence.
- [ ] Publish with expected-SHA lease, verify remote blobs and local snapshot.
- [ ] Verify exact-head full Python matrix, lint and Docker CI to completion.

## Integration ledger

- Root first full regression: 697 pytest tests and 1,471 subtests passed; extended offline suite 687 tests passed.
- Core comparison: 31 tests/323 subtests; backup initial final: 23 tests/59 subtests; report CLI after no-scope fix: 14 tests/12 subtests.
- Independent review resolved no-scope headline via a failing regression, explicit no-target wording and scoped/matched counts.
- Accepted final backup durability finding: pin/check and fsync newly created backup directory parent before success. Backup agent implementing red/green regression; root must rerun full suite afterward.
- Remaining: final reviewer verdict, actual final test counts, fresh synthetic examples/verification note, expected-SHA publication and exact-head CI.
- Ruling: existing snapshot audit is SQL-read-only but SQLite may manage WAL/SHM; immutability claims concern journal event semantics. Stored-backup verification uses isolated copies and preserves backup contents.
- Ruling: direct continuation authorizes main publication; independent implementation domains run concurrently under the active delegation instructions. Remote history is preserved with GitHub API tree/commit/ref leases rather than local snapshot push.

## Final local gate

- All four implementation SHA-256 values match the independent reviewer’s final source. No findings remain.
- Parent publication flush fix: three new RED→GREEN regressions; backup suite now 26 tests/59 subtests.
- Final full run: 700 pytest tests and 1,471 subtests passed; extended offline run: 690 tests passed. Compileall/fatal lint/diff checks passed.
- Final synthetic run saved both comparison reports and verified backup/restored-state equality, eight checks true, one fake HTTP response and zero actual broker requests.
- The remaining remote steps happen after this document is committed; exact-head evidence is recorded in GitHub Actions and the completion response.
