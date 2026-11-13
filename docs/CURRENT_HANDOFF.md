# Trading continuation handoff — 2026-11-13 (v40)

## Resume point

Repository: `uulab-official/trading`. Continue on
`feat/smc-market-structure`; [PR #1](https://github.com/uulab-official/trading/pull/1)
remains Draft. The restored v39 head was
`111ef2ea26db646e36b4081979ea303ecfd3fdcf`. Independent remote inspection found no
new commits, all three exact-head workflows successful, and `main` unchanged at
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user again asked to resume interrupted development. Ongoing implementation
and development-branch delivery remain authorized. Keep the established main
merge, deployment, credentials and live-execution boundaries. Temporary files
from the preceding session were gone, so this continuation used a fresh full
clone of the actual remote head, not a stale conversation checkpoint.

## Delivered v40 implementation

- `python tools/paper_research_report.py REPORT_JSON [--html NEW_FILE]` inspects
  an existing schema-1 or schema-2 walk-forward research report. The default
  prints a bounded JSON result with its original identity and summary; optional
  HTML is a new private standalone Korean document.
- Reuse `research_report.read_report` and `validate_report`. The renderer has no
  I/O, does not recompute trading metrics and preserves all original decimal
  strings, folds, training candidates, losses and skipped windows.
- Display train/test half-open indices and first/last included UTC bar times,
  settings, original summary, reported input digest and experiment identity.
  Independent windows and arithmetic averages remain explicit; a skipped window
  has no measured test result even though it contributes cash-zero to the summary.
- Schema 1 labels cost diagnostics as not performed. Schema 2 displays every
  original fixed-selection 1x/2x/3x cost scenario, strategy/buy-hold metrics and
  excess percentage points. No retuning, ranking or report upgrade is performed.
- The visible status is **연구 보고서 일관성 확인**. CLI status is
  `research_report_checked` with `validation_scope: saved_report_consistency`.
  These are not source/execution authentication or a market-study receipt.
- The output parent must already be owner-private. Reuse the v39 publisher's
  canonical-parent/device/inode binding and no-clobber publication. Existing
  files/aliases are preserved, output mode is 0600, uncertain completion exits 3,
  and keyboard interruption exits 130 with an instruction to inspect output.
- New renderer/CLI tests and compile checks are required in Quant Regression CI.
  Existing producer, research contract, dashboard and study behavior are retained.

Full usage, synthetic setup, permissions and exit codes:
[QUANT_RESEARCH.md](QUANT_RESEARCH.md#static-research-snapshot-v40).
Design and implementation record:
[spec](superpowers/specs/2026-11-13-research-snapshot-design.md) and
[plan](superpowers/plans/2026-11-13-research-snapshot.md).

## Verification in this continuation

- Restored research baseline: 45 tests passed before implementation.
- New renderer: 14 tests passed after 14 expected missing-feature failures.
- New CLI/publication integration: 18 tests passed after missing-feature failures.
  Real subprocesses forbid research/strategy/configuration imports, network/DNS
  calls and SQLite account connections. The shared publisher still imports
  existing paper model definitions; importing those definitions is not execution.
- Package/server tests: 309 passed, plus 382 subtests. This fresh environment
  initially lacked pytest; after installing the existing CI test dependencies,
  the complete suite passed. Existing deprecation warnings remain visible.
- Node clients: 79 tests passed.
- Full development unittest regression: 919 tests passed in 208.388 seconds.
- All 16 execution-fingerprinted source modules and the three research
  contract/producer modules are byte-identical to the restored v39 baseline.
- A real original-engine run of the bundled synthetic fixture retained one
  skipped window. A second synthetic wave example produced two evaluated windows
  and seven simulated fills, including a losing window. Both schema-2 reports
  were then rendered with research imports/network/account connections forbidden;
  source bytes stayed unchanged and HTML was mode 0600. No market-performance
  claim is made from these synthetic examples.
- Fresh independent whole-increment review found no Critical, Important or Minor
  issue. The reviewer independently reran all 32 new tests and injected a lost
  link confirmation after actual publication: exit 3, complete output retained,
  private error details suppressed, source unchanged and no owned temporary left.

Actual desktop/mobile browser visual QA remains incomplete. The earlier Browser
connection was unavailable and the cloud browser refused the local-file protocol;
that security boundary was not bypassed by another surface or localhost. Structure,
content, escaping and timestamp tests do not establish font, print, accessibility
interaction or responsive appearance in a real browser.

## Carried-forward v39 capabilities

`python tools/paper_study.py check DIRECTORY --html NEW_FILE` retains its separate
saved study/account audit and completion-receipt semantics. The v39 report keeps
all planned candidates and distinguishes incomplete/blocked states. Its private
no-clobber publisher includes the late-directory-replacement fix reused here.

The prior legacy fixes remain: only exact empty signal IDs are normalized at the
legacy BUY boundary; saved filled orders accept absent or literal false
`cancel_requested`, while malformed/active values stay blocked. Required server
CI and declared offline CLI build prerequisites remain in place.

## Keep these boundaries

- Research JSON consistency, study receipt verification, source data authenticity
  and actual execution are different claims. Do not combine their statuses.
- The report's fixture digest is a reported value; this inspector does not reopen
  source prices to compare it. Experiment identity does not attest the producing
  code revision or independently authenticate results.
- No research rerun, source acquisition, automatic strategy selection, portfolio
  compounding, automatic study recovery or uncertain-order retry is added.
- Risk-stop flags are not measured maximum drawdown. Marked open holdings are not
  fully realized cash and do not include a forced final sale.
- No main merge, deployment, broker credentials/orders or live enablement.
- Existing research modules are integrated and verified in this checkout; earlier
  unrelated local-only KIS/UI packages are not assumed integrated.
- Keep personal inputs/results outside Git. Only synthetic examples were used.

## Next useful work

First perform desktop/mobile visual checks through an authorized browser route.
A useful next code increment is optional, explicit matching of an existing local
fixture file to the report's recorded digest, with a distinct source-match status.
It must remain separate from provider authentication or execution attestation;
keep size/regular-file checks and never fetch or rerun research during inspection.

## Delivery verification

Publish only to the existing development branch using an expected-head,
fast-forward update. Compare the exact Git tree with the tested local tree. If
connected GitHub delivery consolidates local commits, local development SHAs in
work records are checkpoints, not remote delivery identifiers.

This handoff is written before final remote publication/CI completes. Verify the
actual current PR head and its Quant Regression, Order Outcome Safety and CI/CD
runs; a workflow from an earlier commit does not cover a later head. The branch
and PR are the durable resume locations.
