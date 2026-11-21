# Production readiness gate — trading monorepo

**State (2026-11-21): LIVE TRADING IS NOT APPROVED.**

This project has an offline spot paper replay engine with deterministic
accounting and checkpoint tests. The new Upbit quotation snapshot and
append-only archive are *public market data importers*, not a validated
always-on trading service, authenticated exchange adapter, or production
order executor. The Rust and Python dashboard state paths remain separate.

No environment variable, version tag, Docker deployment, report export,
dashboard screenshot, successful synthetic run, or positive backtest return
may automatically enable live order placement.

## Gate A — research / deterministic paper

- [x] Paper-only mode, no broker credentials required
- [x] Completed-candle signal evaluation and next-available-open simulated fills
- [x] Single-symbol cash/holdings/fees ledger and bounded risk entries
- [x] Validated checkpoint resume and exclusive POSIX writer lock
- [x] Independent replay checkpoint audit (tamper refusal)
- [x] Static offline HTML report with restrictive CSP
- [x] Strict historical OHLCV CSV import and provenance
- [x] Public Upbit one-shot candle snapshot with completed-bar/staleness/gap guards
- [x] Append-only archive with overlapping-history equality checks
- [x] Linux Python 3.10/3.11/3.12, required lint and Docker verified for published `8aaaf71` ([run 44904305168](https://github.com/uulab-official/trading/actions/runs/44904305168)); every later revision still requires its own exact-head CI
- [x] Black/isort failures now block CI across packages, apps and tools; pinned direct tools and 148-file local check recorded in [quality verification](docs/QUALITY_UTC_VERIFICATION_2026_11_21.md)
- [x] Deprecated UTC clock calls removed while preserving legacy timestamp formats; timezone-independent historical request epochs regression-tested
- [x] Rust binary reuses the public library, package compiler warnings are errors, and parser-only compatibility checks cover every CLI mode; operational Rust broker paths remain unvalidated
- [ ] Obtain real market sample and manually verify candles against exchange history
- [ ] Evaluate long history and market regime diversity; remove survivorship/lookahead biases

## Gate B — continuously supervised PAPER operation (NOT complete)

- [x] Opt-in local paper supervisor with minimum poll period, atomic health, fail-closed retry threshold
- [x] Supervisor and one-shot exclusive local owner lock (including idle periods)
- [x] Restart-persistent halted heartbeat; explicit operator recovery required
- [x] Release notes forbid unverified live activation and unsafe key setup
- [x] Incremental v1-compatible checkpoint prefix hashing (local equivalence checked)
- [x] Paper risk-entry halts surfaced as risk_blocked (not healthy)
- [x] Read-only heartbeat freshness and clock-skew probe
- [x] macOS launchd paper-only template with no auto-restart after halted state
- [ ] Reliable timed ingestion with reconnect budgets, backoff/jitter, and validated multi-day runtime
- [ ] Clock-skew detection and NTP operational checks; reject stale or future prices
- [x] Bounded historical pagination to a **separate candidate** with overlap and no-gap checks
- [x] Independent backfill-candidate/previous-ledger review before manual promotion
- [x] Manual backup/candidate review excludes an active local paper supervisor
- [x] Consecutive paper failure budget persisted atomically across process restarts
- [ ] Operator-validated complete backfill integration for live paper supervisor (not automated)
- [ ] Crash-safe archive+strategy state orchestration and startup reconciliation
- [ ] Circuit breaker for network instability, schema drift, data gaps, bad ticks
- [ ] Single source of truth for positions, trades, and portfolio in operator UI
- [ ] User authentication, durable audit trail, external alerts, and measured SLO monitoring (local heartbeat probe exists)
- [ ] Credential-free paper environment continuously exercised for at least 2 weeks
- [x] Immutable-by-policy paper archive/checkpoint pair backup and SHA-256 manifest verification added
- [x] Optional full economic/risk replay on a backed-up paper pair, including forged balance rejection
- [x] Manual backup and candidate review require exclusive stopped-supervisor ownership lock
- [x] Full pre-capture checkpoint audit rejects tampering before altering public history
- [ ] Disaster-recovery and backup/restore drill completed (manual recovery test pending)

## Gate C — broker sandbox order lifecycle (NOT complete)

- [x] Separate offline cash-only KRW/USD stock paper book, with whole-share fills and duplicate order safeguards
- [x] Durable SQLite stock-paper execution journal with replay, idempotence and cash protection
- [x] Saved Toss 1m JSON parser with closing-time to opening-time conversion
- [x] KRW 15:30 aggregates excluded from fixed-minute replay to avoid later-trade leakage
- [x] Opt-in Toss OAuth + read-only candle collector (code only; genuine authenticated endpoint not yet exercised)
- [x] Existing Toss access-token reuse, explicit opt-in for new issuance, and no automatic refresh/retry (mock verified)
- [x] Completed-bar MA-cross paper stock backtest with next-available-open fills
- [x] Transactional completed-bar stock strategy cursor and simulated fills in SQLite
- [x] Exact committed-cursor fill reconciliation rejects deleted or extra historical fills
- [x] Real SQLite concurrent append, cursor-write failure and abrupt process-exit recovery regressions
- [x] Mandatory independent expected-equity check inside SQLite transaction before strategy fills/cursor commit
- [x] Read-only stock paper bar-close maximum drawdown audit checked against recorded fills
- [ ] Exchange-side max loss/drawdown enforcement and independently verified live risk controls
- [x] Read-only full replay audit and static local stock PAPER HTML report
- [x] SQLite native stock-paper strategy backup with SHA-256, deep replay and non-overwriting output
- [x] Single offline crypto + stock audit command (portfolio totals intentionally unaggregated)
- [x] Local CSP-restricted static crypto + stock paper HTML dashboard from pre-audited JSON
- [x] Bundled synthetic 55-bar stock fixture and one-command end-to-end offline smoke test (executed locally on 2026-11-21)
- [ ] Test stock paper backup/restore under actual operational failures and establish RPO/RTO
- [ ] Real-time multi-asset monitoring with authenticated operators, fresh data checks and verified FX
- [ ] Validate real Toss quote credentials and feed, market calendar, operational monitoring and stock-ledger backup
- [ ] Validate actual Toss OAuth/IP allowlisting and real quote ingestion against live API
- [ ] Production-grade OAuth token reuse (one active token per client), rate-limit budgets, and 429 recovery
- [ ] Verify US/KR market calendars, stock splits, tick sizes, tax/fee schedules and FX independently
- [ ] Forward-only scheduled stock paper trading with exchange session calendar and durable strategy cursor
- [ ] Unified market risk controls across simulated stock and crypto ledgers
- [ ] Connect authenticated read-only Toss stock data to validated paper candles
- [x] KIS VTS-host-only current-price read-only probe with mock tests (actual API not yet exercised)
- [x] Pure KIS VTS limit-order preparation and unverified response classification without order transport
- [x] KIS VTS read-only daily-order observation with bounded complete pagination and private-field filtering (mock verified; actual account unvalidated)
- [ ] Validate KIS official virtual-trading API end-to-end
- [ ] Exchange-specific symbol and step/min-notional filters
- [ ] Clock and nonce, API authentication, and least-privilege credentials
- [ ] No withdrawal permission; IP restrictions where supported
- [ ] Broker reconcilation of balances, orders, trades, and fee currency
- [x] Durable offline SQLite order-intent journal with unknown-ACK state, unique client IDs, and concurrent-writer tests
- [x] Local read-only SQLite intent preflight rejects corrupt state, unresolved orders and duplicate broker IDs
- [x] Read-only journal instances refuse mutations; exact intent quantities are independent of Decimal context
- [ ] Independently verify all broker evidence identifiers against authenticated sandbox responses
- [ ] Integrate journal BEFORE network submission in a verified broker sandbox adapter (not connected yet)
- [ ] Broker idempotency: client IDs, unknown-result lookup, deduplication
- [x] Offline synthetic broker emulator and regression tests for partial fills, cancellation, lost ACK and overfill refusal
- [x] Separate durable offline stock-order event journal with exact KRW deltas, partial fills, cancellation races and duplicate delivery protection
- [x] Stock-order journal replay/hash/tail audits, concurrent writers, rollback and real process-exit recovery tests
- [x] Stock-order init/record/read-only status CLI and a 14-event synthetic lifecycle demonstration with four unique executions
- [x] Read-only comparison of audited local orders with strict saved KIS observations; mismatch, unknown, related-history and stale/future findings (no automatic reconciliation)
- [x] Private Korean static order-review JSON/HTML reports with scoped counts, escaped text, no external resources and permanent unverified/no-submit flags
- [x] Native SQLite stock-order backup, strict SHA-256 plus full-replay verification and exclusive new-path paper restore candidates preserving unresolved states
- [x] Synthetic collector-to-review-to-backup-to-restored-journal trial with deliberate discrepancy and duplicate-replay checks, without actual broker requests
- [x] One-command synthetic crypto/KRW/USD paper lab with independent audits/backups, retained reports and separate order-lifecycle/recovery trials
- [x] Offline Toss/KIS configuration-presence diagnostic with no secret values, token issuance or authentication claim
- [ ] Actual broker partial fills, cancel/replace, timeout, reconnect, and retry semantics (not connected)
- [ ] Fail-closed start: no new orders until all prior intents reconcile
- [ ] Explicit operator kill switch, exits and exposure reduction drill
- [ ] Negative testing (HTTP 429, 5xx, lost ACK, clock drift, network partitions)
- [ ] Sandbox end-to-end regression and an independent security review

## Gate D — limited live capital release (NOT APPROVED)

- [ ] A named accountable operator independently signs off Gates A–C
- [ ] Separate explicit execution flag plus out-of-band approval for live orders
- [ ] Dry-run diff: paper risk decisions vs sandbox order behavior reviewed
- [ ] Tight gross and per-order exposure budget; no leveraged/borrowed positions
- [ ] Max daily loss, total drawdown, and max order notional enforced *before* submit
- [ ] Exchange-side risk and portfolio reconciliation; reject uncertain state
- [ ] On-call response, alert escalation, rollback, and emergency shutdown documented
- [ ] Explicit maintenance windows, release canary, and audited change approvals
- [ ] Real-live first order manually supervised and verified end-to-end

## Gate E — operations (NOT complete)

- [ ] Audit records durable and immutable/reconstructable
- [ ] Backup retention and restore tests meet agreed RPO/RTO
- [ ] Monitoring distinguishes stale display from active broker positions
- [ ] Fault-injection exercises repeated across planned release versions
- [ ] Secrets never committed, echoed into logs, or copied into HTML reports

Local verification evidence: [2026-11-21 report](docs/VERIFICATION_2026_11_21.md).
Required remote CI results are recorded per commit in [GitHub Actions](https://github.com/uulab-official/trading/actions/workflows/ci.yml).

## Safe local commands (no actual orders)

```sh
python tools/offline_checks.py
python tools/offline_checks.py --include-backtest

# Requires internet; public market quotation only:
python tools/upbit_paper_archive.py --market KRW-BTC --unit 60 \
  --archive data/upbit-btc-1h-archive.json

# Offline replay of imported public candles:
PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
python -m trading_bot.paper_runner data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json --initial-balance 1000000

python tools/paper_report_html.py data/upbit-btc-1h-archive.json \
  data/upbit-btc-1h-paper.json --output data/upbit-btc-1h-report.html
```

Live-market data from the old data package is deliberately unavailable.
Do not interpret the existence of older Rust/legacy bot configs or Docker
deployment scripts as authorization to trade real assets.

References: [public candle snapshot](docs/UPBIT_PUBLIC_PAPER.md),
[append-only archive](docs/UPBIT_PAPER_ARCHIVE.md),
[audited offline dashboard](docs/OFFLINE_PAPER_REPORT.md),
[checkpoint verification](docs/OFFLINE_CHECKPOINT_AUDIT.md).
