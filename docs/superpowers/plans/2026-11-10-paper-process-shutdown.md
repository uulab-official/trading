# Cooperative paper process shutdown implementation plan

> Execute inline with superpowers:executing-plans and failure-first tests.

**Goal:** Allow explicit historical paper run/resume CLIs to finish their existing
halt and lease-release cleanup on SIGTERM/SIGINT.
**Architecture:** A stdlib CLI-only process controller wraps the existing coroutines;
no fingerprinted execution module changes. One cancellation per invocation.
**Tech Stack:** Python 3.10+, asyncio, signal, unittest, local POSIX subprocesses.
**Spec:** docs/PAPER_PROCESS_SHUTDOWN.md

## Global constraints
Keep PR Draft and no main merge. No live orders/providers/credentials. Preserve
SQLite schema, economic calculations, existing error semantics and manifests.

## Review focus
Repeated signals during halt; suppressed cancellation; synchronous operations;
exceptions after a signal; restoring handlers without leaking child tasks.

## Tasks
- [x] Add failing controller and actual-process run/resume signal cases.
- [x] Add trading_bot/paper_process.py and wire both existing CLIs.
- [x] Exercise saved-effect interruption, explicit recovery, halt failure and
      old economic/fingerprint behavior; review every error path.
- [x] Update CI path coverage, docs and independent patch application evidence.
- [x] Prepare only focused verified changes for commit; remote check results go
      in the PR handoff after the push.

## Decisions
User has repeatedly requested continuous implementation; no new broker or strategy
choice is needed. Work is in an isolated scratch repository because git clone
failed DNS. Prior artifact sources are support, not new deliverables. Missing v29
reader and order store are restored to known original blob hashes. No independent
reviewer tool is present; review and adversarial tests will be performed inline.

## Review evidence
Initial process tests reproduced baseline SIGTERM exiting -15 without the existing
cleanup; SIGINT lacked the new explicit signal diagnostic. The first feature test
run had 18 failures and one absent-field KeyError, all resolved by the feature.
A separate regression reproduced one failed handler restoration leaving the other
handler installed; restoration now attempts every owned handler. A secondary
restore failure must not hide an existing execution/cleanup failure.
Actual SQLite-commit signal and repeated async-cleanup signals passed.
A second review regression reproduced a signal arriving after asyncio.run closed
the loop; the handler now retains the stop request without raising from the closed
loop. Final local suites: 24 shutdown, 26 run, 31 recovery (81 unique tests). An
outer combined command timed out in recovery; its partial recovery results are
excluded and replaced by a separate completed 31-case run.
