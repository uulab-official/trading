# Paper order detail implementation plan

> Execute inline with superpowers:executing-plans. No independent subagent tool is available.

**Goal:** Show exact persisted fill evidence for a selected order without changing it.
**Architecture:** Extend the current observer with a pinned projection and a GET route; add a small independently tested detail panel to the existing browser.
**Tech Stack:** Python 3.10+, SQLite, native JavaScript modules; no new dependencies.
**Spec:** docs/PAPER_ORDER_DETAIL.md

## Global Constraints
- No live/provider requests, account writes, schema changes, main merge or deployment.
- Both list anchors mandatory, amounts remain strings; no invented cause or timeline.
- Existing v1/v2/v3 sessions and list contracts unchanged.
- Publish only this increment to the existing Draft PR branch, preserving concurrent changes.

## Review Focus
- Lost acknowledgement with account fill: display both facts without confirming it.
- Empty fill: null values, no rejection or zero-price inference.
- Commit/replacement between reads: 409/503; never fall back to latest detail.
- Encoded/control-character IDs: exact round trip or strict rejection before I/O.
- Superseded page/detail/timeout: stale results never replace the current selection.

## Tasks
- [x] Write and run failing SQLite/API detail tests, then implement pinned projection/route.
- [x] Write and run failing client contract/state tests, then implement detail panel/navigation invalidation.
- [x] Exercise real local HTTP, existing scoped regressions and offline DOM rendering; review failure paths.
- [ ] Package focused patch/evidence, publish exact tested files and check remote CI.

## Execution ledger
- Scope chosen from current v26: an embedded detail panel, not a second execution/control surface.
- Full clone failed DNS; local support reconstructed from supplied artifacts. Exact changed base files and critical dependencies are hash-checked where available. Do not claim full-checkout local coverage.

- Backend/API: 21 initial failure-first tests, then 23 passed after adding identity-race and decimal-context cases. Missing-ID projection initially skipped final identity check; reproduced and corrected by finishing observation before emitting not-found.
- Local scoped suite: 238 tests passed in one completed run. An earlier run failed three import-boundary cases due to an absent existing CLI in the partial tree; the exact original CLI was restored (Git blob 76829c267db9468161baef9a695c4677601e64bf), not altered.
- Client: 16 new + 16 original page tests passed. Chromium offline rendering passed 1440/390/320px and dark mode; actual browser localhost was policy-blocked, not bypassed.
- Review was inline; no independent reviewer tool available. Rendering harness initially used a wrong existing method name and missing Playwright-bundled binary; corrected to the actual API and installed system Chromium before completed checks.
