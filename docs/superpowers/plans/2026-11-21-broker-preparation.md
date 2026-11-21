# Broker Preparation Implementation Plan

> **For agentic workers:** Use the existing authorized parallel execution workflow.
> Steps use checkbox syntax for tracking; root integrates and verifies all work.

**Goal:** Reuse Toss tokens and prepare KIS VTS limit-order contracts offline.

**Architecture:** Keep Toss in its read-only collector. Add a pure KIS core
module and an offline file-writing CLI; neither changes a paper ledger.

**Tech Stack:** Python 3.10+, standard library, unittest/pytest, existing CI.

**Spec:** `docs/superpowers/specs/2026-11-21-broker-preparation-design.md`

## Global Constraints

- No live-money or VTS order transmission in this iteration.
- Existing Python 3.10/3.11/3.12 CI matrix and socket-forbidden offline suite.
- No credentials/account values in generated plans, logs, examples or commits.
- Preserve authoritative accounting and previously committed regression tests.

## Review Focus

- Supplied token plus credentials or issuance opt-in must not rotate the token.
- HTTP errors, revoked tokens and malformed responses must never cause retry.
- Boolean/fractional/excessive quantities and noncanonical prices fail closed.
- A business acknowledgment must never imply a fill or ledger reconciliation.
- File collisions and hostile error bodies must preserve evidence and secrets.

## Task 1: Toss token reuse

**Files:** `tools/toss_readonly_snapshot.py`,
`apps/server/tests/test_toss_readonly_snapshot.py`, existing Toss operator doc.

**Interfaces:** Extend `fetch_readonly_minute` with `access_token=None` and
`issue_token=False`; CLI reads `TOSS_ACCESS_TOKEN` and accepts `--issue-token`.

- [x] Add failing tests for GET-only supplied-token reuse, credentials without
  issuance opt-in, invalid tokens before I/O, and no retry on HTTP failure.
- [x] Observe failures, implement explicit issuance and header/input validation.
- [x] Verify old opt-in OAuth flow and CLI secret suppression, then update docs.

## Task 2: KIS VTS offline preparation

**Files:** create `packages/core/trading_core/kis_vts_order.py`,
`packages/core/tests/test_kis_vts_order.py`, `tools/kis_vts_order_plan.py`,
`apps/server/tests/test_kis_vts_order_plan.py`, `docs/KIS_VTS_ORDER_PLAN.md`.

**Interfaces:** `build_order_plan(*, client_order_id, symbol, side, quantity,
limit_price, max_notional, market="KRX", currency="KRW") -> dict` and
`classify_order_response(payload, *, http_status) -> dict`. The module contains
no HTTP transport. The CLI accepts explicit paper mode and a new output path.

- [x] Add failing exact request-contract and input-boundary tests.
- [x] Implement VTS-only request templates with account/credential placeholders,
  exact integer notional checking and local-only correlation IDs.
- [x] Test accepted, rejected, malformed and transport-unknown acknowledgments;
  every result says no verified fill and no automatic retry.
- [x] Test CLI with socket/DNS disabled, secret-bearing environment, exclusive
  output and existing evidence unchanged; document limits and official sources.

## Task 3: Integrate, review and publish

**Files:** `tools/offline_checks.py`, `README.md`, verification record.

- [x] Include new core/server test modules in socket-forbidden checks.
- [x] Run CLI examples with synthetic inputs; review generated plan structure.
- [x] Independent review of both changes and fix substantive findings.
- [x] Run full pytest, offline checks with backtest, compileall and fatal lint.
- [x] Atomically commit to the authorized remote main using expected-SHA lease.
- [ ] Verify CI for the exact resulting head, then report implemented scope and
  the remaining credential-dependent integration boundary.

## Execution notes

The user has repeatedly requested direct continuation. Existing authorization
covers reversible implementation, verification, documentation and main commits;
no extra design-approval stop is introduced. The local repository is a verified
snapshot with local-only tracking history; GitHub API commits preserve remote
history. Each independent subsystem has one writer, with root-owned integration.

Local verification completed: 550 pytest tests, 757 subtests and 540 offline
checks passed. Independent review is complete; the concrete Toss non-directory
output-ancestor issue was reproduced, fixed and included in the final suite.
Remote publication and exact-commit CI are recorded in GitHub Actions and the
completion message, after this pre-publication plan snapshot.

Initial remote CI d46620e exposed Python 3.10 HTTP 308 exception dispatch
differences. An explicit refusal handler and a GET/POST, same/cross-host
regression reproduced the missing-stdlib-handler case before the fix; the
updated full local verification above includes the compatibility correction.
