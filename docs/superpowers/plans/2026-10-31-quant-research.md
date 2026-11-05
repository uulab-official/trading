# Quant Research Implementation Plan

> Execute inline with superpowers:executing-plans; test each change before committing.

**Goal:** Existing offline engine → chronological candidate selection → checked research report → local UI.
**Architecture:** Pure report contract/planner; delayed engine adapter; bounded CLI; existing server read route and separate page.
**Tech Stack:** Python 3.10+, Decimal, standard library; browser ES modules, Node tests.
**Spec:** docs/superpowers/specs/2026-10-31-quant-research-design.md

## Global Constraints
No real orders, credentials, public hosting or main merge. Preserve existing replay identities and admission accounting. All amounts remain Decimal text. No fabricated performance.

## Review Focus
Future rows in selection; warm-up signals crossing boundaries; ties/skips and negative returns; corrupt/oversized reports; publication races and existing files.

## Tasks
- [x] Write failing tests for chronology, selection, skips, validation and no-clobber output.
- [x] Implement research_report.py and walk_forward_research.py with actual runner adapter.
- [x] Add CLI and original-engine integration tests (real engine verification in GitHub CI when local DNS blocks cloning).
- [x] Add /api/research and /research.html, client validation and stale retention tests.
- [ ] Run local tests, inspect diff, publish one coherent commit to the existing PR branch, verify CI and document limits.

## Execution ledger
Baseline: d468c9284987a2da2668f19fed8d4fd8e4d88f03; baseline full CI success verified. Git clone fails DNS. Worktree is an isolated sparse reconstruction, not a full local clone. No reviewer subagent available; review will be inline.

Local regression cycle: first missing implementation tests failed, then passed. Additional RED→GREEN reproduced first-row input crashes, context-dependent Decimal tie ranking and bool-as-count report acceptance. Local current result: 25 Python tests + 6 Node tests pass. Server/store/replay dependency copies checked against upstream blob hashes. Actual engine test execution remains delegated to the configured CI matrix, not a simulated local success.

CI cc4856c: all isolated Python/Node checks passed; original-engine positive-fill test failed because the original strategy requires 50 observed candles and the test allowed only 36+12. Ruling: preserve the engine readiness gate; extend the positive fixture to train=72/warmup=60, assert original cold short-window skip separately, and strengthen actual warm-up/no-early-fill coverage. No production selection threshold or existing execution behavior was changed.
