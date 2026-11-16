# Market study report implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the
> isolated renderer and superpowers:executing-plans for CLI integration; finish
> with an independent review of the complete increment.

**Goal:** Make existing audited historical study results readable in one private HTML report.
**Architecture:** The unchanged checker supplies one immutable observation to a
pure renderer; the existing CLI optionally publishes it outside the study.
**Tech Stack:** Python 3.10+, stdlib, unittest, existing POSIX private publisher.
**Spec:** docs/superpowers/specs/2026-11-12-market-study-report-design.md

## Global Constraints

- Keep PR #1 Draft; no main merge, deployment, credentials, broker orders or live activation.
- Do not modify fingerprinted engines, account schemas or v38 receipts.
- No new dependencies, network assets, automatic recovery, ranking or return aggregation.
- Keep JSON stdout and existing check exit codes; private new HTML output is optional.
- Preserve all candidates and distinguish receipt validation from child completion.

## Review Focus

- All children complete but receipt missing/corrupt: never display verified study or comparison deltas.
- A blocked child after an earlier completed child: retain plan order, hide only unsupported metrics.
- User text contains HTML or an attribute payload: all output remains inert text.
- Output aliases a source directory or an existing file: no modification to either.
- Inspection or publication fails: no success exit, repair, retry or deletion of evidence.

## Task 1: Pure static report renderer

**Files:** create `apps/server/trading_bot/market_study_html.py` and
`tests/test_order_market_study_html.py`.
**Interfaces:** consumes the unchanged dict from `inspect_market_study`; produces
`render_study_report(report: dict) -> str`. Invalid envelopes raise ValueError
with the fixed message `market_study_report_invalid`.

- [x] Write failure-first renderer cases for verified metrics/units/order,
  incomplete/missing/corrupt receipt states, blocked/missing rows, escaping,
  immutable input, rejected execution envelope, unknown/zero values and no active content.
- [x] Run `python -m unittest discover -s tests -p 'test_order_market_study_html.py' -v`.
  Expected: failure because the renderer is not implemented.
- [x] Implement a Korean, responsive, self-contained HTML document. Reuse the
  study's metrics without calculations. Gate deltas on full receipt verification.
- [x] Run the same command. Expected: every renderer test passes.
- [x] Review and commit the focused implementation with its tests.

## Task 2: CLI HTML export

**Files:** modify `tools/paper_study.py`; create
`tests/test_order_market_study_report.py`.
**Interfaces:** consumes `render_study_report(report)` and existing `_publish`;
adds only `check DIRECTORY --html NEW_FILE`. Errors use existing fixed JSON.

- [x] Write failure-first CLI cases using original synthetic studies. Assert
  actual JSON/exit codes, no source changes, private HTML file, no clobber,
  source alias refusal, early parent refusal and output uncertainty exit 3.
- [x] Run `python -m unittest discover -s tests -p 'test_order_market_study_report.py' -v`.
  Expected: failures caused by unsupported `--html`.
- [x] Add output validation before inspection and again before publication;
  use the existing no-clobber publisher. Preserve check's result and status code.
- [x] Run the same command and the v38 study suite. Expected: all tests pass.
- [x] Review and commit the focused integration with its tests.

## Task 3: Delivery and verification

**Files:** update `docs/MARKET_PAPER_STUDY.md`, `README.md` and
`.github/workflows/order-manager-safety.yml`; add `docs/CURRENT_HANDOFF.md`.

- [x] Document exact usage, output privacy, incomplete reports and retained limits.
- [x] Include renderer path in CI filters; new tests match existing discovery.
- [x] Run actual synthetic study/check/export.
- [ ] Inspect desktop/mobile rendering — blocked by the available browser environment; disclosed.
- [x] Run order regression, Python package/application regression and Node suites.
- [x] Review the complete diff independently; fix material findings with regressions.
- [ ] Commit and push to the existing development branch, then verify remote CI.
- [x] Record verified state and next work in the handoff to survive session loss.

## Execution record

- Baseline: `00712a42e26fd674dc9921b50d6f99303e01c98d`, clean clone and existing
  development branch. All three latest-head workflows succeeded remotely.
- Baseline local v38 study tests: 35 passed; existing datetime.utcnow deprecation
  warnings observed in legacy strategy modules. No engine changes are needed.
- Renderer and CLI can proceed concurrently because they own different files
  and share only the fixed function contract above. Root owns integration/delivery.
- Renderer: commit `9beafee`; 11 consumer behavior tests passed after RED.
- CLI: 15 real-study integration tests passed after RED, including a discovered
  explicit empty `--html` value that previously skipped export silently.
- Independent review found one important publication race after the final
  path-only check. Two additional failure-first cases now cover a publisher-entry
  symlink swap and same-inode relocation into the study. The optional publisher
  binding pins both the canonical parent path and its device/inode; default
  publication behavior remains unchanged. Final CLI integration: 17 passed.
- Existing private-evidence publisher regressions: 35 passed. Package/application
  suite: 309 passed with 382 subtests; Node clients: 79 passed.
- Three-candidate synthetic CLI study/check/export succeeded with a verified
  receipt. No real market data acquisition or performance claim.
- Browser QA limitation: local Browser has no connection; Cloud Browser rejects
  the local file protocol. No security-policy workaround was attempted. Actual
  desktop/mobile browser rendering remains unverified in this environment.

## Validation-driven legacy compatibility repair

The first broad package/application run exposed failures already present at
`00712a4`, reproduced in an isolated archive of that commit. The default empty
`Signal.strategy_id` is incompatible with the newer OrderManager identifier
validation. Once that boundary is corrected, the current serialized
`cancel_requested=False` field is rejected by the legacy checkpoint reader.
One lifecycle test waits for a create-order event that cannot occur after the
earlier identifier failure; this explains the interrupted broad test run.

Ruling: repair these two legacy compatibility boundaries and include application
tests in CI, which previously ran only `packages/`. The fix remains outside the
fingerprinted historical runner and keeps strict OrderManager validation. Old
checkpoints without the optional field and new records explicitly containing
boolean False may restore; active/malformed cancellation flags stay blocked.
Cost if wrong: a legacy paper account could be mis-restored; explicit failure-first
roundtrip and byte-preservation tests are required before delivery.

**Files:** `apps/server/trading_bot/main.py`,
`packages/core/trading_core/legacy_checkpoint.py`,
`apps/server/tests/test_legacy_lifecycle.py`, `.github/workflows/ci.yml`.

- [x] Reproduce both failures at the original branch baseline.
- [x] Normalize only empty-string IDs at legacy BUY handoff sites.
- [x] Validate backward-compatible cancellation metadata without weakening recovery.
- [x] Run legacy lifecycle/restart/checkpoint/valuation tests and the full package/application suite.
- [x] Include this small addition in the independent final review.

Ruling: retain the existing exact package coverage-command contract and add server
regressions as a separate required CI step, appending coverage. The old contract
test remains unchanged; a failing server test still blocks the workflow.

Final review boundaries: the renderer delegates receipt authentication to the
existing checker and does not invent candidates when that checker cannot load a
study. Visual browser QA remains unavailable. Investment quality, provider
authenticity, live-readiness and comprehensive legacy-system certification are
outside these verified claims; no minor review findings were deferred.

Final local tree verification: 887 development regressions; 309 package/application tests with382 subtests;79 Node client tests, all passed. Shared publisher default behavior is retained.
Delivery uses a consolidated GitHub API commit because ordinary git push has no credential. Compare the created tree against the exact tested local HEAD tree; update only the existing development ref with expected original head and force=false. Remote CI is verified after this document is committed.
