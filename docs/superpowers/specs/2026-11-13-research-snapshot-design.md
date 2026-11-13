# Read-only walk-forward research snapshot (v40)

## Intent and current state

Continue from v39 `111ef2ea26db646e36b4081979ea303ecfd3fdcf` on
`feat/smc-market-structure`, keeping PR #1 Draft. The user requested continued
development after an interrupted session. This increment follows the next-work
direction in `docs/CURRENT_HANDOFF.md`.

The repository already produces schema-1 walk-forward research and optional
schema-2 fixed-selection cost diagnostics, validates both, and displays them in a
local dashboard. Add a portable Korean HTML snapshot of an existing report so
inspection does not require running a server or repeating research.

## Approach

Considered: adding HTML to the research producer (couples viewing to execution),
reusing the market-study receipt UI (different evidence contract), and a separate
read-only CLI plus renderer. Choose the separate CLI; this is an implementation
decision under the continuation request, not a new user-specified requirement.

`tools/paper_research_report.py REPORT_JSON [--html NEW_FILE]` reads through the
existing `trading_bot.research_report.read_report`. It never imports the research
runner, opens fixture data, reselects candidates, fetches prices, or changes
accounts. `trading_bot.research_html.render_research_report(report: dict) -> str`
is pure, reuses `validate_report` at its entry, and only projects validated values.
Neither the research contract nor any execution engine is changed.

## Visible report

- A compact Korean, light, responsive standalone document, with native details
  for training scores and diagnostics; no script, network asset, form, refresh,
  execution link, or third-party dependency. Escape all dynamic content and use
  CSP, no-referrer and viewport metadata.
- Label its status **연구 보고서 일관성 확인**. Explain that saved structure,
  identities and arithmetic were checked, without authenticating data origin,
  rerunning execution, or providing market-study receipt verification.
- Keep every fold, every training candidate, losses and skipped windows. Display
  selected candidates from the report; do not rank them again. Preserve all
  metric decimal strings exactly, including zeros, exponent notation and signs.
- Show symbol/quote units, timeframe, candidates, common account/cost/risk
  assumptions, train/test/gap/warmup settings, anchored/rolling policy, minimum
  training fills, original summary, and unused tail bars.
- Show half-open bar indices and first/last included bar times labeled UTC.
  Time conversion must not depend on host timezone or floating-point timestamps;
  a last exclusive bound must not be mistaken for an included bar.
- Skipped windows have no measured test metrics. Explain their cash-zero
  contribution to the existing all-window summary without inventing test data.
- Schema 1 explicitly says cost robustness was not performed. Schema 2 displays
  every 1x/2x/3x scenario, original strategy/buy-hold/percentage-point excess
  summary and per-fold diagnostics, with frozen training selection and the
  reference entry/mark conventions. No invented zero diagnostic values.
- Explain independently funded windows, arithmetic rather than compounded
  averages, marked open holdings/no forced liquidation, and configured stop
  flags rather than measured maximum drawdown. Show report creation time,
  experiment identity and the reported fixture digest with their limited meaning.

## CLI and publication contract

On success, one JSON stdout object has `mode: paper`, `read_only: true`,
`live_enabled: false`, `status: research_report_checked`,
`validation_scope: saved_report_consistency`, `schema_version`, `run_id`, the
original `summary`, `robustness_available`, and `html_written` booleans. Without
`--html`, inspect only and write no report. Existing producer CLI stays unchanged.

HTML output is optional but, when supplied, must be a new nonempty file path in
an existing owner-private POSIX directory. The source and any existing target,
including symlinks and hardlinks, are never overwritten. A private sibling HTML
file beside the source JSON is allowed. Validate the canonical parent and its
device/inode before reading and again before publication. Reuse
`trading_core.paper_evidence._publish(..., expected_parent=binding)` so late
directory replacement is checked at the actual publication boundary.

Invalid arguments, report, output or pre-publication failure return 2 with a
bounded JSON error and no HTML success. Post-publication uncertainty returns 3
and preserves the possibly completed output. Keyboard interruption returns 130;
inspect any saved output before retrying. Do not expose exception text, raw
invalid content or source paths in errors. Successful private files are mode 0600.

## Validation and boundaries

Use real schema-1/2 reports, mixed/all-skipped cases, exact numbers and valid
unusual timestamp separators. Reject malformed/oversized/nonregular reports via
the existing validator before publishing. Exercise actual CLI subprocesses with
network and execution-module imports forbidden; compare source bytes before and
after. Test no-clobber races, parent symlink replacement and same-inode parent
relocation, and publication uncertainty using narrow filesystem fault injection.

Keep original producer, research/API/dashboard, study and publisher regressions.
Add new tests to required Quant Regression CI. Complete one independent final
review and verify the delivered head's workflows. Existing browser local-file
policy refusal is not bypassed; desktop/mobile visual QA remains explicitly
unverified unless an authorized supported route becomes available.

No data acquisition, candidate recommendation, signed execution attestation,
combined study/research receipt, broker credentials/orders, live enablement,
deployment or main merge belongs to this increment.
