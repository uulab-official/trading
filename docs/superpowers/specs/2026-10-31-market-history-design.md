# Closed market history → existing paper replay

Continue the approved paper-first platform in PR #1. Reuse the existing execution,
report and dashboard paths; do not add another broker or execution engine.

## Scope and approach
An explicit CLI downloads bounded public Upbit KRW spot minute candles to a NEW
immutable local fixture. The existing exporter replays that fixture offline and
carries a small validated provenance record into its read-only report/UI.
Alternative: stream directly into a running strategy. Deferred: it would mix
network uncertainty with order execution before broker reconciliation is ready.
Alternative: silently fill missing candles. Rejected: fabricated prices/volumes
would hide missing observations. This increment rejects gaps, including the tail.

No API keys, private account endpoints, orders, remote dashboard deployment or
live activation. No external network in automatic tests. New Python code is
standard-library-only and supports Python 3.10–3.12. The existing checkpoint
runner retains its POSIX local-filesystem requirement.

## Contract
Fixed HTTPS host/path; no arbitrary URLs or redirects; GET only. At most 10,000
bars, at most 200 rows/page; UTC opening timestamps, Decimal price/volume strings.
A fixed exclusive boundary, rounded down after a five-second clock safety margin,
excludes unfinished candles. Confirm market/unit/OHLC, duplicate consistency,
strict pagination progress, full requested count and uninterrupted intervals.
429/5xx/network failures have bounded retries; 418 is an immediate stop.

Publication is atomic create-if-absent, never overwrite/symlink traversal. Export
revalidates imported data and its hash before the original runner can write.
Source metadata is a self-reported provenance record, not exchange authentication
or evidence of profitability. Unknown local fixtures remain unlabeled; no claims
that generic/synthetic fixtures are real market observations.
