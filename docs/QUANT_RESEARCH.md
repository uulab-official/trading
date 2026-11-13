# Chronological strategy research (offline, read-only)

## What this adds

`tools/quant_research.py` selects a configured strategy on each past training window and evaluates only that selection on the subsequent test window. It reuses the existing `PaperRunner`, `PaperExchange` and checked replay report; it does not introduce another fill simulator or enable real orders. The generated file is read through `GET /api/research` and displayed at `/research.html` by the existing loopback-only server.

Default candidates are MA cross 3/5, MA cross 5/20 and RSI 14. These are research examples, not recommendations or claims of profitable configurations. Supply a JSON array via `--candidates` for another bounded set. Only MA-cross and RSI parameters are supported; arbitrary executable strategy imports are not accepted.

## Run

From repository root, after installing the same editable engine packages used by the replay dashboard:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
python tools/quant_research.py /tmp/uulab-btc-history.json \
  --output /tmp/uulab-btc-research.json \
  --train-size 120 --test-size 60 --gap 1 --warmup 100 \
  --initial-balance 1000000 --fee-pct 0.1 --slippage-pct 0.05
python apps/dashboard/server.py \
  --research-report /tmp/uulab-btc-research.json --port 8765
```

Open `http://127.0.0.1:8765/research.html`. The input is a local file produced by the existing importer or a local fixture; the research process never fetches data. The existing source-annotation validator is reused before any evaluation. Fee/slippage values above are modeling examples, not a statement of actual broker charges.

For the repository's smaller synthetic fixture, use `--train-size 60 --test-size 20 --warmup 60`. Input/output must be distinct. Existing output must describe the exact same experiment/results or publication fails; unrelated files are never overwritten. No source file or replay checkpoint is modified. The research job uses in-memory segment runs without checkpoint resume.

Example candidate file:

```json
[
  {"id":"ma-fast","strategy":"ma_cross","fast_period":3,"slow_period":5},
  {"id":"ma-slow","strategy":"ma_cross","fast_period":5,"slow_period":20},
  {"id":"rsi-14","strategy":"rsi","rsi_period":14}
]
```

## Static research snapshot (v40)

Inspect an **existing** research JSON without a dashboard server or another
research run. The inspector uses the existing bounded schema/arithmetic reader.
By default it does not open the source fixture. An explicit `--fixture` can
compare local file bytes as described below. Inspection never fetches prices or
operates a paper account.

```bash
# Check only; print the checked report's identity and original summary as JSON.
python tools/paper_research_report.py .private/research-001.json

# Check and create a NEW private HTML file.
python tools/paper_research_report.py .private/research-001.json \
  --html .private/research-001.html
```

Open the saved HTML on your own computer. It contains its own CSS and needs no
JavaScript, network assets or API. It is a static observation of the loaded report;
later source changes do not update it. Choose another output filename to export a
new snapshot. A private HTML sibling beside the research JSON is allowed.

For a complete first run using only the repository's **synthetic** fixture:

```bash
# Create this only if absent; an existing output directory must be owner-private.
mkdir -m 700 .private
python tools/quant_research.py config/examples/offline_fixture.json \
  --output .private/research-001.json \
  --train-size 60 --test-size 20 --gap 1 --warmup 60 --robustness
python tools/paper_research_report.py .private/research-001.json \
  --fixture config/examples/offline_fixture.json \
  --html .private/research-001.html
```

The first command after directory setup explicitly runs research. The second
checks its saved result, compares the explicitly supplied fixture and renders
the snapshot. This example is not market evidence.
Omit `--robustness` when producing a schema-1 report. The inspector supports both
versions and does not upgrade an old report or manufacture missing diagnostics.

### Optional local fixture match (v41)

Use `--fixture` when you also want to compare a local file with the SHA-256
recorded in `experiment.fixture_sha256`. This is the same **raw-byte** digest
that the existing research producer records. It does not parse or normalize
the fixture; changing whitespace, a newline or encoding changes the digest even
when parsed JSON would be equivalent.

```bash
# Compare only; report and fixture both already exist.
python tools/paper_research_report.py .private/research-001.json \
  --fixture config/examples/offline_fixture.json

# Publish a new private snapshot containing the comparison result as well.
python tools/paper_research_report.py .private/research-001.json \
  --fixture config/examples/offline_fixture.json \
  --html .private/research-001-checked.html
```

Without `--fixture`, the existing JSON result is unchanged and contains no
`fixture_check` key. The HTML explicitly displays **원본 파일 미대조**. With the
option, a completed comparison adds `fixture_check` with these fields:

| Field | Meaning |
| --- | --- |
| `status` | `matched` or `mismatch`, derived from the two digests |
| `scope` | Always `local_fixture_bytes` |
| `algorithm` | Always `sha256` |
| `expected_sha256` | Digest recorded in this validated research report |
| `actual_sha256` | Digest of the file bytes observed by this check |
| `size_bytes` | Number of bytes read, at most 33,554,432 (32 MiB) |

