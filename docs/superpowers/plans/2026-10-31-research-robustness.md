# Research robustness implementation plan

> **For agentic workers:** Use superpowers:executing-plans; execute the existing user's requested continuous development, no extra approval round.

**Goal:** Optional, audited benchmark/cost diagnostics connected to existing research and UI.
**Architecture:** v1 unchanged; opt-in v2 contract, original PaperRunner/PaperExchange, same read-only API.
**Tech Stack:** Python 3.10+, standard library, ES modules, existing CI matrix.
**Spec:** docs/superpowers/specs/2026-10-31-research-robustness-design.md

## Global constraints
No real orders, keys, network evaluation, separate fill engine or mutable old reports. Multipliers exactly 1/2/3. Independent-window arithmetic averages, no compounding. Base-selected strategy frozen. Strict same initial allocation, second scored open entry and terminal mark reference.

## Review focus
Skipped windows are cash, not absent. Zero fee is valid. Higher costs can change risk paths. Legacy report identities unchanged. Large or corrupt reports must fail before serving or executing.

## Tasks
- [x] Contract tests fail first: v1 compatibility; v2 identity/selection/cost/reference/arithmetic tampering; no-clobber publication; nonfinite and work-budget rejection.
- [x] Implement pure report validation/reference arithmetic and optional diagnostics in run_research, preserving existing evaluator path.
- [ ] Original-engine CI tests: reference fees/rounding and next-open entry, frozen selection, deterministic repeated runs, skipped windows, quote currencies and no network.
- [x] CLI opt-in and client v1/v2 validation/rendering tests, with loading/error/legacy paths.
- [ ] Verify local contract/HTTP/client tests, inspect mobile/desktop behavior when supported, commit atomically to existing PR branch and inspect actual GitHub results.

## Evidence ledger
Baseline remote head ceb3f126; its CI/CD completed successfully. Direct git clone failed DNS; local source is a sparse connector reconstruction. research_report and walk_forward_research blob hashes match upstream. No independent subagent is available; review inline with explicit tests. Do not claim locally running the full original engine until it is actually installed and run.

Local evidence: 13 contract + 2 CLI Python tests and 7 Node tests passed; missing-feature and tiny-mark regression failures observed first. Desktop/mobile/dark offline render passed, no browser HTTP/CSP claim. Original-engine/real-HTTP tests require installed original packages and run in GitHub Actions. Code review found a tiny-mark precision mismatch; reference projection now follows original equity-minus-cash precision. No account/execution/risk authorization boundary changed.
