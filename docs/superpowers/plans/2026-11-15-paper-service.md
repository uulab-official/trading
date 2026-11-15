# Supervised PAPER Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Reliably supervise one existing PAPER pair with parent-death containment, explicit lifecycle and fresh bounded process-health evidence.

**Architecture:** Add a private state/preflight layer, fixed guarded worker protocol and selector-driven foreground coordinator. Preserve original runtime APIs/files; add strict CLI and inert systemd generator.

**Tech Stack:** Python>=3.10 stdlib, Linux prctl/procfs/Unix sockets, POSIX flock/fsync, unittest and existing CI.

**Spec:** `docs/superpowers/specs/2026-11-15-paper-service-design.md`

## Global Constraints

- Preserve all24 frozen modules and existing v47 backups; no dependency changes.
- Linux-only guarded foreground processes; no respawn, daemon/PID control or live activation.
- Configuration16KiB; frame2048bytes; probe request256/response8192; stderr256KiB per child; attempt result64KiB; at most64 attempts.
- startup10..900(default180), heartbeat5..120(default30), stop1..60(default15); forced reap5seconds.
- Fixed native resume only; capture derived from account; existing terminal states remain binding.
- No provider/broker calls in tests or delivery; network denial inside each test interpreter.
- Publish exact tested feature tree, preserve main and Draft status; record final identities in PR/ignored receipts.

## Review Focus

1. Parent death before/after guard ACK must leave no active worker or released controller with inherited lease — worker/core real subreaper tests.
2. Old current snapshots, replaying or SIGSTOPed workers must not yield healthy service — core live probe/progress tests.
3. Failure or signal while pipes are full must still stop/reap both children; storage/refusal priority survives — core flood/signal/fault tests.
4. Config/parent/socket/attempt substitution must never redirect publication or silently adopt another process — state/core identity tests.
5. Native waiting/terminal outcomes and explicit restart must preserve economics and source policy — native integration/CLI tests.

## Task1: Private configuration, preflight and attempt evidence

Files: `apps/server/trading_bot/paper_service_state.py`, `tests/test_paper_service_state.py`.
Produces ServiceUnavailable/Refused/StorageError/Interrupted; prepare_pair;
configure_service; ServiceDirectory; Attempt as specified. Consumes unchanged
readers/_SourceBinding/_verify and private IO helpers, with strict persisted-document validators.

- [x] Write real-pair/private-file/held-owner/fingerprint/terminal tests and observe missing-feature RED.
- [x] Implement immutable pair identity, full preflight, new private config and held service lease.
- [x] Implement bounded consecutive attempts, held-FD health socket and no-clobber final evidence.
- [x] Cover source/target changes during final sync, partial publication and fork/lease behavior.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_paper_service_state.py' -v`; local gate passed; actual socket capability limits are recorded below.

## Task2: Guarded worker protocol and native adapter

Files: `apps/server/trading_bot/paper_service_protocol.py`,
`apps/server/trading_bot/paper_service_worker.py`, `tools/paper_service_worker.py`,
`tests/test_paper_service_worker.py`.
Produces canonical encode/decode helpers, FrameReader(role,attempt),
encode_frame(event,attempt,role,sequence,**result_fields),
arm_parent_death(parent_pid), worker_main(role,directory,attempt,parent_pid).

- [x] Write parser/ordering/limits and real subprocess guard tests, observe RED.
- [x] Implement exact frames and Linux parent-before/after guard contract before native imports.
- [x] Use native stop/sleep hooks for ownership/progress and preserve terminal final observations.
- [x] Test real parent death/subreaper cleanup, both signals, no runtime entry on parent mismatch and unchanged sleep semantics.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_paper_service_worker.py' -v`; local gate passed; actual socket capability limits are recorded below.

## Task3: Foreground supervision and live probe

Files: `apps/server/trading_bot/paper_service.py`, `tests/test_paper_service.py`,
`tests/paper_service_fixtures.py`, `tests/test_paper_service_integration.py`,
`tests/test_paper_service_monitor.py`.
Consumes Task1/2 contracts; produces run_service/probe_service and reexports
configure_service. Private `_spawn_worker(role,directory,attempt,parent_pid)`
is the sole process-launch seam; production always uses fixed isolated argv.

