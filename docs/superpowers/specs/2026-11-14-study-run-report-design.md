# Study execution and comparison report (v43)

## Intent and authority

Continue the existing automated-trading development on `feat/smc-market-structure`
from v42 `840eb4895506f835557a07a40d5a2141f248b1cf`. The user requested continued
implementation. The next bounded operator improvement connects explicit local
source/plan selection, original study execution, fresh inspection and HTML export.
The implementation choice below is the assistant's choice under that continuation,
not a claim that the user separately specified this interface.

Existing feature-branch and Draft PR delivery authorization applies. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`; merge, deployment, credentials and live
execution remain outside this work.

## Approaches considered

1. A new workflow tool with separate study/research modes would connect both
   product paths, but requires a new output-bundle lifecycle and research settings
   interface. Defer that larger surface until the existing path is simpler to use.
2. Running research from an existing study would reuse its frozen assumptions,
   but couples research to study completion and does not simplify initial execution.
3. Extend `paper_study.py run` with optional `--html NEW_FILE`. Selected: it reuses
   the current explicit inputs and original lifecycle without another plan format,
   validator, renderer or engine. Plain run and all check behavior stay compatible.

## User contract

```text
python tools/paper_study.py run SOURCE --plan PLAN --directory NEW_STUDY --html NEW_HTML
```

All inputs remain explicit. The source is the existing imported-market fixture
format, and the plan is the existing bounded 2–8 candidate plan. A new study
directory and a new HTML filename in an existing private owned parent are required.
HTML must be outside the study. No parent directory is created implicitly.

Preflight the new directory and existing HTML output binding before executing.
Read the plan with the original bounded reader/decoder. Run the original study
exactly once, then perform a fresh original inspection, bind it to that execution,
render that read-only inspection and publish through the original no-clobber
publisher. One `run_paper_operation` owns the async execution/inspection sequence.

The original renderer continues to reject execution responses. Neither producer
nor inspector is retried. A producer exception stops the sequence immediately;
there is no automatic post-failure inspection, receipt repair or partial cleanup.

## JSON and exit behavior

Only the new `run --html` path returns a workflow envelope with these fields:

- `schema_version: 1`, `source: market_paper_study_workflow`, `mode: paper`.
- `operation: execute_new_study_with_report`, `read_only: false`.
- `live_enabled: false`, `resume_authorized: false`.
- `status`: the fresh inspection's status; `report_published: true` only after
  confirmed publication.
- `execution`: untouched original execution response.
- `inspection`: untouched fresh read-only inspection response.

Stdout remains one JSON document; incidental execution/reporting diagnostics go
to stderr. The outer status and exit follow the fresh inspection after confirmed
publication: 0 verified; 4 incomplete or missing receipt; 2 blocked/invalid/error.
Original halt uncertainty and publication uncertainty retain exit 3; interruption
retains 130/143 and original cleanup precedence. A signal after publication must
not become success. All partial studies and possible published HTML are retained.

Errors on the optional path add only a fixed `phase`: `preflight`, `execution`,
`inspection`, or `report_publication`. Existing fixed error codes remain; add
`market_study_directory_not_new` for an already existing requested study. Binding
failure uses `market_study_changed`. Plain-run/check error payloads stay unchanged.
No raw exception text, source contents or absolute paths are added to JSON.

## Execution-to-inspection binding

Canonicalize the study's parent before execution; do not resolve a final study
symlink. Capture the created private root's `(device, inode)` using the existing
`recovery._private_directory` immediately after the producer returns. Recheck it
after inspection and immediately before HTML publication.

For every exportable inspection require equality with the execution's source
digest, symbol, timeframe, bar count, common assumptions, baseline and ordered
candidate `(id, config, run_directory)` identities. A minimal blocked/unavailable
response without these identities cannot be attributed to this execution: fail
with the fixed binding error and no HTML. A later explicit `check` remains usable.

When inspection claims `verified_study`, compare all stable response content,
excluding only top-level `observed_at`, `operation`, `read_only`, and each candidate
`observed_at`. Account snapshots, result/input hashes, metrics, statuses, reasons,
boundaries and ordering remain in the comparison. This prevents exporting a
different successful execution with the same source and strategy plan.

Bound diagnostic reports remain useful: a removed/corrupt receipt or changed child
can produce diagnostic HTML with the original 4/2 status. They must not require
stable-result equality or be promoted to verified. The execution result still
describes the earlier successful execution; inspection describes the later check.

These are ordinary observed-change checks. The post-run root pin is not an atomic
creation-to-publication attestation and cannot identify an exact-copy substitution
before its first observation. Later changes do not update an exported document.

## Implementation boundaries

- Python 3.10+, existing stdlib/POSIX local storage and existing dependencies only.
- Extract the current `_inspect` publication body into one shared helper, retaining
  its output recheck, private 0600 publication and `expected_parent` binding.
- The new helper accepts a narrow optional guard invoked immediately before the
  publisher; plain check passes no guard and keeps its existing behavior.
- All engine/fingerprint modules, study producer/inspector and HTML renderer remain
  byte-identical. No new input-validation rules, research runs, acquisition or GUI.
- Synthetic annotated fixtures may exercise the original study engine, explicitly
  labeled synthetic. Do not present them as provider downloads or performance evidence.
- Earlier browser denial remains respected; actual browser visual QA is unverified.

## Verification

Use focused failure-first tests for the new orchestration: real end-to-end success,
one execution followed by a fresh read-only inspection, invalid outputs/new-directory
checks before side effects, producer failure/uncertainty/cancellation precedence,
bound missing/corrupt receipt diagnostics, foreign successful results, changed root
and source/plan bindings, publication races/failures/uncertainty, and real SIGTERM
preserving an incomplete child without later candidates or HTML.

Keep existing plain-run/check/renderer tests. Replace only the obsolete assertion
that `run --html` is an unknown option. Existing Order Outcome Safety already
discovers `test_order_*.py`; retain all required CI commands. Run the full local
development, package/server and Node suites once after integration, independent
whole-increment review, complete base-to-head whitespace check, exact-tree delivery
and exact-final-head CI. No repetitive broad testing without a remaining risk.