A match is shown as **파일 해시 일치** and exits `0`. A completed mismatch is
shown as **파일 해시 불일치** and exits `1`; optional HTML is still published so
the difference remains reviewable. Both preserve the original report identity,
summary and `research_report_checked` status, which refers only to report
consistency. An output failure takes precedence over the mismatch exit code.

Only a bounded regular local file is accepted. Final-component symlinks,
directories, FIFOs, missing/unreadable files, oversized inputs and detected
changes during reading return `research_report_fixture_unavailable` with exit
`2`, without publishing HTML. The checker compares identity, size, mode and
nanosecond modification/change times around the read and rechecks the named
path and parent. It permits an initially resolved parent alias while rejecting
detected redirection during the observation. These ordinary metadata checks are
not an authenticated atomic filesystem snapshot.

The result describes the bytes observed during this invocation. It does not
prove that they came from a provider, constitute valid market data, or were used
by the producing engine. No research rerun or account access occurs. Neither
file paths nor contents are included in the result or HTML. The displayed report
generation time remains the saved report's original value; later file changes
do not update a saved snapshot.

### What is displayed

The Korean report keeps all folds, all training candidates, selected candidates,
negative outcomes and skipped windows. Original decimal strings retain their
precision and quote-currency units. Training/test indices are half-open; displayed
UTC timestamps identify the first and last **included** bars. Settings show the
gap, warm-up, minimum fills, rolling/anchored policy and cost/risk assumptions.

A skipped fold has no measured test result. Its zero-interest cash contribution
to the existing summary remains explicit. The all-fold mean is arithmetic across
independently funded windows, not compound growth or a continuous account curve.
Open holdings remain marked at the last close, with no forced liquidation.

Schema 2 also shows every original fixed-selection 1x/2x/3x cost scenario and its
strategy, buy-and-hold and percentage-point excess results. Schema 1 instead
labels those diagnostics as not performed. The reference uses the original
allocation, second test bar's opening price and last closing mark, with no active
risk exits; equal starting cash does not imply equal exposure or risk.

### Meaning of validation

| Inspection | What is checked | What it does not establish |
| --- | --- | --- |
| `paper_research_report.py` | Existing research schema, experiment identity, chronological bounds, training selection, internal accounting and summary; optional diagnostics | Source authenticity, an engine rerun, execution attestation, repository revision, or market-study completion receipt |
| Optional `--fixture LOCAL_FILE` | Exact local bytes against this report's recorded SHA-256, with a separate comparison status | Provider authenticity, data validity, execution attestation or a guarantee against later file changes |
| `paper_study.py check` | The separate saved study/account audit and its existing completion-receipt contract | Walk-forward evaluation or source-provider authenticity merely from a successful receipt |

The HTML status is **연구 보고서 일관성 확인**. The CLI success status is
`research_report_checked`, with `validation_scope: saved_report_consistency`.
`robustness_available` distinguishes schema 2 from legacy reports, and
`html_written` says whether this invocation also published a file. A requested
fixture comparison has its own `fixture_check.status`; report consistency alone
does not imply a match. These statuses are not trading authorization. Retain the
original JSON and producing code revision;
an experiment ID and a reported fixture digest do not authenticate the results.

### Output and failure behavior

The existing output parent must be a POSIX directory owned by the current user
with no group/other permission bits. The final HTML is mode `0600`. Existing
targets, source aliases, hardlinks and symlink targets are rejected. Directory
identity is bound before inspection, rechecked afterwards and checked again by
the shared publisher. The command never replaces or deletes a final target.

| Exit | Meaning |
| --- | --- |
| `0` | Saved-report checks completed; any explicitly requested fixture matched; optional HTML was published |
| `1` | Saved-report checks completed, but the explicit fixture digest differs; optional diagnostic HTML was published |
| `2` | Invalid/unavailable arguments/report/fixture/output, or a pre-publication failure; JSON contains a bounded error code |
| `3` | HTML may already exist but publication confirmation failed; preserve and inspect it |
| `130` | Keyboard interruption; inspect any saved output before retrying with a new filename |

Errors do not echo invalid report contents, exception details or source paths.
Do not treat a failed report as an empty account or zero-return experiment.
The HTML interface has automated structure/content tests. Actual desktop/mobile
browser visual verification for this increment is tracked separately in
[CURRENT_HANDOFF.md](CURRENT_HANDOFF.md).

## Exact evaluation contract

