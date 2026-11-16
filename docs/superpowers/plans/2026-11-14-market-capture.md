# Continuous market capture implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Continuously capture bounded public closed-candle history with explicit continuity/freshness and immutable historical export.
**Architecture:** Reuse the original Upbit client/fixture validator; add a private transactional rolling store, bounded transport and collector, then a passive status UI. Existing historical execution remains explicit and unchanged.
**Tech Stack:** Python >=3.10 standard library; existing dependency-free HTML/CSS/ES modules.
**Spec:** docs/superpowers/specs/2026-11-14-market-capture-design.md

## Global Constraints

- Base dd1713276ea5a4580193fca12b62b17bde73e5c5; existing feature/Draft delivery only; main unchanged.
- No credentials, broker/order calls, live activation, external messages, deployments or browser-policy workaround.
- Original imported fixture, four-source readiness, account/recovery schemas and all16 execution fingerprints unchanged.
- Original validators/Decimal math reused; no fake candles, hidden repeated replay, source rewriting or automatic account resume.
- Exact APIs, fields, limits, phases and exit semantics are in the spec; deviations require a recorded ruling before dependent work.

## Review Focus

- A hard provider refusal or long cooldown reaches the outer loop: no retry-policy bypass.
- A response arrives after a signal or clock regression: no late accepted data or false freshness.
- The last committed overlap is revised or omitted: retain previous complete state and surface conflict/rejection.
- A writer/parent/DB changes during capture or observation: fail closed without corrupting or relabeling another capture.
- Retention evicts data or a process dies after a commit: current window/age and saved phase cannot imply a complete archive or current liveness.

## Task 1: Durable rolling capture store

**Files:** new apps/server/trading_bot/market_capture_store.py; tests/test_market_capture_store.py.
**Interfaces:** Exact validate_config/read_capture/capture_writer and writer.read/attempt/apply/fail/stop from the spec; original validated fixture in/out; shared CaptureUnavailable/CaptureConflict/CaptureStorageError.

- [x] Write real-file/SQLite tests for new/resume binding, double ownership, normal progress, unchanged overlap, conflicting/missing overlap, rollback, explicit retention counts, bounds, read-only observation and changed paths; run RED.
- [x] Implement private ownership, fixed bounded schema and atomic candle/state transitions with original fixture validation.
- [x] Verify final snapshot/bytes are preserved on failures; run focused stdlib suite and report exact evidence.

## Task 2: Bounded provider transport and retry classification

**Files:** packages/data/trading_data/upbit_history.py; new packages/data/trading_data/public_candle_transport.py; tests/test_public_candle_transport.py; tests/test_market_retry_policy.py.
**Interfaces:** PublicCandleTransport(timeout=10.0, connection_factory=None) callable; validate_public_url; MarketDataUnavailable.reason/retryable/retry_after additive contract.

- [x] Write behavior tests for fixed endpoints/proxy bypass/no redirects, response cap/total deadline, final-attempt429 cooldown and terminal418/invalid cooldown; run RED.
- [x] Reuse/extract original URL validation, preserve original import exits/retries, and implement total socket-I/O deadline with bounded resource cleanup.
- [x] Run new tests and all existing Upbit/import/market provenance tests; report actual timing/loopback results.

## Task 3: Collector, CLI and read-only status API

**Files:** new apps/server/trading_bot/market_capture.py; tools/market_capture.py; tests/test_market_capture.py; tests/test_market_capture_cli.py; tests/test_market_capture_api.py; modify apps/dashboard/server.py.
**Consumes:** Tasks1/2 exact APIs. **Produces:** collect_capture/capture_status/export_capture and exact independent status contract from spec.

- [x] Write progression/retry/hard-stop/gap/conflict/restart/clock/budget tests with original Upbit parsing and real store; run RED.
- [x] Implement bounded poll/catch-up orchestration, separate data/observation age, explicit stop/failure semantics and immutable original-fixture export.
- [x] Write actual CLI, SIGINT/SIGTERM, invalid-input-before-network and read-only API tests; run RED and implement commands/route without changing existing readiness.
- [x] Run focused suites plus old dashboard/probe/market integration; verify exported fixture with the original inspector and original engine.

## Task 4: Operator status page and guide

**Files:** new apps/dashboard/market-data.html, market-data.css, market-data.mjs; tests/market_capture_client.test.mjs; docs/MARKET_CAPTURE.md. Existing nav links and top-level documentation owned by root integration.
**Consumes:** Exact independent GET status contract. **Produces:** passive operator UI and accurate collect/check/export/restart instructions.

- [x] Write client tests for status/schema errors, exact values, failed refresh preserving data, overlapping requests and no execution controls; run RED.
- [x] Implement existing-style responsive status page, explicit current/stale/failure states and bounded hidden-tab-aware polling.
- [x] Document rolling retention, cooldown/overlap/clock rules, signal/storage exits and explicit export-to-paper handoff; no visual/soak claim.
- [x] Run Node suites and inspect document commands against implementation.

## Task 5: Integration and verified delivery

**Files:** .github/workflows/quant-regression.yml; dashboard asset/nav registration; README.md; PRODUCTION_CHECKLIST.md; docs/PRODUCTION_PAPER_OPERATIONS.md; docs/CURRENT_HANDOFF.md; this plan.

- [x] Add isolated capture/transport/API suites and Node client to existing CI; preserve required gates.
- [x] Run complete unittest, package/server pytest and Node regression; fix concrete failures.
- [x] Execute guarded synthetic continuity/reconnect/export/original-paper demonstration and verify private modes/source preservation.
- [x] Obtain fresh complete-increment review and resolve concrete findings.
- [ ] Record final evidence/handoff and publish exact verified tree with expected-head non-force update.
- [ ] Verify final-head CI, update PR preserving concurrent metadata and independently audit refs/PR/main/local cleanliness.
