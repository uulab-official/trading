# Completed historical paper audit Implementation Plan

> Execute inline with superpowers:executing-plans and failure-first tests.

**Goal:** Verify completed opt-in runs without trusting marker presence or touching account state.
**Architecture:** Add one read-only inspector reusing original recovery/evidence/replay validators, then a separate CLI and focused CI paths. No fingerprinted engine changes.
**Tech Stack:** Python 3.10+, stdlib unittest/Decimal/SQLite.
**Spec:** docs/DURABLE_PAPER_AUDIT.md

## Global constraints
Six changed files only. Preserve PR Draft; no merge/live/provider calls. Source is isolated artifact reconstruction after actual clone DNS failure, not a full checkout.

## Review focus
- Forged result metrics or comparison with valid underlying evidence: reject.
- Current DB changed after genuine completion: reject rather than certify the current state.
- Path traversal/symlink/hardlink/permissions and source replacement during replay: reject.
- Recovery counters cannot be proved from endpoints: structurally check, explicitly exclude from claims.
- Exact numeric/bool types, no-fill outcomes, CLI cancellation/invalid arguments: deterministic semantics.

## Tasks
- [x] Write failing tests using real run/resume, SQLite observations and CLI subprocesses.
- [x] Implement audit_durable_replay(directory) with captured-file/signature checks, original replay and paired account observations.
- [x] Add paper_audit.py and CI path entries, preserving clean stdout JSON.
- [x] Review boundary cases; rerun originals and independent patch application.
- [x] Prepare tested six-file publication. Record actual remote ref and CI status separately in the PR handoff.

## Decisions
Ruling: continuous execution is already requested; no repeat design questions.
Ruling: require recorded manifest instead of claiming historical source identity for old unmarked runs.
Ruling: additive module avoids invalidating v32 execution fingerprints.
Ruling: no available independent reviewer tool; perform inline review and disclose scope.

Review evidence: a failure-first regression reproduced result replacement during the final execution-fingerprint hash. Source identities are now checked after that hash; the regression passes. Recovery counters remain explicitly unproven. No available independent reviewer; review was inline.

Completed local checks: 339 unique focused Python cases (39 new), all passes with no failures/errors/skips/expected failures; 32 existing Node cases. Prior timed-out invocations are not counted. Final receipts contain exact test IDs.

Independent patch verification: all six contents matched; 39 new audit and 31 existing recovery cases passed. A subsequent plan-only documentation update does not change the tested execution/test bytes. Synthetic normal CLI run and read-only audit agreed on 100 bars, 2 fills, zero unresolved orders and halted control; run-file bytes/modes/mtimes stayed unchanged.
