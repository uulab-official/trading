# Durable paper operating control implementation plan

> Execute inline with test-driven development. No subagent tool is available.

**Goal:** Persist explicit active / reduce_only / halted operating modes in opt-in local paper accounts.
**Architecture:** Retain PaperExchange arithmetic and the existing account/order row. A version-3 envelope adds a validated control state; every change is committed before success. Guard new intents and account effects, but preserve reconciliation and duplicate-ID reads.
**Tech Stack:** Existing Python >=3.10, standard-library asyncio/SQLite, POSIX local filesystem.
**Spec:** This document's design and constraints below.

## Design and rulings
User repeatedly requested autonomous continuation; no additional design approval sought. Selected additive, explicit paper-only control, not provider integration or automatic live activation. No schema migration: existing v1/v2 accounts keep prior behavior. New enable_control=True accounts start halted. Reopening cannot override control. Latest mode, fixed reason code and generation are stored; this is not a signed audit log or account identity proof.

Allowed modes: active, reduce_only (spot SELL only, all other checks retained), halted (no new orders). A change carries expected_generation and a bounded reason code (operator, risk_review, maintenance). Same current mode with current generation is a no-op; stale generation fails even on same mode. Relaxing a restriction requires no in-flight or nonterminal intent. Restricting does not cancel orders or undo fills. No force-resume or automatic reset. Quote receipts are cleared after actual transitions when quote-age policy is enabled.

Guard before preflight and immediately before simulated account commit. Post-intent denial remains UNKNOWN; never assume no submission. Control saves do not change economic snapshots or orders. Failed storage latches the manager; a new process must reopen. Generation reserves its final value for halted. One lifetime file owner; these methods belong to the running owner, not an out-of-process emergency kill service.

## Tasks
- [x] Write failure-first tests: opt-in, modes, persistence, CAS, idempotency, legacy, recovery, concurrent control and writes.
- [x] Implement pure control contract in packages/core/trading_core/paper_control.py and integrate paper_session.py.
- [x] Add corruption, commit-failure, process-exit, policy coexistence and synthetic CLI checks.
- [x] Document contract, limits, examples and focused evidence. Review diff and rerun relevant suites.
- [ ] Apply focused patch independently; publish to current development branch only if base matches; inspect CI.

## Review focus
Unknown outcomes cannot be cleared by a mode change. Successful stop must not be overwritten by a suspended account calculation. Storage failure must never report stop success. Freshness must not be renewed by a control transition. Old accounts must not implicitly change version or policy.

## Execution ledger
Baseline: reconstructed from supplied packages; core order_state_store re-read through connector and Git blob hash matched. Full clone attempted and failed DNS. Local baseline admission suite: 32 passed. Remote v23 all three workflows completed successfully. Local lazy initializers are support-only and will not be included in the focused patch.

Implemented: 37 control tests and existing 32 admission + 31 session + 47 manager + 29 reconciliation tests passed (176 unique IDs). Actual child processes exited before/after control commit. Original engine self-test passed separately on reconstructed support. Inline diff review performed; no independent reviewer available. No original execution arithmetic changed.
