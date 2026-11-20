# Stock paper portfolio backup and unified offline monitoring

The trading monorepo now has a **read-only audit and backup workflow**
for local simulated stock orders, plus a consolidated operations query
that independently audits the existing crypto paper ledger and the
new stock strategy ledger.

This does not connect accounts, execute live orders, deploy a web
server, or provide continuous availability monitoring.

## 1. Audit stock strategy state first

Use the exact options used to initialize the strategy:

```sh
python tools/stock_paper_forward.py data/toss-005930-paper.json \
  --database data/stock-strategy.sqlite --mode paper \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20 --audit-only
```

This checks all previously simulated stock fills, the strategy cursor,
the immutable processed-candle prefix hash, and cash/equity accounting.

## 2. Create an isolated stock-paper backup

Stop automated or manual writers and preserve the current normalized
fixture before running:

```sh
python tools/stock_paper_backup.py create \
  --fixture data/toss-005930-paper.json \
  --database data/stock-strategy.sqlite \
  --destination data/backups/stock-001 \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20
```

The backup tool uses SQLite's **native online backup API** to capture a
consistent snapshot even when the source journal is in WAL mode. It
records hashes for the normalized fixture and SQLite database, then
independently replays the backup before publishing a *new* backup folder.
It does not overwrite a pre-existing destination.

It writes `candles.json`, `stock.sqlite` and `manifest.json`.
The manifest records the exact strategy parameters, market, currency,
processed bar count, simulated trade count and SHA-256 digests.

The backup is a snapshot of a cooperating local strategy database.
Because independent writers may modify the source after it is audited,
the tool compares pre- and post-snapshot replay results and rejects
mismatches. This is **not** a verified atomic transaction spanning the
separate JSON fixture and SQLite files; operators should stop all
writers when capturing evidence. A malicious actor able to replace both
backed-up files and the unsigned manifest is outside this threat model.

## 3. Independently verify a backup

```sh
python tools/stock_paper_backup.py verify data/backups/stock-001
```

Default `verify` recomputes hashes and performs a **deep replay** from
private temporary copies. It checks all simulated fills and strategy
state without mutating the backup directory. `--bytes-only` skips
economic replay, returning a weaker verification result explicitly
marked `economic_replay_verified: false`.

**No automatic restore command is provided.** Never overwrite a running
database. Test a private copy first and keep the incident original.

## 4. Inspect separate audited portfolios in one command

```sh
python tools/paper_portfolio_status.py \
  --crypto-fixture data/upbit-btc-1h-archive.json \
  --crypto-checkpoint data/upbit-btc-1h-paper.json \
  --stock-fixture data/toss-005930-paper.json \
  --stock-database data/stock-strategy.sqlite \
  --stock-initial-cash 1000000 --stock-fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20
```

The JSON response contains one audited `crypto` record and one
independently audited `stock` record. It is intentionally
`audited_offline_only`, and `cross_portfolio_total` is always
`null`: KRW, USD, and USDT are not aggregated without verified
exchange rates, a unified risk budget and account reconciliation.

This is an **on-demand command**, not an authenticated production
dashboard. It does not measure API connectivity, worker liveness,
live broker positions, market-data freshness, exchange open hours or
real-time account equity. A failure of either audit makes the command
exit nonzero instead of returning a falsely healthy combined result.

## Testing and operations

```sh
python tools/offline_checks.py
```

The suite includes mocked/no-network tests for WAL backups, replay
integrity, hash tampering, new destination refusal, cross-market
nonaggregation and tampered checkpoint refusal.

Outstanding gates include real Toss OAuth/IP/data verification,
stock calendars, forward market sessions, token reuse, scheduled
monitoring, exchange sandbox orders, stock-specific recovery drills,
authenticated access controls, and independent deployment/CI checks.

**Live automated trading remains unapproved.**


## Zero-credential end-to-end synthetic smoke

Run `python tools/stock_paper_smoke.py` from the repository root.
The smoke uses
`config/examples/synthetic_toss_stock_paper_1m.json` (55 explicitly
**synthetic** bars, not actual Toss prices), and creates all strategy,
report and backup artifacts inside a disposable private temporary
directory. It checks that identical replay produces zero duplicate fills,
that an independent audit reconstructs state, that SQLite backup deep
verification succeeds, and that the Korean report contains only offline
paper information. The smoke does not authenticate Toss, invoke KIS VTS,
place any real orders, or prove future trading profitability.
