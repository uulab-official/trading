# Verified PAPER Runtime Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for continuous execution with a fresh whole-increment review. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, externally verify and safely extract the actual minimal PAPER runtime, then prove the extracted source works independently of installed packages and the checkout.

**Architecture:** A pure canonical format/profile module feeds a descriptor-pinned filesystem layer. A strict JSON CLI exposes four bounded operations. Separate integration tests execute only extracted runtime code and existing native paths.

**Tech Stack:** CPython >=3.10 standard library, canonical uncompressed USTAR, SHA-256, POSIX openat/no-follow/fsync, unittest and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-15-paper-release-design.md`

## Global Constraints

- Preserve all 24 frozen engine files against base `19f2f64767535ee473742d3b2d34bc4b7cd45543`.
- Source tree layout and seven explicit tools only; 55 payload files plus `paper-release.json`.
- Archive <=8 MiB; each payload <=1 MiB; manifest <=64 KiB; exact canonical member set, metadata, order and zero padding.
- External expected SHA-256 mandatory for check/extract/verify; no authentication claim from internal hashes.
- No extraction/import/activation before complete archive validation; new outputs only, 0700 directories/0600 files, preserve partial output.
- No provider, broker, credentials, external messages, browser, main merge or actual activation. Readiness remains false.
- Continuous authorized work through guarded feature publication and Draft title/body update; no repeated approval menu.

## Review Focus

1. A source or destination is replaced after a good earlier check: retained original FDs must make final success impossible — Task 2 substitution/fsync regressions.
2. An archive carries parser extensions, huge sizes, duplicate members or trailing data: validation must stay bounded and must create no destination — Tasks 1/2 malformed archive cases.
3. Host/site/editable imports conceal a missing release file: isolated off-checkout execution must fail rather than fall back — Task 3 native/worker provenance tests.
4. A moved runtime is confused with compatible account bytes: original service root/interpreter bindings must still refuse — Task 3 relocation test.
5. Output or signal failure follows partially completed extraction/publication: private error precedence and preserved output must survive — Tasks 2/3 actual subprocess and fault tests.

## Task 1: Canonical release profile and archive validation

**Files:** Create `apps/server/trading_bot/paper_release_format.py` and `tests/test_paper_release_format.py`.

**Interfaces:** Standard library only. Produce `ReleaseInvalid(ValueError)`,
`RELEASE_FILES`, `ENTRYPOINTS`, `ENGINE_FILES` (module-name -> relative path),
`MAX_ARCHIVE_BYTES`, `MAX_FILE_BYTES`, `MAX_MANIFEST_BYTES`, `MANIFEST_NAME`.
`encode_archive(files: dict[str, bytes]) -> bytes` requires exact payload keys.
`decode_archive(raw: bytes, expected_sha256: str) -> dict[str, bytes]` returns all
payloads plus `MANIFEST_NAME`; its result is trusted only in the current call.
`execution_fingerprint(files: dict[str, bytes]) -> str` derives the unchanged
native digest for current Python without importing archived code.

- [x] Write behavior tests for deterministic standards-readable output, exact payload/metadata, external digest mismatch and hostile canonicality cases; run RED for the missing module.
- [x] Implement explicit profile, strict JSON and bounded raw USTAR header/payload/padding validation; never call generic extraction.
- [x] Exercise oversized/duplicate/traversal/link/PAX/sparse/compressed/trailing data and integer/depth errors with independently constructed malformed fixtures.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_release_format.py' -v`; expect all pass, no capability skips.

## Task 2: Stable build, check, extraction and tree verification

**Files:** Create `apps/server/trading_bot/paper_release.py` and `tests/test_paper_release.py`.

**Interfaces:** Consume Task 1 constants/functions. Produce
`ReleaseUnavailable(ValueError)`, `ReleaseStorageError(OSError)`,
`ReleaseInterrupted(signum)` with `.signum`, and these synchronous APIs:

```text
build_release(output, *, stop=None) -> dict
check_release(archive, *, expected_sha256, stop=None) -> dict
extract_release(archive, directory, *, expected_sha256, stop=None) -> dict
verify_release(directory, *, expected_sha256, stop=None) -> dict
```

