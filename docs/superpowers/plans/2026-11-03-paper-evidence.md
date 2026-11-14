# Portable paper-session evidence implementation plan

> Execute inline with test-driven development; no independent subagent is available.

**Goal:** Save and independently verify a complete single-revision paper account.
**Architecture:** Observer projection -> canonical bounded envelope -> no-clobber
private file. Offline verification reuses original account arithmetic validation.
**Tech Stack:** Python 3.10+, stdlib JSON/SQLite/POSIX; no new dependencies.
**Spec:** docs/PAPER_SESSION_EVIDENCE.md

## Constraints and review focus
- No source writes, order normalization, credentials, network or live activation.
- Preserve all entries (not the 100-row display limit) and exact decimal strings.
- Corruption with recomputed checksums still fails accounting validation.
- Preserve existing output and distinguish uncertain publication from no output.
- Snapshot revision strings remain exact above JavaScript's integer precision.
- No implicit migration or claim of process liveness/authenticity.

## Tasks
- [x] RED: add snapshot/roundtrip/UNKNOWN/no-clobber tests and observe failure.
- [x] GREEN: add observer projection, canonical envelope and independent validator.
- [x] Add private bounded read, atomic no-clobber publication and failure tests.
- [x] Add export/verify CLI, process crash tests and CI path trigger.
- [x] Run existing related tests, independent patch verification and inline review.
- [ ] Commit focused files on existing dev branch; retain Draft, no main merge.

## Rulings
Existing repeated requests authorize continuing implementation on the established
paper-first dev branch without another requirements interview. Clone failed DNS;
work occurs in an isolated scoped reconstruction. Original dependency files are
hash-checked and excluded from delivery. New feature is CLI/library only: adding
another dashboard panel would not improve snapshot integrity.

## Review evidence
Temporary-name collision reproduced deletion of an unowned file. Ownership is
now recorded only after exclusive creation succeeds; the regression passes.
A hardlink remaining after actual process death is intentionally accepted for
read-only verification, never for source-account writer ownership. No independent
reviewer is available; review is inline and does not count as external approval.
