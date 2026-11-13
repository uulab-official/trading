# Trading continuation handoff — 2026-11-13 (v42)

## Resume point

Repository: `uulab-official/trading`. Continue on `feat/smc-market-structure`
and [Draft PR #1](https://github.com/uulab-official/trading/pull/1).
The verified starting head was v41 `2b0b4cd6ceb2d136029056cb42c8ac505e55d2cb`,
tree `3ae52cea4aa07f19bc787b6c482cbbfb8ea4891d`. Independent remote inspection
found no divergence, all three exact-head workflows successful, PR open/Draft
and unmerged with no auto-merge, and main unchanged at
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user again requested continued implementation. Existing feature-branch
delivery remains authorized. Keep main merge, deployment, credentials and live
execution outside this continuation. The checkout was intact and clean.

## Delivered v42 implementation

```text
python tools/paper_research_report.py REPORT_JSON --fixture LOCAL_FILE --validate-fixture [--html NEW_FILE]
```

The new explicit `--validate-fixture` requires `--fixture`. It applies the
original research producer's input rules under the saved report's candidates,
settings and schema, then compares symbol, timeframe, bar count and first/last
included timestamps. It does not acquire data, evaluate a strategy or run research.
Default report-only and v41 hash-only JSON remain unchanged, including hash-only
acceptance of arbitrary bytes without a data-validity claim.

### Shared authoritative rules

- `trading_bot.fixture_input.load_fixture(raw)` is the original replay loader,
  moved without changing its statements and re-exported from the original tool.
  It retains duplicate-key rejection, legacy float decoding, row shape and
  imported-provenance checks.
- `trading_bot.research_input.prepare_research_input(...)` is the original
  producer preflight through cleaned rows. `run_research` delegates once, then
  performs its unchanged evaluation. The extraction preserves failure order,
  `number(str(v))`, numeric bounds, defaults, timestamp continuity, OHLC ranges,
  candidate/history/window/work limits and optional robustness budgets.
- This small producer/replay source refactor is deliberate: the loader alone
  was too weak for unannotated data, and a separate copied validator would drift.
  Saved report schema/identity and all 16 execution fingerprint sources are intact.

### One observation, three separate claims

The existing stable bounded reader now has a shared private observation function.
`check_fixture` stays streaming; `read_fixture_snapshot` captures the same chunks
it hashes, returning them only after original descriptor, file/path/parent and
metadata checks. The parser never reopens the file. Limits remain 32 MiB plus one
sentinel byte, with final symlinks, nonregular files and detected changes rejected.

The original saved-report status and six-field `fixture_check` remain separate.
Only the new flag adds the eight-field `fixture_data_check`:

- `status`: `matched`, `mismatch` or `invalid`.
- `scope`: `research_input_consistency`.
- `run_id`: saved report identity; `fixture_sha256`: actual observed byte digest.
- `expected` and `observed`: five metadata fields; observed is null on invalid input.
- `mismatches`: differing field names in fixed metadata order.
- `error`: null, or the fixed `research_input_invalid` code.

The pure validator binds context to both identities, checks metadata with the
existing report experiment/window constraints, strict integers and derived last
time, enforces status/difference/error consistency, and returns nested copies.
The renderer performs no fixture loading or preparation. Its separate section
shows input inspection unrequested, matched, metadata mismatched or invalid,
with exact expected/observed values, UTC endpoints and textual difference markers.
Invalid input has no invented observed metadata. Existing research metrics,
skips, cost diagnostics, escaping and standalone CSP are retained.

Exit 0 requires all explicitly requested checks to match. A completed mismatch
or invalid-input diagnostic exits 1 and can still publish clearly labeled HTML.
File-observation or unexpected failures exit 2; uncertain publication and
interruption retain 3 and 130 and take precedence. The same private no-clobber
publisher produces new 0600 HTML. Existing files and aliases remain protected.

Usage and semantics:
[QUANT_RESEARCH.md](QUANT_RESEARCH.md#optional-research-input-inspection-v42).
Design and plan:
[spec](superpowers/specs/2026-11-13-research-input-inspection-design.md) and
[plan](superpowers/plans/2026-11-13-research-input-inspection.md).

## Verification in this continuation

- Initial related baseline: 106 tests passed before implementation.
- TDD: shared input 11 new tests, snapshot capture two new tests, data inspector
  13 new tests, renderer seven new tests and data CLI 11 new tests were exercised
  before their features existed. Missing-feature failures were observed.
- Final focused suites: reader 23, data inspector 13, shared input 11, renderer
  27, legacy CLI 31 and new data CLI 11; all 116 passed. A no-site-packages
  `python -S` run passed all 35 new input/data/CLI tests.
- Before extraction, captured 12 deterministic producer report/evaluator-trace
  baselines spanning both schemas, decimal strings, floats, missing defaults,
  shifted metadata and synthetic annotated imports. After extraction all were
  exactly equal, excluding only report generation time.
- Full development regression: 1,003 tests passed in 207.793 seconds.
- Package/server: 309 tests and 382 subtests passed in 45.33 seconds, with the
  same 926 existing deprecation warnings. Node clients: 79 passed.
- Actual original-producer demos generated schema-1/schema-2 reports from the
  bundled synthetic source and a schema-2 synthetic provenance-bearing source.
  Ten guarded inspections correctly distinguished originals, newline-only byte
  changes, a different market and invalid OHLC. All HTML was 0600; original
  report/source bytes, identity and summary were preserved. These synthetic
  examples do not establish market performance or real-provider acquisition.
- Default/hash-only subprocesses retain the original ban on data/execution
  imports. The explicit path permits only existing pure data/provenance
  definitions; acquisition entry points are replaced with fail-fast guards,
  alongside research/runner/strategy/configuration import bans and network/DNS/
  SQLite audit guards. Importing a definition is not invoking acquisition.
- All 16 execution-fingerprinted source modules, `research_report.py` and
  `quant_research.py` are byte-identical to v41. The two intended producer/loader
  entry-point delegations are verified by exact output and AST comparisons.
- Fresh independent whole-increment review found no Critical, Important or Minor
  finding. It independently passed 146 distinct focused tests, reran data tests
  without site packages, and proved by AST comparison that loader statements,
  original preflight order and post-preflight evaluation statements are retained.

Actual desktop/mobile browser visual QA remains unverified. The earlier browser
connection was unavailable and the local-file protocol was refused. No localhost,
alternate browser or automation workaround was used. Markup/content/CSP tests do
not establish rendered fonts, responsiveness, print or accessibility interaction.

## Retained boundaries and capabilities

A data match means the observed bytes were accepted by the producer's input rules
under this report's configuration and the five metadata values agree. A shorter
otherwise well-formed source may have no complete window and be invalid before
metadata comparison; large sources may exceed work/robustness budgets. This is
not general acceptance under arbitrary research settings.

A newline-only change can be byte mismatch plus data matched. Recorded byte
identity can also coexist with invalid input. Neither byte nor data consistency
authenticates a provider, proves price truth, reproduces saved performance or
attests execution. Ordinary metadata checks are not authenticated atomic file
snapshots. Later source changes do not update an exported document; its displayed
report generation time remains the original report's time.

Research windows retain independent capital and arithmetic means, skipped windows
lack measured results, marked holdings are not fully realized cash, and risk-stop
flags are not measured maximum drawdown. The separate `paper_study.py check`
receipt/account audit and all existing recovery/order-safety behavior remain.
Earlier unrelated local-only KIS/UI packages are not assumed integrated.
Only synthetic data was used here; keep personal inputs and results outside Git.

## Next useful work

The inspector now covers saved-report consistency, byte identity and original
research-input rules. Prioritize a complete operator workflow using explicitly
selected local sources/candidates and clear study/research outputs, rather than
adding duplicate validators. Any source-provider authentication or actual result
reproduction should be a separately specified operation, never a hidden rerun
inside this inspector. Authorized desktop/mobile visual QA is still outstanding.

## Delivery verification

Publish the exact tested tree only to the existing feature branch with an
expected-head fast-forward guard. Keep PR #1 Draft/unmerged with no auto-merge
and main unchanged. Compare the remote tree with the local verified tree.

Publication and exact-head CI follow this handoff commit. On resumption, inspect
the actual current PR head and its Quant Regression, Order Outcome Safety and
CI/CD runs; previous-head success is not evidence for a later commit. Local
checkpoints `ad31788`, `d16aa52`, `e6dd140` and `4a0693a` describe work stages;
connected GitHub delivery may consolidate them into a different remote commit.
