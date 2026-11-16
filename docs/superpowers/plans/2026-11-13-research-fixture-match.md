# Research Fixture Match Implementation Plan

**Goal:** Add optional explicit local raw-byte SHA-256 matching to saved research
inspection, with a separate JSON/HTML result and no research execution.

**Base:** `c6c9fa934a8480bd97d8f55cb282bc24728a8bbf`.
**Spec:** `docs/superpowers/specs/2026-11-13-research-fixture-match-design.md`.
**Stack:** Python >=3.10 standard library, existing HTML/CSS, unittest and CI.

## Workstreams

### 1. Bounded local fixture check

Owned files: `apps/server/trading_bot/research_fixture.py`,
`tests/test_research_fixture.py`.

- [x] Write failing behavior tests for exact bytes, limits, regular-file checks,
  concurrent modifications/replacements, cleanup and strict comparison context.
- [x] Implement `check_fixture` and pure `validate_fixture_check` from the spec.
- [x] Run focused tests and commit only owned files.

### 2. Pure renderer integration

Owned files: `apps/server/trading_bot/research_html.py`,
`tests/test_research_html.py`.

- [x] Test unrequested/matched/mismatched status, report-bound context validation,
  separate consistency scope, no I/O/mutation and retained schema-1/2 content.
- [x] Implement the optional keyword-only context and local-file section.
- [x] Run focused tests and commit only owned files.

### 3. CLI, CI and documentation (primary agent)

Owned files: `tools/paper_research_report.py`,
`tests/test_research_report_cli.py`, `.github/workflows/quant-regression.yml`,
`README.md`, `docs/QUANT_RESEARCH.md`, `docs/CURRENT_HANDOFF.md` and this plan/spec.

- [x] Test the actual guarded CLI with explicit fixture matching for both schemas,
  mismatch exit 1 with optional HTML, unchanged default JSON and unavailable-file
  errors without publication. Check source aliases and publication-error priority.
- [x] Implement the optional argument and result without touching producer/engine.
- [x] Add required CI compile/test coverage and document commands and exit codes.
- [x] Execute real original-producer synthetic match and byte-changed mismatch
  examples; inspect using execution/network/account guards.
- [x] Run development unittest, package/server pytest and Node suites. Confirm the
  16 fingerprinted modules and three research contract/producer files unchanged.
- [x] Obtain a fresh independent whole-increment review and resolve findings.
- [x] Record actual evidence and limitations in the handoff.
- [ ] Publish the exact tested tree with expected-head protection; verify all
  exact-head CI, Draft PR state and unchanged main.

## Initial evidence and coordination

Remote and local v40 head/tree match. Main is unchanged at
`a5750019dcd7a980a28783258c06c7ef4706afc1`; PR #1 is open, Draft and unmerged
with no auto-merge. All three v40 workflows passed. The existing renderer's 14
tests and CLI's 18 tests passed before v41 implementation. The checkout is clean.

Use parallel ownership for the checker, renderer and CLI, with the fixed shared
contract above. Do not edit another workstream's files or stage unrelated work.
The user has already authorized continuation and development-branch delivery.

## Final local evidence

- Checker 21 tests and renderer 20 tests passed after expected preimplementation
  failures. The final CLI suite passed 31 tests, including the additional guarded
  path-resolution regression that first reproduced the independent review finding.
- Development discovery passed 958 tests in 202.916 seconds; discovery preceded
  addition of the final path-error test. Final CLI retesting covered that case and
  the narrow correction. Package/server passed 309 tests and 382 subtests; Node
  passed 79 tests. No optional broad retesting was needed for the resolved
  one-line CLI error-classification change; required exact-head CI follows.
- Real original-producer synthetic reports for both schemas were inspected with
  execution/network/account guards: original bytes matched, a newline-altered
  copy mismatched, original sources remained unchanged and all HTML was 0600.
- All 16 fingerprinted execution modules and three research contract/producer
  modules are unchanged from v40.
- Fresh independent review initially found one Minor fixture-path error-code
  discrepancy, with no Critical/Important issues. The fix is `ec5962d`; the
  reviewer independently reran all 31 final CLI tests and closed the finding.
  No unresolved findings remain. Browser visual QA remains unverified.
- Publication and exact-head CI occur after committing this handoff; use the
  current PR head/checks for durable delivery evidence.
