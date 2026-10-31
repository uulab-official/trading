# Replay dashboard verification — 2026-10-31

## Scope and code identity

Implementation commit: `2d88101623b8cb13016ddd0c4c5ca51c836ab060`.
This connects the existing offline PaperRunner/PaperExchange results to a separate
read-only report/API/UI. It does not enable any broker or live trading.
A following styling/docs-only increment adds content gutters after visual inspection.

## Executed checks

| Check | Result |
|---|---|
| Python report/accounting/atomic publication | 12 passed locally |
| Python exporter path/alias preflight | 4 passed locally |
| Python loopback report API/legacy journal compatibility | 5 passed locally |
| New Node replay-client tests | 7 passed locally |
| Python compile and ES module syntax | Passed locally |
| GitHub Quant Regression, run 41196787271 | Success on implementation commit |
| Existing-engine integration, Python 3.10 and 3.12 | All seven cases passed per runtime |
| GitHub CI/CD Pipeline, run 41196787258 | Success on implementation commit |

Local environment: Python 3.13.5 and Node 22.16.0. Local source was a sparse
connector-backed reconstruction because GitHub DNS/clone was unavailable. Full
repository and original-engine execution were verified remotely by GitHub Actions,
not claimed as local execution. Published source blobs were checked against local
Git blob hashes.

## Regression coverage

Net fees, open-position mark-to-market, inconsistent/NaN balances, duplicate or
mismatched fills, currency mismatch, live-mode rejection, decimal context isolation,
secret-field exclusion, idempotent output, unrelated-file protection, competing
file creation, FIFO/non-regular path rejection, oversized/duplicate-key JSON,
input/checkpoint/report/lock aliasing, missing/corrupt API input, Host/Origin and
write restrictions, HEAD/no mutation, and existing journal read compatibility.

Original runner tests additionally forbid socket/DNS calls while replaying and
verify checkpoint resume, damaged checkpoint rejection, different-experiment
preflight, failed publication recovery without duplicate fills, KRW isolation,
zero-fill runs, report-lock contention, and a separate actual HTTP projection.

## Browser checks and limits

Chromium rendered the new results panel from its actual local HTML/CSS/JS with
explicitly synthetic test report data at desktop 1440px, mobile 390px and dark
1440px. No page errors or page-level horizontal overflow were found. Failed
refresh kept the last good result; reconnection removed its stale state. A fill
identifier containing HTML markup was displayed as text. A visual-spacing
regression (1px content inset) failed before the gutter fix and passed afterward.

This isolated offline render does NOT prove browser-to-server/CSP integration.
The Python HTTP tests and remote engine integrations are separate evidence.
Screenshots are synthetic validation previews, not real trading performance.

## Remaining gates

Real market ingestion, KIS/Upbit authentication and broker reconciliation,
partial-fill/live settlement, admission/execution coordination, multi-account
operation and long-duration fault tests remain unimplemented/unverified. Public
hosting requires a separate authentication and authorization design. Keep PR #1
draft; do not infer production or profitability readiness from green CI.