`_ROOT` is the fixed code root, not a user option. Test utilities may patch it
to a controlled complete source fixture. Reports obey the spec and statuses
`built`, `verified_archive`, `extracted`, `verified_tree`; errors are fixed labels.
No source execution or subprocesses in this layer. Reuse unchanged private IO
helpers where they fit; exact inventory must handle the explicit profile's
directory counts without changing `paper_backup_io.MAX_NAMES`.
APIs hold all original observations through their final filesystem checks and
return a point-in-time dict after closing FDs. CLI serialization is outside that
lifetime; it must not reopen names or claim validation-to-execution atomicity.

- [x] Write actual filesystem tests for no-clobber, SHA mismatch before mkdir, exact private tree, deterministic copies, exclusion of unlisted sensitive files, read-only checks and descriptor cleanup; observe RED.
- [x] Implement held source/archive/destination observations and complete final checks, canonical artifact publication and bounded extraction.
- [x] Add fault cases for hardlinks/symlinks/FIFO, path substitution during read/final fsync, missing/extra tree files, short writes and fsync faults, numeric stop validation and interruption; observe RED for each repaired behavior.
- [x] Run `python -m unittest discover -s tests -p 'test_paper_release.py' -v`; expect all pass without network or runtime activation.

## Task 3: CLI and isolated runtime integration

**Files:** Create `tools/paper_release.py`, `tests/test_paper_release_cli.py`,
`tests/test_paper_release_runtime.py`, test-only subprocess fixtures as needed,
and `docs/PAPER_RELEASE.md`.

**Interfaces:** Consume Task 2 APIs. The CLI accepts only the four spec forms,
no option abbreviation/repetition/help bypass, and emits one private JSON result.
The runtime guide is part of the fixed payload and contains reproducible commands
and explicit trust/runtime/path/activation boundaries.

- [x] Write subprocess argument/output/no-clobber/digest tests and observe RED before the CLI.
- [x] Implement bounded JSON-only dispatch, numeric first-signal handlers, fixed error codes and final stdout failure handling.
- [x] Build and extract outside the checkout, then execute real native synthetic capture/account/audit and backup/recovery with `-I -S -B` and network denial. Compare the original 24-source fingerprint and unchanged original data bytes.
- [x] Exercise extracted fixed worker launch and native guarded service behavior with real sockets where available; require them in Linux CI. Test wrong runtime root and no-checkout import provenance explicitly.
- [x] Run all `test_paper_release*.py`; inspect real artifacts, native audits and reports, then compare all frozen bytes.

## Task 4: CI, review and verified publication

**Files:** New `.github/workflows/paper-release.yml`; update README,
PRODUCTION_CHECKLIST, operations guide and CURRENT_HANDOFF.

- [x] Add Python 3.10/3.12/3.13 jobs that do not install dependencies, use isolated interpreters, require actual Unix sockets, build twice, compare exact bytes, extract, verify and exercise the runtime. Preserve release archives/digests as CI artifacts only.
- [x] Run the complete root unittest suite, package/server pytest and all Node client tests; inspect actual results and capability exclusions. Avoid unrelated Rust re-testing locally when final CI already owns its unchanged build gate.
- [x] Perform a fresh independent complete-increment review; reproduce and fix material findings with regression tests and necessary full gates.
- [x] Update operator documentation/handoff with observed evidence and remaining limits; commit a clean tested tree.
- [ ] Publish exact tree through expected-head non-force feature update, preserving main and Draft state. Inspect every applicable final-head workflow/job and actual logs, including the new artifact jobs.
- [ ] Guard original PR title/full body, update only those fields, then independently reread refs/tree/parents/metadata/CI/local cleanliness. Preserve final identities in PR and ignored receipts.

This tracked plan is a prepublication checkpoint. Exact future commit, artifact
digest and final CI identities belong in the PR and delivery receipts; no
self-referential follow-up commit is needed merely to record its own identity.
