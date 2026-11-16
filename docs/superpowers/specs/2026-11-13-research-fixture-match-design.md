# Explicit local research fixture matching (v41)

## Goal and authorization

Continue the user's authorized trading-system development from v40 commit
`c6c9fa934a8480bd97d8f55cb282bc24728a8bbf` on `feat/smc-market-structure` and
Draft PR #1. Add an explicit optional comparison between an existing local file
and the raw-byte SHA-256 recorded in a saved research report. No main merge,
deployment, credential access or live execution is part of this increment.

## CLI and outcomes

Extend the existing command, retaining its behavior when `--fixture` is absent:

```text
python tools/paper_research_report.py REPORT_JSON [--fixture LOCAL_FILE] [--html NEW_FILE]
```

Read and validate the saved report first. Only an explicit `--fixture` opens a
fixture. Stream at most 32 MiB plus one sentinel byte from a regular local file,
matching the producer's existing 32 MiB input limit. Hash exact bytes without
JSON parsing or normalization. Final-component symlinks, directories, FIFOs,
missing/unreadable files, oversized inputs and detected concurrent changes fail
closed. Check file identity, size, mode and nanosecond modification/change times
before and after reading, and recheck the named path. An existing parent alias
may be resolved initially but must not silently redirect the read.

A successful optional check adds exactly one `fixture_check` object to the
existing JSON envelope. It has these exact fields:

```text
status: matched | mismatch
scope: local_fixture_bytes
algorithm: sha256
expected_sha256: report.experiment.fixture_sha256
actual_sha256: SHA-256 of the bytes observed by this check
size_bytes: integer in [0, 33554432]
```

No file names, paths, raw bytes or underlying exception details are included.
The default JSON envelope stays unchanged. `research_report_checked` still
means saved-report consistency; it never implies a successful fixture match.

| Exit | Meaning |
| --- | --- |
| 0 | Report checked; an explicitly requested fixture matched; optional HTML published |
| 1 | Report checked but explicit fixture digest differs; optional diagnostic HTML published |
| 2 | Invalid/unavailable report, fixture, arguments or output, or a pre-publication error |
| 3 | HTML publication confirmation uncertain; preserve and inspect existing output |
| 130 | Interrupted; inspect any output before retrying |

Output errors and interruption take precedence over a digest mismatch. A fixture
read/check failure returns only `research_report_fixture_unavailable` and does
not publish HTML. A missing `--fixture` argument remains an argument error.

## Module boundaries and HTML

Add a standard-library-only `trading_bot.research_fixture` module exposing:

- `MAX_FIXTURE_BYTES = 32 * 1024 * 1024`.
- `check_fixture(path, expected_sha256) -> dict`: bounded stable-file observation
  and digest comparison, raising on unavailable/invalid input.
- `validate_fixture_check(value, expected_sha256) -> dict`: pure exact-field,
  digest, scope, size and status-consistency validation, returning a new dict.

Extend the pure renderer to
`render_research_report(report, *, fixture_check=None) -> str`. Validate the saved
report and any supplied comparison against that report's expected digest. A
caller cannot label inconsistent hashes as matched or reuse a result from a
different expected digest. Do not add fields to the saved research schema.

Show a distinct, prominent local-file section with **원본 파일 미대조** when not
requested, **파일 해시 일치** on a match, or **파일 해시 불일치** on a mismatch.
For completed comparisons show expected/observed hashes and byte count. Preserve
all existing report metrics, diagnostics, escaping and standalone CSP. Do not
read a fixture from the renderer, add script/network content or display paths.

The claim is limited to bytes observed during this check. Matching does not
authenticate a data provider, establish valid market data, reproduce execution,
prove the producing code revision, or verify a market-study receipt. Ordinary
metadata checks detect concurrent changes; this is not an authenticated atomic
filesystem snapshot. The report's original generation time remains explicitly
the report's time. Later source changes do not update an exported HTML snapshot.

## Retained constraints

Keep the original research reader/schema, producer, execution fingerprint
modules, dashboard and study-receipt contracts unchanged. Never acquire prices,
rerun research or access an account during inspection. Reuse the existing
owner-private, parent-identity-bound, no-clobber HTML publisher and its uncertain
completion handling. A fixture or alias is never a replaceable output target.

Use synthetic inputs only. Actual browser visual QA remains unverified because
the prior local-file browser denial must not be bypassed with another surface.

## Verification

Test byte-exact comparison, unchanged input bytes, zero/exact-limit/oversized
files, bounded growing reads, symlinks/FIFOs, I/O errors, in-place modification,
path replacement and cleanup. Test default no-fixture behavior and malformed
comparison contexts, match/mismatch CLI and HTML for both schemas, explicit exit
codes, no-clobber output and error precedence. Run real subprocesses with the
existing execution/network/account guards. Independently review the complete
increment, run the required regressions, publish with an expected-head guard,
and verify CI against the exact published tree and head.
