# Fixed-plan market paper studies (v38, v39, v43)

## Design

Run 2–8 explicitly named MA/RSI candidates on one captured imported historical
source using the existing market handoff, durable execution and completion audit.
All capital, sizing, fees, slippage and risk thresholds are explicit shared inputs.
Candidate differences are limited to the strategy and its relevant periods.
Reject duplicate effective strategies, unknown fields and unsafe identifiers.
Validate the entire plan and a 20,000 candidate-bar budget BEFORE any output.

A NEW private study directory contains exact source.json, canonical plan.json,
study.json bindings and runs/<candidate>/ (each an original v37 bundle).
Freeze all candidates before executing the first. Execute sequentially, stop on
any child/audit error, and never skip a failed candidate, choose a winner, retry
an uncertain order or silently reset an account. Existing signal cleanup is used.
No existing engine, source fingerprint, account schema or importer is changed.

Read-only check keeps every planned candidate, including not-started, incomplete,
blocked and unavailable rows. Completed rows must pass the original audit AND
match the shared source, exact configuration and execution-input binding. Recheck
sources and account snapshots after inspection. All verified candidates use the
first candidate as the preselected baseline; differences are percentage points.
Never add currencies/accounts or compute a performance rank. Use only existing
engine return/equity/fee/fill and risk-stop fields, not invented MDD/win-rate metrics.
Observations are sequential, not a simultaneous portfolio snapshot or liveness.

Publish study-result.json only after every candidate verifies. Check independently
recomputes and compares the saved receipt, never trusts file presence. No batch
auto-resume in this increment: preserve partial studies, inspect them, and recover
individual opted-in child runs explicitly. A missing receipt is not a successful
completed study. No collection of fresh data, brokers, credentials, web actions,
background scheduler or live enablement. Same-period trials are not out-of-sample
validation or proof of profitability. Hashes are not authenticated provenance.

## Implementation contract

Python 3.10+, stdlib, POSIX private local storage. 2–8 candidates; 1–5000 input bars;
4 MiB source; 64 KiB plan; <=20,000 candidate-bars. Labels: [a-z][a-z0-9_-]{0,31}.
Common numeric values are finite decimal STRINGS, with <=64 coefficient digits,
exponents -18..18 and initial capital <=1e18. Original configuration validation
still applies. Repeated effective strategy settings are rejected.

CLI: `paper_study.py run FILE --plan FILE --directory NEW [--html NEW_FILE]`,
or `check DIRECTORY [--html NEW_FILE]`.
Stdout: one JSON report/error; stderr: diagnostics. Exit 0 verified study receipt;
4 incomplete/confirmation attention; 2 blocked/invalid/unavailable; 3 publication
uncertain or original halt unconfirmed; 130/143 cooperative interruption. Error
responses use fixed codes, never raw paths or account data. Individual runs retain
existing explicit recovery commands and may leave active saved mode after SIGKILL.

## Limits

An interruption stops the batch, not a guaranteed liquidation or cancellation of
an order. Child evidence/DBs remain. No claim of physical-power-loss durability,
maximum-load throughput, cross-platform support or an independently reviewed system.

## Usage

Existing repository setup is required; no API key. The acquisition command remains
separate. Give the study a local file from the existing importer, not a URL.

```bash
python tools/paper_study.py run .private/btc-hourly-001.json \
  --plan config/examples/market-study-plan.json \
  --directory .private/study-001
python tools/paper_study.py check .private/study-001
```

The example compares MA(3,5), MA(5,20) and RSI(14). Numbers are explicit simulation
assumptions, not broker fee quotes or recommended strategies. Capital, position
allocation and costs are the SAME for every independent candidate. Capital is
not distributed from one portfolio; do not sum the accounts or their returns.
The result's `return_delta_pp` is the candidate's return minus the first candidate's
return in percentage points. Order of candidates is preserved, not profit-ranked.
Missing/blocked results have no comparison delta. Risk-stop booleans are original
engine outputs, NOT measured maximum drawdown or loss guarantees.

`check` may report `verified_study`, `incomplete_study`, `complete_without_receipt`,
`blocked` or `unavailable`. Completion requires each original audit plus source,
configuration, runtime fingerprint and final receipt agreement. A malformed receipt
blocks the study without removing otherwise inspectable candidate rows. The report
shows sequential observations, not a simultaneous snapshot; later changes remain
possible. A plain run result has `operation=execute_new_study`, `read_only=false`;
a check result has `operation=inspect_existing_study`, `read_only=true`. Their
existing JSON and exit behavior remain unchanged.

The source file, plan and existing execution-source fingerprint are frozen before
execution. Each candidate is in `runs/<id>/run/`. A failed candidate halts the loop;
already-completed candidates survive and subsequent candidates remain not_started.
No automatic batch continuation or receipt repair is provided. After inspection,
individual child recovery remains explicit and does NOT start other candidates:

```bash
python tools/paper_preflight.py .private/study-001/runs/ma-fast/run
# Only if that original opted-in child is incomplete:
python tools/paper_resume.py .private/study-001/runs/ma-fast/run
```

Never remove UNKNOWN intents or bypass the original saved-fill reconciliation rule.
A missing study receipt remains `complete_without_receipt` even when each child
is later completed manually. Preserve such studies; a new study requires a new
path. An output-publication error can leave a complete receipt: use `check`, not a
rerun or deletion. `check` never creates or repairs it.

The existing dashboard can read any child's account.sqlite. Existing static status
HTML can inspect the new completed children without a new UI or web write path:

