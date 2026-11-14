# Durable paper recovery implementation plan

> Execute inline with superpowers:executing-plans; no reviewer subagent available.

**Goal:** Explicitly continue an opted-in historical paper run without duplicating saved fills.
**Architecture:** Immutable manifest and run lock, original-engine prefix verification,
shadow replay forwarding only new fills, original session/evidence finalization.
**Tech Stack:** Python 3.10+, stdlib, existing PaperRunner/PaperTradingSession.
**Spec:** docs/DURABLE_PAPER_RESUME.md

## Global constraints
No new live backend, schema migration, auto-retry or recovery of unrecorded fills.
Retain default v31 new-run behavior. Existing local reconstruction is support only.
No main merge. Focused feature branch commit after verification.

## Review focus
Input/engine change, unknown no-fill order, completion-publication race,
crash after effect/before ack, and restart while a writer owns the account.

## Tasks
- [x] Failure-first real-account tests for interruption, no duplicate fill, conflict and CLI.
- [x] Add opt-in creation manifest and run lock without changing ordinary execution.
- [x] Original replay validation and prefix-skipping session adapter; explicit saved-fill reconciliation.
- [x] New resume CLI, tests/CI integration, full scoped regression, patch verification.
- [ ] Inline review, update docs with evidence and commit focused code only.

Ruling: user has repeatedly requested uninterrupted implementation; retain paper-only
scope, no broker or investment choices required. Full clone unavailable (DNS).

Review: no independent reviewer tool was available. Inline review reproduced
atime-only false rejection, late directory-replacement detection and acceptance
of unrelated saved marks until after new fills. Added failure-first tests and
fixed all three. A test's foreign trade fixture was changed to use a valid frozen
mark so it still isolates the history mismatch, without weakening its assertion.

Evidence before publication: 31 new +26 legacy run +176 execution +35 evidence
=268 unique Python cases, 32 existing Node cases; all completed without skips.
A combined repeat invocation timed out and was excluded; affected groups reran
separately. Full clone/archive download unavailable; local support is reconstructed.
The strategy package initializer is the prior offline lazy-import support version;
MA/RSI implementation files are unchanged. Remote CI must test the actual branch.
