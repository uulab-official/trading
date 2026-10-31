# Paper operations verification — 2026-10-31

Baseline: `4b92d793193d8e402f823f7d7ac55bfbe66ac5ee`, feature branch `feat/smc-market-structure`.

## Changes validated

- Immutable reservation identity after cancellation, legacy migration, atomic order/audit rollback, competing writers, restart and unknown-order retention, Decimal context isolation.
- Transaction-consistent read-only snapshot, false-positive health corrected, odd-path URI handling, filters/pagination, cross-origin/Host rejection, disabled writes, schema and amount validation.
- Connected dashboard summary/audit, exact decimal display, nonoverlapping refresh, stale last-good state, synthetic provenance, KST display, responsive light/dark layout.
- Explicit non-overwriting synthetic lifecycle demo; no broker API calls.

## Evidence and limits

Targeted Python tests were run in this container with Python 3.13.5. Node client tests were run with Node 22.16.0. The new suites contain 37 Python cases and 9 Node cases; the two existing journal/API modules add 5 compatibility cases. See the PR verification comment for final execution results and current CI state.

Chromium layout/client smoke tests used `--offline-render` with actual locally persisted synthetic journal snapshots. Desktop/mobile/dark, status filter, stale-data retention and recovery passed with no page errors. Direct Chromium navigation to a loopback URL was blocked by the environment's administrator policy and was not bypassed. The Python HTTP tests separately exercised real loopback requests; browser-to-HTTP/CSP integration is not claimed as locally verified.

The container could not clone the full repository due DNS restrictions. Targeted upstream files were read through the GitHub connector; the original journal/server blob hashes were verified before modification. This is **not** a local run of the entire legacy repository suite. Supported Python 3.10–3.12 and full legacy pipeline results must be checked on the new GitHub commit, not inferred from a previous green run.

## Upstream design references (no source copied)

- NautilusTrader execution: https://nautilustrader.io/docs/latest/concepts/execution/ — separates commands, definitive outcomes and unknown outcomes; informs conservative local transition handling.
- Freqtrade REST API: https://www.freqtrade.io/en/stable/rest-api/ — informs local-only operational API boundaries; this implementation does not claim Freqtrade's authentication facilities.
- SQLite URI filenames: https://www.sqlite.org/uri.html — URI escaping and `mode=ro` connection semantics.

These are architectural/documentation references, not vendored dependencies. No external source or assets were copied.
