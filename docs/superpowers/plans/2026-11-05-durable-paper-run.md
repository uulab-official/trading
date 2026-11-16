# Durable paper strategy operation implementation plan

> Execute inline with superpowers:executing-plans; use failure-first tests.

**Goal:** Run original historical MA/RSI signals directly into a NEW durable paper
account, and verify before/after evidence without a broker connection.
**Architecture:** Add an internal session backend seam to the existing PaperRunner.
A bounded orchestrator owns a new private run directory and PaperTradingSession,
freezes inputs/settings, starts halted, activates only for this explicit replay,
then halts and verifies saved evidence before publishing completion.
**Tech Stack:** Existing Python 3.10+, asyncio, SQLite, Decimal, unittest; no deps.
**Spec:** docs/DURABLE_PAPER_RUN.md

## Global constraints
Original strategy, next-bar execution, sizing, risk and PaperExchange arithmetic
must be reused unchanged. Legacy checkpoint paths stay unchanged. New runs only;
no replay resume, live mode, provider calls, secrets, main merge or deployment.
Only explicit changed files are published; reconstructed support stays local.

## Review focus
Future-bar leakage; ambiguous acknowledged outcome; failure to persist halt;
existing output paths; disagreement between final report and saved evidence.

## Tasks
- [x] Failure-first tests: original-engine parity, actual persisted account,
  final halt, prevalidated inputs, no-clobber, unknown/cancellation/faults and CLI.
- [x] Internal runner seam with no new accounting algorithm; strict session guard.
- [x] New-run orchestration + private frozen inputs and before/after evidence.
- [x] Keyless example, CLI, operational boundaries and CI test integration.
- [ ] Verify related suites, review, independent patch apply and remote CI.

## Decisions
Continue autonomously as requested. A new run never opens an existing account.
SIGKILL/power loss cannot execute a finalizer; surviving DB is evidence to inspect,
not proof of a stopped process. No automatic rerun or cursor reconstruction.
Local clone failed DNS; work in isolated scoped reconstruction, not user's tree.
No independent reviewer/subagent tool is available; review inline and label it.

Review: the initial seam forgot to await async get_open_orders, causing normal
orders to be rejected. Corrected with the existing failing parity/CLI cases.
Review: route runtime error JSON to stdout, leaving original library diagnostics
on stderr rather than suppressing logging. Added a failure-first diagnostic test.
Actual subprocess death after account fill preserved unconfirmed fill, active
saved mode and no completion; no claim of finalization after SIGKILL.
