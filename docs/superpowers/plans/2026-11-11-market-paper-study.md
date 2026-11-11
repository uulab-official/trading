# Fixed-plan market paper study implementation plan

> Execute inline using superpowers:executing-plans; no subagent tool is available.

**Goal:** Run a fixed, comparable strategy experiment plan on imported market history.
**Architecture:** Validate/freeze source and common plan, reuse original per-candidate
market execution and audit, then derive a pinned comparison and optional saved receipt.
**Tech Stack:** Python 3.10+, unittest, Decimal, existing SQLite modules, stdlib only.
**Spec:** docs/MARKET_PAPER_STUDY.md

## Global constraints
No existing execution/account/fingerprint edits, live mode, network, new dependency,
automatic recovery, main merge or deployment. New private output only. 2–8 candidates,
5000 source bars / 20000 candidate-bars, 4 MiB source / 64 KiB plan.

## Review focus
Candidate two fails: candidate one survives, candidate three must not start.
Shared/child source or config replaced: no mixed-source comparison or success.
Same effective settings under different labels: refuse before outputs.
Earlier audited child changes while checking later child: discard stale success.
Partial study/corrupt result receipt: never hide rows or certify completion by presence.

## Tasks
- [x] Fail-first tests: plan bounds, exact original-engine results and failure retention.
- [x] Implement source/plan bindings, sequential run and read-only full-plan inspection.
- [x] Wire CLI with existing shutdown wrapper, explicit sample plan and CI path coverage.
- [x] Review adversarial cases, run related original regressions and independent patch.
- Remote delivery and exact-commit CI are tracked in the PR #1 handoff, not inferred from local tests.

## Rulings / ledger
Continuous development and dev-branch commits were already requested; no new questions.
The network clone/archive is unavailable. Work in a new isolated scoped reconstruction;
unchanged support must never be uploaded as new work. Prior patches include local-only
features: depend only on the current remote market/engine/audit modules.
Do not add batch resume in this increment; no automatic reconciliation is permitted.

Review ledger: four failure-first regressions caught post-audit evidence replacement,
engine changes after the last audit, malformed-receipt inventory loss and a run
report incorrectly labelled read-only. Pin evidence before awaiting audit, recheck
engine/account/file identities, preserve inspected rows and distinguish execute
from inspect. A further failure-first test requires the shared engine fingerprint.
No independent reviewer/subagent is available; this is inline review.
Local verification: 203 unique Python cases, 32 existing Node cases, actual three-
candidate synthetic CLI run/check and SIGTERM interruption with explicit child
recovery. One combined outer call timed out; final new cases completed in separate
18/17 groups and the partial call is not counted. Independent apply and remote
checks are recorded in the evidence package and final PR handoff.
Independent application: all seven files matched; all 35 new tests passed again in 18/17 groups.
