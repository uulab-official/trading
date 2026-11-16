# Explicit research-input inspection (v42)

## Goal

Continue from v41 `2b0b4cd6ceb2d136029056cb42c8ac505e55d2cb` on the authorized
development branch and Draft PR #1. Add an explicit optional check that the
observed fixture satisfies the original producer's input rules under the saved
report's candidates/settings/schema and that its five metadata fields match.
Do not rerun research, acquire prices or operate an account.

```text
python tools/paper_research_report.py REPORT_JSON --fixture LOCAL_FILE --validate-fixture [--html NEW_FILE]
```

`--validate-fixture` requires `--fixture`. Omission preserves the exact v41 JSON
envelopes and hash-only acceptance of arbitrary/empty bytes. The existing
six-field `fixture_check` contract remains unchanged.

## Authoritative validation reuse

The existing `load_fixture` only checks row shape for unannotated data. Do not
mislabel that as research-input validity or copy another validator. Make two
literal extractions, preserving expressions, validation order and exceptions:

1. Move `tools/paper_replay_dashboard.load_fixture(raw)` into
   `trading_bot.fixture_input`, retaining its import/re-export from the original
   tool. Preserve duplicate-aware JSON, legacy float decoding and provenance.
2. Move the pre-evaluation block of `run_research` into
   `trading_bot.research_input.prepare_research_input(data, fixture_sha256, *,
   candidates=None, settings=None, robustness=False) -> (spec, cleaned_rows)`.
   The producer delegates once and otherwise retains its evaluation behavior.

Keep the exact order: robustness bool, paper mode, settings keys, candle shapes,
annotated provenance, experiment/candidate/fold/work limits, robustness budget,
strict timestamp continuity and numeric/OHLC checks. Preserve `number(str(v))`
and all existing limits. Do not substitute the differently bounded Candle model.
The saved report contract and all 16 execution-fingerprinted modules stay intact.

## One source observation

Refactor v41's reader into one shared private observation function. Keep public
`check_fixture(path, expected_sha256) -> dict` streaming without collecting bytes.
Add `read_fixture_snapshot(path, expected_sha256) -> (dict, bytes)` that captures
the same bounded chunks already hashed. Retain all descriptor, named-file,
parent, metadata, regular-file and 32 MiB plus sentinel protections. Never reopen
the source to parse it. Only return the captured bytes after stable observation.

New `research_fixture_data.check_fixture_data(path, report)` validates the report,
obtains the snapshot and returns `(fixture_check, fixture_data_check)`. Load the
observed bytes through the shared loader, then prepare using the **actual** hash,
the report's candidates/settings and `robustness=(schema_version == 2)`.

## Separate result

Only the explicit flag adds `fixture_data_check` to CLI JSON. Exact fields:

```text
status: matched | mismatch | invalid
scope: research_input_consistency
run_id: the validated report's run_id
fixture_sha256: fixture_check.actual_sha256
expected: {symbol, timeframe, bars, first_bar_ms, last_bar_ms}
observed: the same five fields, or null for invalid input
mismatches: ordered list of differing field names, or [] for invalid input
error: null, or research_input_invalid for invalid input
```

Metadata field order is `symbol`, `timeframe`, `bars`, `first_bar_ms`,
`last_bar_ms`. Expected last time uses the validated report's first timestamp,
count and timeframe. Observed metadata comes only from a successfully prepared
input, including the actual final cleaned row timestamp. A shorter source can
be invalid under the report's window settings before metadata comparison; never
manufacture metadata from failed preparation. Formatting-only byte changes may
produce byte mismatch and data matched simultaneously.

Expected parsing/validation errors produce the fixed invalid result without
private details or partially decoded data. A completed invalid/mismatched data
check exits **1**, and optional diagnostic HTML can still be published. Exit 0
requires all explicitly requested checks matched. File access/observation or
unexpected preparation failures exit 2 without HTML; publication uncertainty
and interruption retain exits 3 and 130 and override comparison outcomes.

`validate_fixture_data_check(value, report, fixture_check) -> dict` is pure and
strict. Bind it to both report run_id and observed byte hash. Validate expected
and observed metadata, exact fields, strict integer types, derived last time,
ordered differences and status/error/null consistency. Return a fresh nested
copy. It must not load/parse data, call preparation, acquire data or execute.

## HTML and imports

Extend `render_research_report(report, *, fixture_check=None,
fixture_data_check=None)`. A data context requires a byte context. Keep report,
byte and data claims separate. Add `data-fixture-data-status` and
`data-field="fixture-data-status"` with the exact labels:

- not_requested: **입력 구조 미검사**
- matched: **입력 구조·정보 일치**
- mismatch: **보고서 입력 정보 불일치**
- invalid: **연구 입력 형식 오류**

Show expected and observed fields for completed valid input; use exact UTC
timestamps and integer counts. Invalid input must show no invented observed
values. Preserve report values, escaping, CSP and pure rendering. Matching means
accepted by shared research-input rules under the report's configuration and
matching metadata, not provider truth, result reproduction or execution evidence.

Default/hash-only paths retain the v41 execution/data-import guard. The explicit
data path may import existing pure data/provenance definitions, including the
module that also defines acquisition APIs, but may never invoke acquisition,
network, strategy, research, configuration or account operations. Use a narrowly
adjusted guarded subprocess for this path. Add packages/data to the CLI's local
module search roots without importing it by default.

## Verification and delivery

TDD for shared preparation, same-byte capture, strict data context, CLI and
renderer. Record pre-extraction deterministic producer outputs and compare after
excluding only generation time. Exercise synthetic float/string data and
annotated synthetic imports, malformed/nonfinite OHLC, gaps/duplicates, metadata
mismatches, settings/budget rejection, invalid JSON/provenance and races. Retain
private no-clobber publication and error precedence. Run full regression and
fresh independent review; publish only the tested tree with an expected-head
guard and verify exact-head CI. Keep main, Draft PR state and live boundaries.
The previously blocked actual browser visual QA remains explicitly unverified.