```bash
python tools/paper_overview.py \
  --run ma-fast=.private/study-001/runs/ma-fast/run \
  --run ma-slow=.private/study-001/runs/ma-slow/run \
  --run rsi-14=.private/study-001/runs/rsi-14/run \
  --html .private/study-status-001.html
```

Keep source bundles, comparison JSON and HTML private. The plan/receipt format is
not signed, and success is consistency checking rather than investment validation.

## Run and export a fresh comparison (v43)

Add `--html` to a new study run to execute once, perform a fresh read-only check
and publish its comparison:

```bash
python tools/paper_study.py run .private/btc-hourly-001.json \
  --plan config/examples/market-study-plan.json \
  --directory .private/study-002 \
  --html .private/study-comparison-002.html
```

Choose a **new study directory** and **new HTML filename**. Both parents must
already exist; the HTML parent must be owned by the current user and private
(`0700`). HTML must be outside the study. No parent is created implicitly. The
new-directory and HTML output checks happen before execution. Source format,
plan rules, candidate limits and simulation assumptions are unchanged.

Only `run --html` returns this 11-field workflow envelope. Stdout is one JSON
document; diagnostics go to stderr.

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `source` | `market_paper_study_workflow` |
| `mode` | `paper` |
| `operation` | `execute_new_study_with_report` |
| `read_only` | `false`: this invocation executed a new study |
| `live_enabled` | `false` |
| `resume_authorized` | `false` |
| `status` | The fresh inspection's status; determines exit `0`, `4` or `2` |
| `report_published` | `true` only after confirmed HTML publication |
| `execution` | The untouched original execution response |
| `inspection` | The untouched later read-only inspection response used for HTML |

An earlier successful execution and a later incomplete or blocked inspection
remain separate. For example, a removed or corrupt receipt can produce diagnostic
HTML with exit `4` or `2`; it is not repaired or reported as verified.

Before export, the inspection must match the execution's source digest, market,
timeframe, bar count, shared assumptions, baseline and ordered candidate/config/run
identities. A verified inspection must also match all stable results, including
account snapshots, input/result hashes and metrics; only top-level and candidate
observation timestamps and top-level action flags are excluded. A response lacking
the required identities cannot be exported by this flow. Use a separate `check`
to inspect it. The private study root is observed immediately after execution and
rechecked after inspection and before publication. These ordinary change checks
are not an atomic attestation from directory creation through publication; an
exact-copy replacement before the first root observation is outside their scope.

Errors on this optional path add a fixed `phase`: `preflight`, `execution`,
`inspection` or `report_publication`. `market_study_directory_not_new` means the
requested study directory already exists. `market_study_changed` means the
execution/inspection binding or observed study identity changed; no comparison is
published for an unbound result. Other existing fixed error codes and precedence
remain unchanged. Plain run/check error responses do not gain a phase field.

Execution failure stops the flow immediately, with no automatic inspection,
retry, receipt repair or deletion. Reporting failure also preserves the study and
any possibly published HTML. After any failure or interruption, inspect the saved
study explicitly instead of rerunning its execution:

```bash
python tools/paper_study.py check .private/study-002
# To export another observation, choose a new filename.
python tools/paper_study.py check .private/study-002 \
  --html .private/study-inspection-002.html
```

## Study comparison report (v39)

Export the existing read-only check as a self-contained Korean HTML report:

```bash
python tools/paper_study.py check .private/study-001 \
  --html .private/study-comparison-001.html
```

Open that HTML file in a browser on the same computer. It needs no server, network,
JavaScript, API key or browser extension. The report shows common capital, costs
and risk assumptions; every candidate in the original plan order; the original
verified equity, return, fees, fills and bar counts; and each candidate's status.
Return differences are in **percentage points**, using the first planned candidate
as baseline. They are displayed only when the whole study and its receipt verify.
Verified child metrics may remain inspectable while the whole study is incomplete
or blocked. Unavailable values stay blank; actual zero values are displayed as zero.

The output must be a **new file**, outside the study and all its child runs, in an
existing directory owned by the current user with owner-only permissions (`0700`).
For example, create `.private` with `mkdir -m 700 .private` if it does not already
exist. The file is published with `0600` permissions. Existing targets, symlink
targets, source-directory aliases and shared output directories are refused.
Choose a new filename for each observation; previous reports are never replaced.

`check --html` remains read-only: it does not execute a study, resume a child,
repair a receipt or change an account. Its JSON stdout is still the original
inspection report. Both HTML paths use the same report renderer and publication
rules; `run --html` adds the explicit execution flow described above.

| Exit | Meaning with `--html` |
| --- | --- |
| `0` | Study and receipt verified; HTML published |
| `4` | Study incomplete or receipt missing; diagnostic HTML published |
| `2` | Study blocked/unavailable, invalid input/output or binding, or ordinary publication failure |
| `3` | Publication confirmation uncertain, or original execution halt unconfirmed; retain and inspect evidence |
| `130` / `143` | Interrupted; retain the study/output and inspect explicitly |

A separate `check --html` can publish diagnostic HTML for a blocked/unavailable
study when inspection returns a report and the output path is valid. The new
`run --html` flow additionally requires its execution/inspection binding. An
`error` JSON response means the command could not complete; no HTML success is
implied, and a file may remain after uncertain publication or interruption.
The HTML is an observation at the printed time, with sequential per-candidate
checks, and does not refresh. Run a new check for a new observation.

Risk-stop indicators are the original engine's flags, not measured maximum
drawdown. Costs are explicit simulation assumptions, not current broker quotes.
There is no ranking, automatic selection, portfolio sum or out-of-sample claim.
All text is escaped and the document has no active content or external assets.
