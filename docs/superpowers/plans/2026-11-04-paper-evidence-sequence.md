# Paper evidence sequence implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans.

**Goal:** Reconcile an ordered collection of saved observations without dropping conflicts.
**Architecture:** Extract an internal captured-pair helper from the unchanged v29 comparison;
stream validated captures through pair and endpoint checks, then expose one CLI action.
**Tech Stack:** Python 3.10+, stdlib/unittest, existing paper accounting.
**Spec:** docs/PAPER_EVIDENCE_SEQUENCE.md

## Global constraints
No DB mutation, provider access, credentials, live activation, deployment or main merge.
2–32 supplied files; cumulative input <=64 MiB; original single-file bounds retained.
Amounts remain exact strings; bounded details never determine totals.

## Review focus
A conflicting middle snapshot concealed by compatible endpoints.
Acknowledgement-only recovery counted as another execution.
Read-after-verify replacement of an input.
More than 100 new fills/positions silently truncated in totals.
Import and error handling leaking paths or partial economic reports.

## Tasks
- [x] Write failing real-account sequence/CLI regression tests.
- [x] Share captured-pair calculation while retaining public compare output.
- [x] Implement bounded sequence, interval accounting and endpoint reconciliation.
- [x] Add CLI, redacted errors and actual synthetic execution examples.
- [x] Review, run related checks and independently apply focused patch.
- [ ] Commit to existing draft branch with lease; inspect exact-commit CI.

## Decisions
Continuous implementation already requested. Scoped isolated reconstruction is used
because git clone failed DNS; original fetched files are hash-checked. No independent
reviewer tool is available; inline review and targeted tests will be documented.

Failure-first evidence: initial suite reached 20 expected missing-feature failures;
two test setup mistakes (lookup keyword and an out-of-range initial balance) were
corrected and individually confirmed as missing-feature failures before coding.
Inline review reproduced non-JSON stderr for an empty sequence; nargs now permits
validation to return the fixed JSON error. No old assertions were weakened.
A local legacy suite initially missed its unchanged reconciliation demo; restored
from v21 artifacts before the completed 176-case run, not a runtime change.
