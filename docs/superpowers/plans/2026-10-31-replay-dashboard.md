# Replay Dashboard Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans, task by task.

**Goal:** Display real existing offline-runner results, not invented account values.
**Architecture:** Existing runner → atomic validated report → read-only HTTP → UI.
**Tech Stack:** Python standard library, existing PaperRunner, native ES modules.
**Spec:** ../specs/2026-10-31-replay-dashboard-design.md

## Global Constraints
Python 3.10+, no additional runtime dependencies, no orders from HTTP,
POSIX checkpoint locking, no journal schema changes, preserve existing behavior.

## Review Focus
- Same destination as fixture/checkpoint/lock must fail before replay.
- Invalid/missing/mixed-currency reports must not become empty or healthy data.
- Repeated replay must not duplicate fills; failed publication keeps last good data.
- Fees and marked holdings must match Decimal accounting without double subtraction.
- Offline historical time and wall-clock refresh time must stay distinct.

## Tasks
- [x] Test then implement replay report projection/validation and bounded atomic IO.
- [x] Test then implement wrapper using the existing PaperRunner and writer locks.
- [x] Test then extend loopback server with independently configured replay read API.
- [x] Test then add separate replay UI panel, source/timestamp labels and stale states.
- [ ] Run local Python/Node checks and actual-engine integration in GitHub CI.
- [ ] Record exact verification boundaries and commit to the existing draft PR.

## Execution ledger
Baseline: 10bcf106577e2523ba26ee5579eb958c2a6617e3; prior full CI succeeded.
Container git clone failed because github.com DNS is unavailable. Read-only source
is retrieved via connected GitHub; local changes use an isolated reconstruction.
Actual-engine tests must run against the complete repository in CI, not mocks.

Local red/green checks: 21 Python report/export/API tests and 7 Node client tests
passed. Actual PaperRunner integration is defined as seven network-forbidden CI
cases against the complete repository; it has not run in this sparse container.
