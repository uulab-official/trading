# Production operations hardening implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Bound and observe the implemented local paper/research operating path and make all readiness claims truthful.
**Architecture:** Preserve the original execution/account validators. Add a bounded observer runtime and shared stable report I/O, then expose narrow readiness and an operator probe.
**Tech Stack:** Python >=3.10, standard library HTTP/SQLite/filesystem, existing test/runtime dependencies.
**Spec:** docs/superpowers/specs/2026-11-14-production-operations-design.md

## Global Constraints

- Base 2c3bcf288deb62160c09c85fba195636e8e6afb0 on feat/smc-market-structure.
- Local paper/read-only observation only; no broker or credential activation.
- Existing engine/account schemas, arithmetic and execution fingerprints remain unchanged.
- Original report validators, result schemas, idempotence and no-clobber behavior remain.
- Exact bounds and probe contracts are in the spec; do not silently change them.
- Python 3.10 compatible; stdlib-only isolated dashboard/research suites remain runnable.
- No browser-policy workaround; actual visual QA remains unverified.
- Feature/Draft PR delivery authorized; main unchanged.

## Review Focus

- Slow incomplete requests continue sending before inactivity timeout: absolute age still closes them.
- A configured source fails while another remains valid: readiness is degraded without hiding either.
- A file or parent changes between observation and publication: failure cannot become reported success.
- Very large or deeply nested database values: no partial complete-looking totals or uncaught tracebacks.
- Complete-looking live settings: no runtime-ready assertion from configuration alone.
- A signal interrupts the owner's wait while its condition lock is held: the handler cannot reenter that lock.

## Task 1: Bound journal observation

**Files:** apps/dashboard/store.py; tests/test_dashboard_store_limits.py.
**Consumes:** read_snapshot(path, *, limit=100, offset=0, status=None, event_client_id=None).
**Produces:** same response/exception contract with spec bounds and page-only retention.

- [x] Write real SQLite tests for maximum/surplus order counts, huge fields, recursion, budget exhaustion and source immutability; run RED.
- [x] Implement file/row/payload/event/metadata limits and SQL/elapsed work budget inside one transaction.
- [x] Keep full bounded validation/totals while retaining only the requested page.
- [x] Run new tests and existing dashboard/journal tests; inspect maximum-size fixture timing.
- [x] Review diff and commit only task-owned files.

## Task 2: Stable report observation and confirmed publication

**Files:** new apps/server/trading_bot/local_report_io.py; apps/server/trading_bot/research_report.py; apps/server/trading_bot/replay_report.py; tools/quant_research.py; tools/paper_replay_dashboard.py; tests/test_report_io_durability.py.
**Consumes:** original read_report/write_report and input/report validators.
**Produces:** read_regular_bytes(path, *, max_bytes) -> bytes; publish_new_report(path, body, *, on_exists=None) -> None or resolved collision result; ReportPublicationUncertain(OSError), with original public report APIs intact.

- [x] Write real-file/FIFO/symlink/replacement and directory-fsync-failure tests; run RED.
- [x] Implement stable bounded observation and confirmed anchored no-clobber publication in the shared stdlib module.
- [x] Route both report readers/writers and quant fixture/candidates through these primitives; retain original exact equal-report behavior.
- [x] Preserve files and original I/O exit statuses on uncertain publication with fixed CLI diagnostic.
- [x] Run new and original replay/research suites, including actual original-engine exports; commit task-owned files.

## Task 3: Bounded local runtime, probes and shutdown

**Files:** apps/dashboard/server.py; new apps/dashboard/runtime.py; tools/paper_dashboard_check.py; tests/test_dashboard_runtime.py; tests/test_dashboard_readiness.py; tests/test_dashboard_probe.py.
**Consumes:** unchanged journal/replay/research/account readers.
**Produces:** compatible create_server/serve; additive liveness/readiness routes; fixed local CLI probe.

- [x] Write actual socket/subprocess tests for 8-worker cap, slot reuse, idle/trickle expiry, signal shutdown and port reuse; run RED.
- [x] Implement runtime limits and clean signal ownership/restoration without same-thread shutdown deadlock.
- [x] Reproduce and repair signal-time wait-lock reentrancy; accept TCP EOF or RST while retaining process deadlines and port-release assertions.
- [x] Write configured-source readiness and no-source-access liveness tests, malformed/foreign requests, HEAD and sanitized errors; run RED.
- [x] Implement additive routes using original readers once per configured source; preserve old /api/health.
- [x] Write CLI integration tests for ready/degraded/unavailable/wrong contract, proxy bypass, bounded response and flags; run RED.
- [x] Implement strict fixed-loopback probe and sanitized startup errors; run all dashboard/API/client suites.
- [x] Review and commit task-owned files.

## Task 4: Configuration truth and operating guide

**Files:** packages/core/trading_core/config_manager.py; packages/core/trading_core/config_cli.py; tests/test_config_readiness.py; README.md; PRODUCTION_CHECKLIST.md; apps/dashboard/README.md; docs/WEB_DASHBOARD.md; new docs/PRODUCTION_PAPER_OPERATIONS.md.
**Consumes:** Task 3 endpoint/probe contract; actual implemented runtime constraints.
**Produces:** configuration-only predicate, false production-readiness predicate and accurate CLI/runbook.

- [x] Write tests showing complete live settings do not certify runtime readiness; run RED.
- [x] Add is_live_configuration_complete and make legacy readiness conservative; update CLI wording while preserving validation exits.
- [x] Replace obsolete operational instructions with exact supported commands and evidence boundaries.
- [x] Run config tests and inspect each documented command against help/actual synthetic operations.
- [x] Review and commit task-owned files.

## Task 5: Integration, verification and delivery

**Files:** .github/workflows/quant-regression.yml; docs/CURRENT_HANDOFF.md; this plan.
**Consumes:** all task outputs.
**Produces:** exact-head verified feature branch and updated Draft PR with evidence.

- [x] Add new stdlib suites to required isolated CI and preserve all existing gates.
- [x] Run full development unittest, package/server pytest and Node suites; resolve concrete failures.
- [x] Execute a guarded synthetic operating demonstration, including service/probe/unavailability/termination and unchanged source bytes.
- [x] Obtain independent final review of complete base-to-head diff and address concrete findings.
- [x] Update handoff/plan with final code, review and regression evidence.
- [ ] Publish exact tested tree with expected-head fast-forward.
- [ ] Confirm final-head CI, update Draft PR without overwriting concurrent metadata, audit remote/main and local cleanliness.

Publication-dependent checks follow this immutable handoff commit. Their final
outcomes belong to the current Draft PR description and head-filtered GitHub
Actions runs, plus the ignored local delivery receipt; an older commit's checked
plan cannot establish a later head's verification.
