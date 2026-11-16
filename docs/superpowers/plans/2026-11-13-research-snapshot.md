# Research Snapshot Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for the integration and verification, with an independent renderer workstream and one fresh whole-increment review. Steps use checkbox syntax for tracking.

**Goal:** Inspect saved walk-forward research and export a private standalone Korean HTML snapshot.

**Architecture:** Reuse the existing research reader/validator, render its exact values, and use the established private no-clobber publisher. Keep the producer, execution engines and study receipt path unchanged.

**Tech Stack:** Python >=3.10 standard library, semantic HTML/CSS, existing unittest and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-13-research-snapshot-design.md`

## Global Constraints

- Base `111ef2ea26db646e36b4081979ea303ecfd3fdcf`; existing feature branch and Draft PR.
- No engine/contract changes, new dependency, network acquisition, live action or main merge.
- Schema-1 diagnostics unavailable, schema-2 original diagnostics retained; exact metric strings.
- Validation means saved-report consistency, not authenticated source or execution.
- Private new HTML, canonical-parent/device/inode binding, no overwrite, uncertain publication exit 3.

## Review Focus

1. A skipped fold must not become a measured zero-return trade result (Task 1 test).
2. End-exclusive indices and large accepted timestamps must retain correct included boundaries (Task 1 test).
3. A schema-1 report must not gain schema-2 zero diagnostics or a receipt-verified badge (Task 1 test).
4. An output parent replaced after validation must not receive published data (Task 2 race tests).
5. A caller must not receive success after a lost publication confirmation or import execution/network integrations while inspecting (Task 2 tests).

## Task 1: Pure Korean research renderer

**Files:** Create `apps/server/trading_bot/research_html.py` and `tests/test_research_html.py`.

**Interfaces:** Consume `research_report.validate_report(dict) -> dict`.
Produce `render_research_report(report: dict) -> str`, no I/O, no input mutation.

- [x] Write behavior tests for schema 1/2, every fold/candidate, mixed/all skipped,
  original high precision/zero/negative/exponent metrics, UTC boundaries, complete
  robustness summaries/scenarios, strict invalid input rejection and HTML escaping.
- [x] Run `python -m unittest discover -s tests -p 'test_research_html.py' -v`;
  observe failures due to missing renderer before implementation.
- [x] Implement the renderer with native expandable details and self-contained CSS.
- [x] Run the same command; expect all tests passing. Record observed limitations.
- [x] Commit only the renderer and its test file.

## Task 2: Read-only report CLI and private output

**Files:** Create `tools/paper_research_report.py`, `tests/test_research_report_cli.py`.

**Interfaces:** Consume Task 1's renderer, `read_report(path) -> dict`, and
`paper_evidence._publish(bytes, Path, expected_parent=(Path, dev, ino))`.
Produce `main(argv=None) -> int` and the exact JSON/exit contract from the spec.

- [x] Write real CLI tests for read-only inspection and export of schema 1/2,
  original summary/status, no source changes, zero/skipped retention and 0600 output.
- [x] Add import/network-forbidden subprocesses, bad input and argument cases,
  existing/symlink/hardlink/shared-parent paths, concurrent target creation,
  parent replacement/relocation and uncertain publication. Fault injection stays
  at the actual filesystem/publication boundary.
- [x] Run `python -m unittest discover -s tests -p 'test_research_report_cli.py' -v`;
  observe missing-feature failures, then implement the smallest CLI that passes.
- [x] Run both new suites and existing research producer/reader/API and publisher
  regressions. Expect preserved existing behavior and all tests passing.
- [x] Commit the CLI and its tests.

## Task 3: CI, documentation, review and delivery

**Files:** Modify `.github/workflows/quant-regression.yml`, `README.md`,
`docs/QUANT_RESEARCH.md`, and `docs/CURRENT_HANDOFF.md`.

**Interfaces:** Document the actual Task 2 command and trust/error semantics.

- [x] Add the new modules to compile checks and both new suites to required CI.
- [x] Document complete synthetic-fixture creation, read-only check/export,
  v1/v2 differences, output permissions, exit codes and evidence limits.
- [x] Execute a real synthetic original-engine research export, then inspect and
  render the saved result with execution/network calls forbidden. Keep artifacts private.
- [x] Run development unittest discovery, package/server pytest and Node suites;
  inspect results. Baseline engine fingerprints/producer contracts remain unchanged.
- [x] Obtain one fresh independent review of the entire increment; reproduce any
  material issue before fixing and rerun the affected/full required suites.
- [x] Update the handoff and record actual validation, including uncompleted visual QA.
- [ ] Publish to the existing branch with expected-head protection, compare Git
  trees, and verify exact delivered-head CI; keep PR Draft and main unchanged.

## Execution record

The user authorized continued implementation and development-branch delivery.
This fresh checkout is already isolated from user work. The renderer can proceed
independently under its fixed interface while the primary agent implements CLI
boundaries. No additional approval pause is required for these reversible changes.

Final test results and remote delivery identifiers are recorded in the handoff
and current PR checks rather than assumed from earlier commits.


### Final local evidence

- Renderer: 14 tests passed; CLI/publication: 18 tests passed.
- Full development regression: 919 tests passed in 208.388 seconds.
- Package/server: 309 tests and 382 subtests passed in 32.82 seconds.
- Node clients: 79 passed.
- Real original-engine synthetic saved results covered skipped and evaluated
  windows; the separate inspector retained original bytes and wrote 0600 HTML
  with research/network/account operations forbidden.
- All 16 execution fingerprint modules and the three research contract/producer
  modules are unchanged from v39.
- Fresh independent review found no Critical, Important or Minor issue, reran
  all 32 new tests and verified lost-link-confirmation output preservation.
- Browser visual QA remains unverified because the prior policy boundary was
  respected. No alternate browser/localhost path was attempted.

Local development checkpoints: `82c6c8e` (design/plan), `b9ca70b` (renderer),
`cefee99` (CLI/CI/docs). These are local checkpoints if connected GitHub delivery
consolidates the final tree. Publication and exact-head CI happen after this
record is committed; use the current PR head/checks as the delivery evidence.
