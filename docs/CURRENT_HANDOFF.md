# Trading continuation handoff — 2026-11-13 (v41)

## Resume point

Repository: `uulab-official/trading`. Continue on
`feat/smc-market-structure`; [PR #1](https://github.com/uulab-official/trading/pull/1)
remains Draft. This continuation started from verified v40 head
`c6c9fa934a8480bd97d8f55cb282bc24728a8bbf`, tree
`e7be30eb39555593c388bfc707e1ec55afd13819`. Local and independent remote checks
matched, all three exact-head workflows passed, and main remained
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user again authorized continued implementation and development-branch
delivery. The existing checkout and installed test environment were intact.
Keep the established main-merge, deployment, credential and live-execution
boundaries. The actual current branch and PR are the durable resume locations;
local workstream commits may be consolidated during connected GitHub delivery.

## Delivered v41 implementation

```text
python tools/paper_research_report.py REPORT_JSON [--fixture LOCAL_FILE] [--html NEW_FILE]
```

- An optional, explicitly supplied `--fixture` compares the observed raw bytes
  with `experiment.fixture_sha256` from a validated schema-1 or schema-2 report.
  It reuses the original producer's SHA-256 convention and 32 MiB size limit;
  no parsing, normalization, source acquisition or research rerun occurs.
- Without `--fixture`, the default JSON envelope remains unchanged. HTML now
  explicitly shows **원본 파일 미대조**. A completed explicit comparison adds
  `fixture_check` with exactly `status`, `scope`, `algorithm`,
  `expected_sha256`, `actual_sha256` and `size_bytes`; no paths or file contents.
- `fixture_check.status` is `matched` or `mismatch`. These remain separate from
  `research_report_checked` / `saved_report_consistency`. HTML displays
  **파일 해시 일치** or **파일 해시 불일치**, both digests and the exact byte count,
  while retaining all original windows, metrics, skips and cost diagnostics.
- A completed mismatch exits 1 and still publishes optional diagnostic HTML.
  An explicitly requested match exits 0. Invalid/unavailable input or a
  pre-publication failure exits 2; uncertain publication exits 3; interruption
  exits 130. Output failures take precedence over a completed mismatch.
- New standard-library module `trading_bot.research_fixture` holds directory and
  file descriptors, uses a bounded chunked read with one sentinel byte, and
  checks file identity, size, mode, ownership/link metadata and nanosecond
  modification/change times. It rechecks the named file and canonical parent.
  Final symlinks, nonregular files, oversized inputs and detected changes fail
  closed. Initially resolved parent aliases are permitted; later detected
  redirection is rejected. Descriptors close on failure and interruption.
- The pure HTML renderer validates optional comparison context against the
  current report's expected digest. Inconsistent status/hash combinations,
  foreign expected digests, malformed sizes and unknown fields cannot be
  projected as a match. The saved research schema and identity are unchanged.
- The existing private no-clobber publisher remains unchanged: new 0600 HTML in
  an owner-private directory, canonical-parent/device/inode binding and explicit
  uncertain-completion handling. Existing fixture/report files and aliases
  remain protected. Initial output binding happens before inspection; fixture
  alias resolution follows the validated fixture check.

Usage, byte semantics and exit codes:
[QUANT_RESEARCH.md](QUANT_RESEARCH.md#optional-local-fixture-match-v41).
Design and implementation record:
[spec](superpowers/specs/2026-11-13-research-fixture-match-design.md) and
[plan](superpowers/plans/2026-11-13-research-fixture-match.md).

## Verification in this continuation

- Restored v40 baseline: 14 renderer and 18 CLI tests passed before implementation.
- Checker: 21 tests passed after 21 observed missing-feature failures. Coverage
  includes exact 32 MiB input, bounded growth, empty/binary raw data, file and
  parent replacement, in-place edits with restored mtime, FIFO/symlink races,
  descriptor cleanup and strict comparison-context validation.
- Renderer: 20 tests passed, including six new tests written before the feature.
  All three source-comparison states retain schema-1/2 content and standalone
  CSP; supplied context is validated without I/O or input mutation.
- Final CLI/publication integration: 31 tests passed in 8.678 seconds. Actual
  subprocesses forbid research/strategy/configuration imports, network/DNS and
  SQLite account connections. The shared publisher imports existing paper model
  definitions; those imports are not account operation or execution.
- Full development unittest discovery: 958 tests passed in 202.916 seconds. This
  discovery started before the final additional path-error regression was added;
  the complete final CLI suite of 31 tests then covered that additional case and
  its one-line production correction. Required CI runs the final suite.
- Package/server: 309 passed plus 382 subtests in 35.93 seconds. The 926 existing
  deprecation warnings remain visible. Node clients: 79 passed.
- The original producer generated both schema-1 and schema-2 reports from the
  bundled synthetic fixture. Each preserved one skipped window. Guarded
  inspections correctly returned match/exit 0 for the original bytes and
  mismatch/exit 1 for a copy with one added newline, with 0600 diagnostic HTML.
  Original fixture/report bytes, report identities and summaries were unchanged.
  These synthetic checks make no market-performance claim.
- All 16 execution-fingerprinted modules and the three original research
  contract/producer source files are byte-identical to v40.
- Fresh independent whole-increment review found no Critical or Important issue.
  It reproduced one Minor error-classification discrepancy: a looping fixture
  parent was reported as an output error only when HTML was requested. The new
  guarded test first failed on that subcase. The narrow correction preserves
  initial output binding and defers fixture-alias resolution until the fixture
  check. The reviewer independently reran all 31 final CLI tests in 8.539 seconds
  and closed the finding. No Critical, Important or Minor finding remains.

Actual desktop/mobile browser visual QA remains unverified. The earlier Browser
connection was unavailable and the cloud browser rejected the local-file
protocol. That boundary was not bypassed with localhost or another surface.
Automated markup/content/CSP tests do not establish actual responsive, print,
font or accessibility-interaction behavior.

## Retained capabilities and claim boundaries

The v40 read-only standalone research snapshot still preserves exact original
decimal strings, all candidates/windows, losses, skips and schema-2 fixed-selection
1x/2x/3x cost diagnostics. Each window uses independent capital; summary means are
arithmetic, not portfolio compounding. Skipped windows lack measured test results.
Marked holdings are not fully realized cash and do not imply a forced final sale.
Risk-stop flags are not measured maximum drawdown.

`python tools/paper_study.py check DIRECTORY --html NEW_FILE` retains its separate
saved study/account audit and completion-receipt semantics. Earlier legacy
empty-signal-ID and filled-checkpoint compatibility corrections, required server
CI and offline installation build prerequisites are retained.

A file match means only that the local bytes observed during the check hash to
the value recorded in this report. It does not authenticate the provider, validate
prices, reproduce execution, attest the producing code revision, or verify a
market-study receipt. Ordinary metadata checks are not an authenticated atomic
snapshot. Later source changes do not update an exported HTML document; the
shown report generation time is still the original report's timestamp.

No main merge, deployment, brokerage credentials/orders or live enablement was
added. The original producer, report schema, dashboard, execution and recovery
contracts remain unchanged. Earlier unrelated local-only KIS/UI packages are
not assumed integrated. Keep personal input data and results outside Git; only
synthetic examples were used in this continuation.

## Next useful work

Use the explicit matching command on an operator-selected report and its retained
local fixture when those files are provided. Preserve the original report and
producing code revision; do not upgrade a matched digest into an execution claim.
Desktop/mobile visual checks remain outstanding through an authorized route.
Further checks of source data validity must be separately specified and must not
silently fetch data or rerun research inside this inspector.

## Delivery verification

Publish only to the existing development branch with an expected-head,
fast-forward update and compare the remote Git tree to the tested local tree.
Keep PR #1 Draft, unmerged and without auto-merge; main stays unchanged.

This handoff is committed before remote publication and its exact-head CI
complete. Verify the actual current PR head and its Quant Regression, Order
Outcome Safety and CI/CD runs. An earlier head's successful workflow does not
cover later changes. Local checkpoints `d4e8528`, `b1b9647`, `60b4c42`, `6bc96db`
and `ec5962d` describe work stages, not necessarily the consolidated remote SHA.
