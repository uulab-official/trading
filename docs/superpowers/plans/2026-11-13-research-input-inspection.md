# Research Input Inspection Implementation Plan

Base: `2b0b4cd6ceb2d136029056cb42c8ac505e55d2cb`.
Spec: `docs/superpowers/specs/2026-11-13-research-input-inspection-design.md`.
User authorized continued implementation and existing feature-branch delivery.

## Tasks

1. Shared input validation (root): `fixture_input.py`, `research_input.py`,
   delegate existing loader and producer, `tests/test_research_input.py`.
   - [x] Save deterministic producer baselines; write new API/delegation tests red.
   - [x] Literal extraction preserving failure order and numerical semantics.
   - [x] Focused green and producer parity; commit owned files.
2. Same-byte fixture data inspection (root): `research_fixture.py`,
   `research_fixture_data.py`, fixture/data tests.
   - [x] Write failing snapshot/data-context tests.
   - [x] Shared bounded observation, new reader, strict pure data context.
   - [x] Prove actual byte hash/parser identity, invalid-vs-mismatch semantics,
     report/hash binding, race/cleanup behavior; commit after focused green.
3. Pure HTML integration (independent workstream): `research_html.py` and its tests.
   - [x] TDD the four data states, context binding, no I/O, preserved content.
   - [x] Implement optional keyword context and comparison display; focused green.
4. CLI and delivery (root): CLI/tests, CI, README, usage, handoff.
   - [x] TDD explicit flag dependency, legacy defaults, guarded actual subprocesses,
     mismatch/invalid diagnostics, privacy, no-clobber/error precedence.
   - [x] Integrate flag, result and required CI; document actual scope/exit codes.
   - [x] Real original-producer synthetic demonstrations and 16-source integrity.
   - [x] Full unittest, package/server and Node regression; one fresh final review.
   - [x] Resolve findings, update handoff, commit exact tested tree.
   - [ ] Expected-head publish, exact-tree comparison and exact-head CI verification.

## Coordination and decisions

The root owns producer extraction, observer, data context, CLI and docs. One
independent renderer workstream uses the fixed interface in the spec. Parallel
read-only remote and architecture audits have already verified the starting
checkpoint and the proposed extraction. This uses delegation for independent
work while retaining a single whole-increment final review.

The loader alone cannot establish research-input validity. Reusing the original
preflight requires a small source refactor in the producer; behavior and all
execution fingerprint files remain unchanged. This deliberately replaces v41's
per-increment source-unchanged promise for the producer with tested delegation
and deterministic output parity. No report schema change is needed.

Pre-flight interfaces: producer and inspector consume the same preparation API;
inspector passes actual observed hash and saved candidate/settings/schema. The
renderer receives only a strict context bound to both the report ID and that
hash, never raw data. CLI requires --fixture for --validate-fixture and uses one
observer invocation. Invalid input is a completed diagnostic result, not a
fabricated metadata comparison. Output errors override comparison exit 1.

## Local verification result

The full development suite passed 1,003 tests in 207.793 seconds. Package/server
tests passed 309 tests and 382 subtests; Node passed 79 tests. A fresh independent
review found no findings and independently passed 146 focused tests. Twelve
producer baselines and evaluator traces retained exact parity; ten guarded
original-producer demonstrations passed. See `docs/CURRENT_HANDOFF.md` for scope
and limitations. Publication and exact-head CI are recorded after this commit
in the current PR description and its head-filtered workflow runs.
