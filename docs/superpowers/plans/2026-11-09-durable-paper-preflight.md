# Durable paper preflight implementation plan

> Execute inline with superpowers:executing-plans and failure-first tests.

**Goal:** Diagnose recovery eligibility without mutating an existing paper run.
**Architecture:** New read-only inspector reuses v32 validators and original replay;
standalone CLI emits machine JSON. Existing resume/run code remains unchanged.
**Tech Stack:** Python 3.10+, stdlib, actual SQLite/PaperRunner tests.
**Spec:** docs/DURABLE_PAPER_PREFLIGHT.md

## Global constraints
No account/control/order writes or new lock/output files. No schema changes,
network, credentials, background work, live activation or main merge.
Keep original fingerprinted sources byte-identical; compare only captured state.

## Review focus
Missing-fill UNKNOWN; saved-fill UNKNOWN; active owner but stable snapshot;
files/account changing during replay; completion marker and invalid CLI inputs.

## Tasks
- [x] Failure-first tests for read-only outcomes, changes and CLI boundaries.
- [x] Implement inspector and CLI with exact v32 validators; preserve originals.
- [x] Regress original run/resume, examine safety edges and independent patch.
- [ ] Publish focused development-branch commit and inspect exact-commit CI.

## Decisions
Ruling: continuous implementation was already requested; no repeated design gate.
Ruling: use isolated local reconstruction after git clone failed DNS; baseline
support is not claimed as a complete checkout. Never upload reconstructed support.
Ruling: success means compatible captured state, never owner availability or
permission to resume. Completed marker existence is not a validity certificate.

Review: inline only; no independent reviewer tool. Explicitly tested read-only
SQLite URIs, live owner leases, old-file replacement and missing-fill refusal.
The initial combined regression shell timed out during its second suite; the
run suite and resume suite were then completed separately. No partial run counts.
