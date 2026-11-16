# Study Run and Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Execute an explicit local fixed-plan study and export its freshly checked comparison in one optional CLI flow.

**Architecture:** Extend `paper_study.py run --html` around the existing producer,
inspector and renderer. Share the existing private publisher, bind the fresh
inspection to the executed study, and return separate execution/inspection results.

**Tech Stack:** Python 3.10+, stdlib and existing packages; POSIX local storage.

**Spec:** `docs/superpowers/specs/2026-11-14-study-run-report-design.md`.

## Global Constraints

- Continue from `840eb4895506f835557a07a40d5a2141f248b1cf` on the existing feature branch.
- Existing source/plan validation, 2–8 candidates and study work limits apply unchanged.
- New private study and HTML only; output parent exists, is owned/private, outside study.
- No engine/inspector/renderer edits; no retry, implicit recovery, acquisition or live actions.
- Default run/check JSON and exit behavior remain unchanged.
- Optional-path fixed phases: `preflight`, `execution`, `inspection`, `report_publication`.
- Exit 0/4/2 follows fresh inspection; uncertainty 3 and interruption 130/143 retain precedence.
- HTML uses the untouched read-only inspection; outer operation remains an execution action.
- Browser visual QA remains unverified; no workaround for previous browser denial.

## Review Focus

- An HTML target created/replaced while execution runs must be preserved; retain study evidence.
- A different valid study with the same inputs must not become the executed study's report.
- A missing/corrupt receipt after successful execution must keep its diagnostic 4/2 result.
- A signal or uncertain publication must not turn into success or restart an execution.
- Parent aliases/final symlinks/root replacement must not redirect the report or hide changes.

## Task 1: Optional execution-to-report flow

**Files:** modify `tools/paper_study.py`, `tests/test_order_market_study_report.py`;
create `tests/test_order_market_study_workflow.py`.

**Interfaces:** Reuse `run_market_study(path, plan, directory=...)`,
`inspect_market_study(directory)`, `render_study_report(report)`,
`run_paper_operation(factory)`, `_private_directory(path)` and `_publish(...,
expected_parent=...)`. Internal helper names are implementation choices; the exact
workflow envelope, phases, binding fields and exits are fixed by the spec.

- [x] Run original study/report/renderer suites and record the clean baseline.
- [x] Add failing optional-path tests for real execution/inspection/publication,
  unchanged nested results, output/new-directory preflight and diagnostic receipts.
- [x] Add failure-first binding, race, publication and interruption tests; assert
  no retry/hidden inspection and preserved original study/output bytes.
- [x] Extract the existing publication body, implement the optional sequence and
  binding checks, and keep existing default paths unchanged.
- [x] Replace the obsolete unknown-option case with unavailable-source/no-output
  behavior under the supported option; run all four focused suites green.
- [x] Commit the implementation and tests after complete base-to-working-tree diff check.

**Verification:** `PYTHONPATH=tests .venv/bin/python -m unittest test_order_market_study test_order_market_study_report test_order_market_study_html test_order_market_study_workflow -q`.
Expected: all tests pass, with original test inventory retained except the explicitly
superseded unknown-option assertion.

## Task 2: Operator documentation, verification and delivery

**Files:** update `README.md`, `docs/MARKET_PAPER_STUDY.md`,
`docs/CURRENT_HANDOFF.md`, this plan. Documentation may be drafted independently
against the fixed spec while root owns code/tests and the final handoff.

- [x] Document the new command, nested JSON, phase/error/exit behavior, partial-state
  inspection and ordinary observed-change limits; remove the obsolete check-only wording.
- [x] Run an actual guarded synthetic workflow and a subsequent explicit check;
  verify private HTML, original receipt, untouched source/plan and consistent metrics.
- [x] Prove all 16 execution fingerprints and study/renderer/research sources unchanged.
- [x] Run full development unittest, package/server pytest and Node suites; inspect results.
- [x] Obtain one fresh independent whole-increment review, address concrete findings,
  update handoff and run complete base-to-head whitespace check before final commit.
- [ ] Publish the exact verified tree with expected-head FF; verify final-head CI,
  update existing Draft PR and independently confirm main/Draft/no-auto-merge state.

**Verification:** full `unittest discover -s tests -p 'test_*.py'`,
`pytest packages apps/server/tests -q`, `node --test tests/*.test.mjs`; all pass.
Existing package coverage CI command remains unchanged. Publication/CI evidence is
recorded in the PR after the handoff commit, with local checkpoints preserved.
