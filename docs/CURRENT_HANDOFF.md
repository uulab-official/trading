# Trading continuation handoff — 2026-11-14 (v43)

## Resume point

Repository: `uulab-official/trading`. Continue on `feat/smc-market-structure`
and [Draft PR #1](https://github.com/uulab-official/trading/pull/1).
The verified starting head was v42 `840eb4895506f835557a07a40d5a2141f248b1cf`,
tree `4e2ee1f3fbab9e21e2f6f6f3656e0b367d923f82`. Independent remote inspection
found no divergence, all three exact-head workflows successful, PR open/Draft
and unmerged with no auto-merge, and main unchanged at
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user again requested continued implementation. Existing feature-branch
and Draft PR delivery authorization applies. Main merge, deployment, credentials
and live execution remain outside this continuation. The existing feature
checkout and environment were intact and clean.

## Delivered v43 implementation

```text
python tools/paper_study.py run SOURCE --plan PLAN --directory NEW_STUDY --html NEW_HTML
```

The optional `run --html` path now connects explicitly selected local historical
input and fixed candidate plan to original paper execution, a fresh independent
inspection and private HTML. The original producer runs exactly once; its returned
execution response is never passed to the read-only renderer. Plain `run` and
all `check` results and error payloads retain their existing behavior.

The source still requires the existing imported-market fixture format. The plan
retains its explicit common assumptions, 2–8 ordered candidates and original work
limits. The new directory and HTML path are preflighted before execution. HTML
must be new, outside the study, under an existing owned/private output parent.
No parent directory is created implicitly.

### Separate execution and observation

Only the new path returns an 11-field envelope:

- `schema_version: 1`, `source: market_paper_study_workflow`, `mode: paper`.
- `operation: execute_new_study_with_report`, `read_only: false`.
- `live_enabled: false`, `resume_authorized: false`.
- `status`: fresh inspection status; `report_published: true` after confirmed HTML.
- `execution`: untouched original execution result.
- `inspection`: untouched fresh read-only inspection used by the renderer.

Stdout remains one JSON document; incidental diagnostics go to stderr. A missing
or corrupt receipt after execution can therefore retain the earlier execution
success and show a later incomplete/blocked inspection without relabeling either.
The outer status and exit follow that inspection after successful publication.

### Binding and publication

The study parent is canonicalized before execution, without resolving a final
study symlink. The original private-directory helper pins the created root
immediately after the producer returns. The root is checked after inspection and
again after rendering, immediately before publication.

Every exportable inspection must match the executed source digest, symbol,
timeframe, bar count, common assumptions, baseline and ordered candidate
IDs/configurations/run references. A minimal unavailable/blocked response lacking
these identities cannot be attributed to this execution and produces no HTML.
A later explicit `check` can still produce its generic diagnostic snapshot.

For `verified_study`, all stable response fields must match too, including account
snapshots, result/input hashes, metrics, statuses, reasons and boundaries. Only
observation timestamps and top-level operation/read-only flags are excluded.
Bound diagnostic responses are allowed to differ in outcome and retain their
original missing/corrupt-receipt or child-audit diagnostics.

The former check publication body is shared with the new flow. It retains late
target/parent checks and the original no-clobber publisher's `expected_parent`
binding. HTML remains 0600. Existing files, partial studies and uncertain outputs
are preserved. No engine, inspector, renderer, receipt or input validator was added.

### Errors and interruption

Only new-path errors add a fixed `phase`: `preflight`, `execution`, `inspection`
or `report_publication`. An existing requested study is
`market_study_directory_not_new`; a changed/unbound result is
`market_study_changed`. Raw exception text and private absolute paths are not added.

Exit 0 means fresh verification and confirmed HTML; 4 means a bound incomplete or
missing-receipt diagnostic; 2 means blocked/input/binding/ordinary output error.
Original halt and publication uncertainty retain 3; cooperative interruption
retains 130/143. The original process wrapper owns the entire async sequence,
including reporting. A received signal after HTML publication still rejects success.

Producer failure stops immediately: no hidden inspection, retry, receipt repair or
cleanup is started. Reporting failures retain the completed study. Use a separate
`paper_study.py check DIRECTORY [--html NEW_FILE]` to inspect saved state after a
failure; individual original child recovery remains explicit.

Usage: [MARKET_PAPER_STUDY.md](MARKET_PAPER_STUDY.md#run-and-export-a-fresh-comparison-v43).
Design: [v43 spec](superpowers/specs/2026-11-14-study-run-report-design.md).
Plan: [v43 plan](superpowers/plans/2026-11-14-study-run-report.md).

## Verification in this continuation

- Original study/report/renderer baseline: 63 passed in 18.535 seconds.
- Eighteen new workflow test methods were run before implementation: 35 failing
  subcases demonstrated missing support. An initial missing-envelope test error was
  changed to an explicit assertion before the final RED run.
- The only original test changed replaces the obsolete unknown-option assertion
  with supported-option/unavailable-source/no-side-effects behavior.
- Final focused suites: 81 passed in 25.554 seconds. Fresh independent review
  repeated all 81 in 26.555 seconds and confirmed 81 unique IDs, zero duplicates.
- Full development regression: 1,021 tests passed in 106.229 seconds.
- Package/server: 309 tests and 382 subtests passed in 15.05 seconds, with the same
  926 existing deprecation warnings. Node clients: 79 passed.
- Real subprocess SIGTERM after the first saved fill retained one unconfirmed fill,
  halted first account, no next candidate, no study receipt and no HTML. SIGTERM
  immediately after HTML publication retained that HTML and returned 143.
- The signal fixture was corrected from 64 to 100 bars after measuring the original
  engine: MA has zero fills at 64 bars, so its first fill could not trigger the
  intended first-candidate interruption. No product behavior or assertion was weakened.
- A separate guarded original-engine demonstration ran the example's three
  candidates (MA3/5, MA5/20, RSI14) on 100 synthetic annotated bars. New workflow
  and subsequent explicit check both returned 0 with matching metrics and 0600
  HTML. Reusing the study directory returned 2 before execution. Source/plan bytes
  and the checked study inventory remained unchanged. No network/provider download
  or performance claim is involved.
- All 16 execution-fingerprinted sources, study producer/inspector/renderer,
  research producer/report/CLI sources and existing CI workflows are byte-identical
  to v42. The only changed production file is `tools/paper_study.py`.
- Complete base-to-working-tree whitespace and syntax checks passed. Existing CI
  automatically includes the new `test_order_*.py` suite; required package coverage
  and separate server lifecycle steps remain unchanged.
- Fresh independent whole-increment review found no actionable Critical, Important
  or Minor issue. It read the full diff and original surrounding contracts, ran
  focused verification, and had no declined-to-judge items.

## Retained boundaries

The root pin begins after successful execution. Ordinary metadata/content checks
are not authenticated atomic creation-to-publication evidence; an exact copied
bundle substituted before the first root observation is outside their guarantee.
A later change does not update exported HTML. A study remains a sequential,
historical simulation over independent accounts, with original risk-stop flags,
no automatic winner selection and no portfolio aggregation.

The v42 research inspector still separately supports saved-report consistency,
optional raw-byte matching and original input-rule inspection. No research run was
hidden in either inspector, and no new research orchestration was added here.
Earlier unrelated local-only KIS/UI packages are not assumed integrated.

Actual desktop/mobile browser visual QA remains unverified. The earlier browser
connection was unavailable and local-file access was refused; no localhost,
alternate browser or automation workaround was used. Automated structural tests
do not establish rendered layout, print or accessibility interaction.

## Next useful work

Continue toward an operator workflow for explicitly selected chronological research
inputs/candidates and saved results, using the original producer and existing
inspection contracts. Keep research execution explicit and distinct from study
receipt verification. A new UI should build on working commands; actual visual QA
still requires an authorized available browser path. Do not duplicate validators
or infer live-readiness from simulation/report completion.

## Delivery verification

Publish only to the existing feature branch with an expected-head fast-forward
check and compare the remote tree with the verified local tree. Keep PR #1
Draft/open/unmerged with no auto-merge and main unchanged.

Publication and exact-head CI follow this handoff commit. On resumption, inspect
the actual PR head and its Quant Regression, Order Outcome Safety and CI/CD runs;
previous-head success is not evidence for a later commit. Local checkpoints
`a51c2ec` and `38f56e5` describe this work; connected GitHub publication may
consolidate the local history into a different remote commit. Final delivery
results are recorded in the current PR description and head-filtered checks.
