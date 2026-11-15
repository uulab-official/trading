# Verified PAPER Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Preserve and fully verify a quiescent account/capture pair and a separately recovered non-executing copy.

**Architecture:** Add standalone streaming private-file helpers and a backup coordinator. Adapt existing read-only SQLite validators and full economic replay to fixed bundle payload names without changing frozen runtime code. Independent CLI/drill work uses the coordinator's exact API.

**Tech Stack:** Python >=3.10, stdlib SQLite/POSIX descriptors/flock/fsync, unittest, existing Python packages and CI.

**Spec:** `docs/superpowers/specs/2026-11-15-paper-backup-design.md`

## Global Constraints

- Keep all 24 v46 execution-fingerprinted modules byte-identical.
- Fixed four-file, 0700/0600, single-link owned bundle; no executable account/capture layout.
- Bounds: account 268435456 bytes, capture 67108864 bytes, canonical metadata 65536 bytes; streaming chunks 1048576 bytes.
- Existing sources and checked bundles read-only; output new/no-clobber, no automatic cleanup or restart.
- Hold existing account audit SH then source SH throughout creation; hold bundle SH throughout verification/copy.
- Never rebind source, clear terminal state, claim newest-state/replica authority, call provider/broker, deploy or merge main.
- Publish the verified tree to the existing feature ref; keep Draft PR and verify final-head CI before metadata update.

## Review Focus

1. A saved stopped phase with an actual owner must still block backup — IO/core ownership tests.
2. A source or output parent moved after audit/copy must not yield success — core/pinned-IO substitution tests.
3. A forged but self-consistent metadata/hash wrapper must not certify forged economic state — full replay tamper test.
4. A late signal combined with uncertain fsync must preserve possible output and report storage uncertainty — IO/CLI fault tests.
5. A recovered archive with missing originals must stay verifiable and non-executable — drill/runtime-refusal tests.

## Task 1: Pinned private streaming IO

**Files:** `apps/server/trading_bot/paper_backup_io.py`, `tests/test_paper_backup_io.py`.

**Interfaces:** `BackupUnavailable`, `BackupStorageError`, `BackupInterrupted(signum)`,
`check_stop(stop)`, context-managed `PrivateDirectory(path, create=False)` with
check/open_file/check_file/refresh_file/check_names/lock/sync/close and path,
directory_fd/files properties; `file_info`, `copy_file`, `write_file` return
exact size/SHA dictionaries. Signatures and safety rules are in the task brief.

- [x] Write real-file refusal/copy/lock/fork/fault tests, observe missing-feature RED.
- [x] Implement bounded descriptors, no-clobber output and uncertainty preservation.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_backup_io.py' -v`; expect all pass.
- [x] Record results and commit with core integration after interface check.

## Task 2: Paired archive, full verification and recovered copy

**Files:** `apps/server/trading_bot/paper_backup.py`, `tests/test_paper_backup.py`.

**Consumes:** Task 1 IO plus unchanged stream/capture readers, `_SourceBinding`, `_verify`.
**Produces:** async `create_backup(account_directory, output, *, stop=None, clock=None)`,
`check_backup(directory, *, stop=None)`, `verify_restore(directory, output, *, stop=None)`.
Exact bundle and result schemas follow the spec.

- [x] Write real continuous-account round-trip, held-owner, blocked-state, source/parent substitution, journal and forgery tests; observe RED.
- [x] Implement fixed canonical manifest, read-only payload-reader adaptation, original replay, held-lock creation and new recovered-copy publication.
- [x] Cover duplicate keys, bool-as-int, noncanonical metadata, payload corruption, extra names, fingerprint mismatch and original-path independence.
- [x] Cover post-mutation signals/fsync uncertainty without deleting possible output.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_backup.py' -v`; expect all pass.

## Task 3: CLI and operator contract

**Files:** `tools/paper_backup.py`, `tests/test_paper_backup_cli.py`, `docs/PAPER_BACKUP.md`.
**Consumes:** Task 2 APIs and Task 1 exceptions.
**Produces:** strict create/check/verify-restore commands; one JSON stdout result;
fixed private errors; 0/2/4/130/143 exit contract; numeric-only signal intent.

- [x] Write parser, late signal, storage precedence, handler restoration and real CLI tests; observe RED.
- [x] Implement wrapper and precise operator runbook, including archive authority/rollback limits.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_backup_cli.py' -v`; expect all pass.

## Task 4: Repeatable offline recovery drill and CI

**Files:** `tools/paper_backup_drill.py`, `tests/test_paper_backup_drill.py`,
`.github/workflows/quant-regression.yml`, `.github/workflows/order-manager-safety.yml`.
**Consumes:** Existing synthetic capture/continuous account interfaces and Task 2 APIs.
**Produces:** `python tools/paper_backup_drill.py --directory NEW_DIR`, a private
drill-report.json, fixed one-document stdout and a network-forbidden proof of
real backup -> verification -> recovered-copy replay after generated originals
are temporarily unavailable. No normal runtime layout in copied bundles.

- [x] Write actual drill and no-clobber/CLI failure tests; observe RED.
- [x] Implement deterministic 120-bootstrap/120-new-bar account and exact recovered-byte comparisons.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_backup_drill.py' -v`; expect all pass.
- [x] Add all `test_paper_backup*.py` to installed-runtime CI jobs (Python 3.10/3.12/3.13), paths and compilation checks.

## Task 5: Final review, compatibility and delivery

**Files:** README, PRODUCTION_CHECKLIST, current handoff and operations runbook;
ignored ledger, exact-tree publication/CI/metadata receipts.

- [x] Verify original v46 demo still passes audit and all 24 frozen source files are byte-identical.
- [x] Run full unittest discovery, package/server pytest and Node clients; inspect actual outputs.
- [x] Run standalone offline drill and inspect preserved/recovered bytes and audit report.
- [x] Independent whole-increment review; reproduce and repair material findings with RED/GREEN regressions.
- [x] Re-run required final gates on repaired tree; update docs with actual evidence and limits.
- [ ] Commit, publish exact tree using expected-head non-force update, and verify all applicable final-head workflows/jobs/logs.
- [ ] Fresh metadata comparison, update only Draft PR title/body, independent final ref/tree/metadata/CI readback.

Publication identities and final CI are recorded in the PR and ignored receipt
after the immutable tracked checkpoint, avoiding a self-referential commit/CI loop.
