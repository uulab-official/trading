# One-command Paper Lab Design

## Intent and authorization

The user asked to continue all of the existing crypto, stock and broker-paper
experiments after publication of `b4e2bad2de6e215c227ded8bb69b3c950abc1bd7`.
Repeated continuation requests authorize implementation, review, main publication
and exact-head CI verification without another design-approval pause. The
implementation choices below are the builder's decisions.

Existing engines, reports and backup tools work separately. The next useful
deliverable is one reproducible local experiment containing crypto, KRW and USD
stock strategies, order lifecycle faults, order comparison and recovery, with
an entry page linking the evidence. Current Toss/KIS environment credentials are
absent; an offline configuration diagnostic should make that blocker explicit.

## Approach

Compose existing Python 3.10+ engines and auditors. Retain their accounting,
backup and report semantics. A new `tools/paper_lab.py` coordinates fixed bundled
synthetic fixtures; it neither accepts live data nor provides order transport.
The existing stock smoke gets optional persistence and a separate USD fixture.
A separate `tools/broker_readiness.py` checks only allowlisted environment-variable
presence, with no validation/authentication or network side effects.

A new hosted dashboard would add deployment and authentication without improving
the local experiment. Rewriting the engines would duplicate already-tested
accounting. The selected composition needs no new Python package; the combined
lab uses the repository's existing dependencies (including PyYAML), while the
readiness diagnostic remains standard-library-only and works with `python -S`.

## Contracts

### Retained KRW/USD stock exercise

`tools.stock_paper_smoke.run_smoke(output_dir=None, *, currency="KRW")` preserves
the existing no-argument behavior. Only exact `KRW` and `USD` are accepted.
Both fixtures are explicitly synthetic, with 55 bars; the USD fixture uses the
fictional symbol `DEMO` and fixed synthetic OHLC scaling, not an FX conversion or
actual US market data. Initial cash is `1000000` KRW or `10000` USD; fees `0.1`,
fast/slow `5`/`20`, allocation `20` remain simulation assumptions.

Retained runs create only a new private directory in an existing trusted parent:
`candles.json`, `strategy.sqlite`, `audit.json`, `report.html`, `smoke.json`, and
`backup/`. Existing files/directories/symlinks must not be overwritten. New report
files are `0600`, directories `0700`. Default temporary runs remove artifacts.
Partial retained evidence on failure is not a completed run. Summary preserves
existing fields and adds currency/symbol, settings and artifact-retention state.
The same fixture replayed twice must add zero bars/fills; full audit, deep backup
verification and report generation run for both currencies.

### Broker configuration diagnostic

`inspect_environment(environ=None)` returns a JSON-safe, allowlisted presence
report. It accesses only `TOSS_ACCESS_TOKEN`, `TOSS_CLIENT_ID`,
`TOSS_CLIENT_SECRET`, `KIS_VTS_ACCESS_TOKEN`, `KIS_VTS_APPKEY`,
`KIS_VTS_APPSECRET`, `KIS_VTS_CANO`, `KIS_VTS_ACNT_PRDT_CD`.
Only nonblank strings count as present. No values, lengths, hashes, account
numbers, unrelated names, exception text or environment dumps appear in output.

Separate capability entries cover Toss token reuse, explicit Toss token issuance,
KIS VTS quote and KIS VTS daily-order inquiry. Each reports required/missing
variable names, presence booleans, `configuration_present`, and
`authenticated: false`. Presence never means validity, access, account binding,
or readiness to trade. Token issuance stays explicitly opt-in in the existing
collector; this diagnostic issues no token. Top-level `mode: paper`,
`live_orders_enabled: false`, `safe_to_submit: false`, and
`actual_broker_requests: 0` are fixed.

CLI default JSON, optional `--format text`; exit `0` when the diagnostic completes,
including missing credentials, and `2` on invalid invocation/failure. It writes
no files, imports no broker transport, and never reads dotenv/keychain/files.
Human output explains the next configuration step and points to existing guides.

### Combined lab

`run_lab(output_dir=None, *, now=None)` runs in a private temporary directory by
default; optional output must be a newly created directory in an existing trusted
parent. `now` is aware UTC-convertible datetime or current UTC. The combined CLI
is `python tools/paper_lab.py [--output-dir NEW_DIRECTORY]` and succeeds only when
every component succeeds. Failures return static diagnostic and exit `2`, retain
partial evidence, and never publish a successful top-level `summary.json` or
`index.html`. Existing destinations are always refused.

The lab uses a scoped Python socket/DNS guard, restored on success and failure;
this is regression defense, not an OS-level sandbox or protection from hostile
native code. It starts no server, scheduler or child process and contacts no
broker even when credentials are present. No live mode or optional network flag.

Components: crypto replay -> resume without extra trades -> independent audit ->
deep backup -> HTML; KRW and USD stock retained exercises; the existing retained
order-lifecycle fault trial; the existing retained fake-KIS comparison/backup/
restored-idempotence trial; offline broker configuration diagnostic.

Produce `crypto/`, `stock-krw/`, `stock-usd/`, `order-lifecycle/`, `order-review/`,
`broker-readiness.json`, `portfolios.json`, `portfolios.html`, `summary.json` and
`index.html`. The portfolio view has exactly three independently audited cards,
with no combined balance/return or currency conversion. Lifecycle and review
trials are independent examples; do not add their repeated executions together.
The index is escaped Korean static HTML, restrictive CSP, no JavaScript/external
resources/forms, fixed safe relative links, KST generation time and explicit
synthetic provenance. Component failure must prevent a success page.

### CI coverage evidence

Persist generated `coverage.xml` as a per-Python GitHub Actions artifact so that
coverage is available without a configured third-party account. Codecov upload
becomes explicit repository-variable opt-in and remains advisory; required tests
and artifact generation must not be weakened. Verify official current action
documentation before changing versions or arguments. Do not configure secrets,
publish containers or change release triggers. Update the stale completed Linux
CI checklist gate with a concrete existing successful run.

## Evidence and limitations

Run meaningful real integration tests for retained artifacts, separate currency
ledgers, repeated execution, links/CSP/escaping, existing-path refusal, error
cleanup of runtime guards, and secret-free diagnostics. Add both new test modules
to the network-forbidden offline suite. Run full pytest, extended offline checks,
fatal lint, actual retained lab, independent review, then publish with an expected
SHA lease and wait for the exact published head's full CI (including Docker).

Synthetic runs do not validate live Toss/KIS credentials, exchange calendars,
actual broker fills, FX, fees/taxes, returns, ongoing operations or disaster
recovery objectives. This increment cannot perform authenticated checks without
user-provisioned credentials. Keep that boundary visible in the final result.