- Rolling training windows by default; `--anchored` retains the first training bar. Test windows are non-overlapping, chronological, and never used for selection within their fold. Earlier test bars may legitimately enter later training windows.
- Rank candidates by training **terminal marked return after modeled execution costs**, then ascending candidate ID on an exact numeric tie. Every training candidate score remains inspectable.
- Require `--min-training-fills` (default 2) before selection. This is a count of fills, NOT completed round trips or a claim of statistical significance. If none qualifies, skip the test run and record a cash/no-interest return of 0 for that window. A losing but eligible training winner is not silently discarded.
- The original `Strategy.on_candle` waits for 50 observed bars before analysis. This gate is not bypassed. Short early windows may therefore produce no eligible strategy; `--warmup` is a maximum, not fabricated prehistory. The first training window starts cold.
- Warm up only with bars preceding the evaluated window (maximum `--warmup`). Already observed gap bars may initialize test indicators; they do not enter selection scoring. Warm-up signals are discarded. There is no cash, position, pending order or risk-state transfer between segments. First evaluated-bar signals can fill only at the next evaluated bar's open, following the existing engine.
- Every segment starts with the same initial cash and configured execution/risk assumptions. A window's last-bar signal is not executed outside that window. Final holdings are marked at the last close, NOT force-liquidated; future liquidation costs are omitted. In particular, these results are not all-realized cash returns.
- Summary is an unweighted arithmetic mean over all complete test windows, including cash/skip windows. It is NOT a compounded return or a continuous portfolio. Worst/best windows, evaluated/skipped counts and unused trailing bars are explicit. No endpoint-only Sharpe, MDD or CAGR is fabricated.
- No overlapping target labels are trained here; the gap is not a general purged-label ML cross-validation implementation. Adding labels/ML requires separate horizon-aware purging and validation.

Limits: 5000 contiguous input bars, 8 unique candidates, 24 complete folds, initial train/test windows <=1000 bars, warm-up <=500 bars, plus a deterministic prefix-work budget for the existing runner. Data gaps, overlapping timestamps, invalid OHLC, nonfinite amounts and malformed settings fail before any segment evaluation. Evaluation exceptions fail the whole experiment; failing strategies/folds are never quietly omitted.

## Report and service boundaries

The reader checks exact fields, bounds, candidate identities, train-only selection, Decimal accounting relationships, source/configuration hash and summary. These are consistency checks, not a rerun or a cryptographic proof of source/execution. A trusted local producer is required. The experiment ID does not attest the repository revision; retain the code commit alongside reports for reproducibility.

Atomic create-if-absent publication protects unrelated files and races. Report readers reject oversized files, duplicate JSON keys, symlinks and non-regular files. The service reads a fixed operator-supplied report path only; HTTP requests cannot choose files, strategies or execution settings. Existing Host/Origin checks, CSP and no-write routes apply. These are not authentication. Do not expose the server through a public proxy or tunnel.

The UI preserves last-good data on a failed refresh, labels unavailable/stale reports and displays amounts as text. Reports/mean returns are historical experiments, not live account balances. No new real-provider network success, brokerage integration, order reconciliation or profitability is implied.

## Original research implementation verification

```bash
python -m unittest discover -s tests -p 'test_quant_research.py' -v
python -m unittest discover -s tests -p 'test_research_cli.py' -v
python -m unittest discover -s tests -p 'test_research_api.py' -v
python -m unittest discover -s tests -p 'test_research_engine.py' -v
node --test tests/research_client.test.mjs
```

Local dependency-free checks: 17 core/contract tests + 4 CLI-boundary tests + 4 real-loopback HTTP tests, and 6 Node tests. Core/CLI tests inject explicit deterministic test evaluators; they do not claim real engine execution. The seven original-engine cases are separately wired into GitHub Actions on Python 3.10/3.12, with sockets/DNS forbidden during evaluation. Consult the commit checks for their actual outcome.

Baseline source was read through the connected GitHub API because container GitHub DNS prevented cloning; exact hashes were verified for the unchanged server dependencies reconstructed locally. The complete legacy repository suite is exercised by GitHub CI, not the sparse local workspace. No external reviewer agent was available; review was inline.

Optional Playwright/Chromium smoke: `python tools/check_research_ui.py --output /tmp/research-previews`. Desktop 1440px, mobile 390px and dark mode were rendered with explicitly synthetic evaluator results. Mobile candidate and test results fit in the viewport, training details expand, stale data is retained and recovery clears the warning. These offline-render tests do not establish browser-to-server/CSP integration; Python loopback tests are separate evidence. No actual market-performance screenshot is claimed.

## References

Architecture references only; no upstream source was copied and no new research dependency was added.
- https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html — chronological training/test split and gap.
- https://www.freqtrade.io/en/stable/lookahead-analysis/ — future-data contamination checks.
- https://www.freqtrade.io/en/stable/recursive-analysis/ — indicator warm-up sensitivity.

Research caution: repeatedly changing candidates after inspecting test results overfits those tests. Use a separately reserved final holdout and subsequent paper operation before considering deployment. This increment does not perform statistical significance, multiple-testing correction, order-book liquidity modeling or live partial-fill settlement.

### Integration-fixture correction

Initial CI cc4856c exposed that the original Strategy.on_candle requires at least 50 bars, while the synthetic integration fixture used 36 training bars plus at most 12 warm-up bars. The expected-fill assertion correctly failed. The engine and training-fill gate were not weakened: the positive test now uses 72 training / 60 warm-up bars; a separate original-engine regression confirms that the old short-window experiment remains skipped. The warm-up boundary test now actually crosses the original readiness threshold before its evaluation window.
