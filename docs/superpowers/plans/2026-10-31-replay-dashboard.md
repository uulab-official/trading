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
- [x] Run local Python/Node checks and actual-engine integration in GitHub CI.
- [x] Record exact verification boundaries and commit to the existing draft PR.

## Execution ledger
Baseline: 10bcf106577e2523ba26ee5579eb958c2a6617e3; prior full CI succeeded.
Container git clone failed because github.com DNS is unavailable. Sources were
retrieved via connected GitHub; local changes use an isolated reconstruction.
Actual-engine tests run against the complete repository in CI, not mocks.

Local verification: 21 Python report/export/API tests and 7 Node client tests
passed. Original-engine integration: seven network-forbidden replay cases passed
in GitHub on Python 3.10 and 3.12, including a separate real loopback HTTP check.
For code commit 2d88101623b8cb13016ddd0c4c5ca51c836ab060, both Quant Regression
41196787271 and CI/CD Pipeline 41196787258 completed successfully.

Offline Chromium render checks passed at 1440px light/dark and 390px mobile:
no page errors or page-level horizontal overflow; failed refresh retained the last
good data, reconnect restored it, and markup-shaped fill identifiers remained text.
A reproduced 1px card-edge inset was fixed with 22px/16px content gutters. These
checks exercise local assets and synthetic read-model data, not browser HTTP/CSP.
The last styling/documentation commit does not change Python or JavaScript behavior;
consult its own CI checks for the final branch-head status.
