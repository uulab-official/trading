# Paper Operations Implementation Plan

> Execute inline using superpowers:executing-plans; no subagent tool is available.

**Goal:** A verified, restart-safe order-admission journal connected to an honest local operations dashboard.
**Architecture:** Extend existing SQLite journal; expose read-only transactional snapshots through the loopback server; render operational state, not fabricated account performance.
**Tech Stack:** Python >=3.10 standard library, SQLite, plain browser modules; Node built-in tests, optional Playwright browser smoke test.
**Spec:** ../specs/2026-10-31-paper-operations-design.md

## Constraints / review focus
No broker calls or public deployment. Preserve old entrypoints. Test old cancelled rows whose notional is lost; malformed database health; concurrent reservation races; odd database filenames; stale UI after successful data.

## Tasks
- [x] Journal: write failing tests in `tests/test_paper_operations.py`; preserve original notional, migrate atomically, append audit events with transitions in `packages/core/order_journal.py`; test restart/concurrency/rollback.
- [x] API: reproduce false-positive health and special-path failures; implement `apps/dashboard/store.py` snapshots and harden `server.py`; test `tests/test_dashboard_operations.py` including Host/Origin/read-only/closing/filters.
- [x] UI: external `app.mjs`/`styles.css`, real summary and recovery states in `index.html`; write `tests/dashboard_client.test.mjs`, test desktop/mobile in Chromium.
- [x] Integration: `tools/paper_operations_demo.py` creates explicitly synthetic sample lifecycle only at a new path; tests run demo twice to prove overwrite refusal. Update startup/security/limitations documentation and CI checks.
- [ ] Verify: run targeted Python/Node suites and browser smoke test; inspect diff; publish only verified changed files as one Git tree/commit on existing feature branch using its current HEAD as parent; read back the commit and check CI.

## Execution record
- Upstream baseline `4b92d793193d8e402f823f7d7ac55bfbe66ac5ee` read via GitHub connector. Network DNS prevented a full clone in this container; local targeted snapshot is explicitly not the full repository.
- Original journal/server content hashes match upstream blobs before modification.

- Self-review (no independent subagent available) added regressions for lost venue IDs, corrupted replay statuses and mixed timestamp sorting; four reproduced failures fixed before final verification.
- Browser HTTP is blocked by environment policy; no policy changes attempted. Offline browser rendering and real Python HTTP integration tests are reported separately.
