# Continuous Paper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Continue one paper account from the durable public capture, with atomic
bar consumption and authoritative restart verification.

**Architecture:** Add a detached original-engine transition and a separate SQLite
stream owner. Root integrates source admission, replay audit and lifecycle;
independent workers implement disjoint engine/store/CLI surfaces, followed by a
fresh whole-increment review. Existing historical execution files stay unchanged.

**Tech Stack:** Python 3.10+, existing packages, POSIX flock, SQLite, unittest.

**Spec:** `docs/superpowers/specs/2026-11-15-continuous-paper-design.md`.

## Global Constraints

- Additive KRW spot PAPER only; no provider/broker requests in tests.
- Original 16 fingerprinted historical source files remain byte-identical.
- Bootstrap 50–500 bars, no bootstrap fills or pending signal.
- Original MA/RSI buffer 500; original economic/risk/next-open rules.
- Atomic event + account + strategy + risk + cursor commits.
- 100,000 consumed bar / 256-MiB archive capacity; never silently evict evidence.
- Explicit resume performs mandatory constant-memory full journal replay.
- Source identity and exact overlap required; stale/unavailable data cannot trade.
- Private local files and permanent writer lock; observer never repairs journals.
- Python 3.10 compatibility, existing suites and final-head CI.
- Continue authorized feature/Draft PR; no merge/deploy/live activation.

## Review Focus

1. A signal or clock regression during error handling must not erase a terminal
   refusal or admit the next bar. Task 3 tests interruption at each boundary.
2. Source replacement or mutation during detached calculation must prevent commit;
   source retirement while resume audits must be rechecked. Tasks 2/3 test binding.
3. RSI behavior after more than 500 bars must use the exact original rolling
   window, and MA cross state must survive restart. Task 1 tests 650+ bars.
4. An acknowledged-lost SQLite commit must not duplicate a fill on reopen.
   Task 2 injects commit/directory sync failure; Task 3 audits actual durable state.
5. Digest-consistent cash/risk/strategy tampering must fail full event verification.
   Task 3 modifies real SQLite rows and runs audit/resume.

## Task 1: Deterministic incremental engine

**Files:** create `apps/server/trading_bot/paper_stream_engine.py` and
`tests/test_continuous_paper_engine.py`.

**Interfaces:** Produce the exact `initial_state`, `validate_state` and async
`advance_state` signatures and state fields in the spec. Consume original
PaperRunConfig/Runner, CandleBuffer, PaperExchange and OrderManager unchanged.

- [x] Write failing behavioral tests: bootstrap creates cash only/pending null;
  following-bar causality; original MA/RSI parity with indicator-only activation;
  continuation beyond 500 bars; UTC daily reset and persistent drawdown;
  malformed state/candle rejected without mutating input.
- [x] Observe RED before implementation, then implement bounded pure transitions.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_continuous_paper_engine.py' -v`.
  Expected: all cases pass, original supported strategies exercised.
- [x] Record evidence in this plan's ignored ledger; root commits verified work.

## Task 2: Atomic continuous account store

**Files:** create `apps/server/trading_bot/paper_stream_store.py` and
`tests/test_continuous_paper_store.py`.

**Interfaces:** Consume Task 1 validator. Produce exact writer/reader methods,
snapshot/manifest/source/event fields and exceptions from the spec.

- [x] Write failing tests using real private SQLite files: concurrent owner,
  atomic head/event commit and monotone cursor, immutable manifest, canonical
  row/hash verification, file/lock/parent substitutions, unsupported schemas,
  bounded inputs/reads, capacity, hot-journal observer refusal/writer recovery,
  failure before commit versus failure after possible commit.
- [x] Observe RED; implement fixed schema and pinned file ownership.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_continuous_paper_store.py' -v`.
  Expected: committed prefix always internally complete, no observer repairs.
- [x] Record evidence; root commits verified work.

## Task 3: Source admission, lifecycle and verified recovery

**Files:** create `apps/server/trading_bot/paper_stream.py`,
`tests/test_continuous_paper_runtime.py`, and integration fixtures as needed.

**Interfaces:** Consume Tasks 1/2, original read_capture and same-snapshot
_observation. Produce async `run_stream`, async `audit_stream`, `stream_status`
with the spec's explicit run/resume/check/audit boundaries.

- [x] Write failing tests for new activation, repeated polling/no duplicates,
  chronological catch-up, current-only admission, post-calculation freshness,
  all overlap and source counters, retention/identity loss, immutable resume
  options, cancellation, regressing clock and unconfirmed storage.
- [x] Write failing tests proving full archived replay rejects forged cash,
  risk/pending/cross state, fills and changed execution fingerprint; source
  remains untouched and resume never resubmits committed fills.
- [x] Implement source descriptor pinning, canonical fingerprints, bounded
  loop and separate verifier. Persist controls only with trustworthy time.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p 'test_continuous_paper_runtime.py' -v`.
  Expected: no network, exact economic continuation, failures preserve prefix.

## Task 4: Operator CLI, passive observation and CI

**Files:** create `tools/paper_stream.py`, `tests/test_continuous_paper_cli.py`,
`docs/CONTINUOUS_PAPER.md`; update existing CI integration selectors and docs.
Optional dashboard route uses existing server runtime and readiness shape.

**Interfaces:** Consume Task 3 public functions. CLI subcommands `run` (new
--capture/--directory), `resume DIRECTORY`, `check DIRECTORY`, `audit DIRECTORY`;
bounded `--max-polls`, frozen original strategy/cost/risk options on run only.

- [x] Write failing subprocess tests for JSON/exit code contracts, invalid live
  options, malformed paths, signals and fixed private errors. Test passive HTTP
  route if implemented; no UI visual claims.
- [x] Implement thin CLI/signal intent handling and operator documentation.
- [x] Run task tests and add actual 3.10/3.12/3.13 CI coverage, no skipped engine
  tests because packages are missing.

## Task 5: Integration, independent review and publication

- [x] Run a synthetic capture → continuous consume → interrupted/resumed account
  → audit demonstration with sockets/DNS forbidden and source preserved.
- [x] Run complete unittest discovery, package pytest, Node clients; verify all
  16 original fingerprinted files unchanged and parse changed Python as 3.10.
- [x] Obtain a fresh independent whole-increment review; reproduce and repair
  material findings, record review decisions and rerun affected/full gates.
- [x] Update CURRENT_HANDOFF, production checklist and README with exact evidence
  and remaining real-provider/operations/broker gaps.
- [ ] Publish exact tested tree to feature branch with expected-head lease;
  verify all applicable final-head CI, then guarded PR title/body update.
- [ ] Independently verify feature/main, draft/unmerged state, metadata and CI;
  save delivery receipt and report outcomes in Korean.

Publication follows this immutable code/document checkpoint. Exact completion of
the remaining live delivery steps is recorded in the ignored delivery receipt and
PR metadata, avoiding a self-referential commit/CI identity.