- [x] Write real-process no-respawn/startup/signal/probe tests, observe RED.
- [x] Implement ordered native ownership startup and bounded selector processing.
- [x] Implement same-UID read-only challenge probe and monotonic native progress deadlines.
- [x] Implement consumer-first cooperative stop, forced cleanup and priority-preserving outcomes.
- [x] Cover malformed/flooded streams, child/SIGKILL/parent death, path mutations and duplicate owners.
- [x] Run native synthetic pair -> clean stop -> full audit -> explicit restart -> stop, verifying economics/IDs and internal network denial.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_paper_service*.py' -v`; local gate passed; actual socket capability limits are recorded below.

## Task4: Operator CLI, systemd generation and runbook

Files: `tools/paper_service.py`, `apps/server/trading_bot/paper_service_unit.py`,
`tests/test_paper_service_cli.py`, `docs/PAPER_SERVICE.md`.
Consumes Task1/3 public APIs. Commands configure ACCOUNT --directory SERVICE
with exact timeout options; run SERVICE; check SERVICE; unit SERVICE --output NEW.

- [x] Write strict args/private-error/signal and unit no-clobber tests, observe RED.
- [x] Implement one-result CLI, fixed exit policy and handler restoration.
- [x] Generate inactive conservative-path systemd user unit with exact stop budget/containment settings.
- [x] Validate actual generated units using `systemd-analyze verify`; never install/start them.
- [x] Document explicit lifecycle, health meaning, private evidence, attempt cap and actual platform limits.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_paper_service_cli.py' -v`; local gate passed; actual socket capability limits are recorded below.

## Task5: Integrated review, compatibility and delivery

Files: CI workflows, README, PRODUCTION_CHECKLIST, operations/current handoff;
ignored evidence and exact-tree publication receipts.

- [x] Add service tests/compilation to applicable installed Python3.10/3.12/3.13 CI jobs and path filters.
- [x] Verify all24 frozen files byte-identical and an existing v47 archive still fully checks unchanged.
- [x] Run full unittest, package/server pytest and Node; inspect actual results.
- [x] Fresh independent whole-increment review; reproduce/fix material findings and rerun affected required gates.
- [x] Update docs with observed results and unverified operational limits; commit clean tree.
- [ ] Publish using expected-head non-force update; check all applicable final-head workflows/jobs/logs.
- [ ] Guard title/body against saved baseline, update only Draft metadata and independently reread final refs/tree/metadata/CI.

The tracked plan is an immutable prepublication checkpoint. Actual commit/CI
identities and completion of the last delivery steps are recorded in PR/receipts.

The final local whole-suite gate ran 1,508 methods in 274.005 seconds: 1,505
passed and exactly three AF_UNIX capability skips. Its service subset includes
143 methods: 140 passed and the same three skips. Real process/native tests run
with an explicitly test-only inert pipe listener when local sockets are
prohibited. Linux CI requires actual sockets through PAPER_SERVICE_REQUIRE_SOCKET=1;
no skipped socket claim counts as remote verification. Package/server pytest
passed 309 tests plus 382 subtests in 14.36 seconds, with 926 existing warnings.
Node passed all 103 tests with no failures or skips in 285.247399 ms.
Fresh full review found zero Critical, zero Important and one Minor issue; the
final-output Minor was reproduced in eight subcases and repaired, with all 28
CLI tests passing. Remote publication and actual-socket final-head CI remain the
following receipt-backed steps.

Actual-socket CI on the first candidate9129927 exposed two prelaunch refusals in
one Python3.12 service job. The SH-probe/EX-admission race was reproduced with an
actual separate-process probe barrier, then repaired by bounded one-second
admission before native work. The optional numeric stop callback reaches admission.
Eight new regressions and independent repair review pass; the failed candidate is
preserved as evidence. The final repaired-tree whole suite above has passed;
new-head actual-socket CI is required before final completion.
