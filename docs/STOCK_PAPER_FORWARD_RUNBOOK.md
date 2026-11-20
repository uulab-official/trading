# Forward stock PAPER replay, audit, and static report

This workflow consumes **already normalized, completed** Toss 1-minute
paper candles. It has no API client, brokerage credentials, trading
permissions or live-order code. It does not poll continuously.

The input must contain `mode=paper`, `no_live_orders=true`,
`timestamp_semantics=source_close_to_utc_open`, the stock symbol,
quote currency (KRW/USD), and chronological six-column OHLCV candles.

## Simulate strategy and store all paper fills atomically

```sh
python tools/stock_paper_forward.py data/toss-005930-paper-bars.json \
  --database data/stock-005930-strategy.sqlite --mode paper \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20
```

The runner calculates the MA crossover using completed historical bars
and simulates orders at the **next available bar's OPEN**. The full
proposed paper fill list and historical candle prefix are checked
against the stored SQLite strategy identity and fill ledger in one
`BEGIN IMMEDIATE` transaction. New fills and cursor/hash are then
committed together. A resumed identical file adds no fills. Append-only
future bars are supported; any past-candle revision or strategy changes
fail closed. Manual stock-paper fills cannot be written into a
strategy-owned ledger.

**Do not confuse this with a continuous forward-test daemon.**
The implementation reruns the complete strategy over a supplied fixture
on each invocation and uses SQLite to prevent duplicate simulated fills.
Market gaps and sessions are accepted as provided, not interpreted from
an official stock-exchange trading calendar. Simulated orders are
hypothetical and fill at candle opens without slippage or queue costs.

## Inspect saved state without changing it

```sh
python tools/stock_paper_forward.py data/toss-005930-paper-bars.json \
  --database data/stock-005930-strategy.sqlite --mode paper \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20 --audit-only
```

The read-only audit opens an **existing SQLite database**, independently
reconstructs all simulated fills, validates the strategy cursor and
SHA-256 history, and compares marked equity. It does not create new
fills or change the strategy state.

## Render a private, static Korean report

```sh
python tools/stock_paper_report_html.py \
  data/toss-005930-paper-bars.json \
  --database data/stock-005930-strategy.sqlite \
  --output data/stock-005930-report.html \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20
```

The report uses read-only strategy audit, inline CSS/SVG, an HTML
Content-Security-Policy, and zero external resources or JavaScript.
It shows the simulated cash, equity, holdings, total paper fills,
return assumptions and source-price chart. It refuses to overwrite an
existing report, preserving previous evidence snapshots.

**Important operational boundaries:**

- This is a single-symbol, whole-share, cash-only KRW or USD ledger.
  It never converts currencies or enables margin, lending or shorting.
- The current file replay is not a complete unattended live paper feed.
  Long-running Toss OAuth rotation, rate limits, market calendars,
  corporate actions, taxes, adjusted data, and delayed bar handling
  still require production implementation and validation.
- The database itself is local POSIX/SQLite storage; no independent
  backups, stock-specific RPO/RTO, encryption, authenticated user
  interface, or exchange-side account reconciliation is included.
- An external source can change old bars (corporate adjustments, etc.);
  that triggers an intentional stop rather than silently retroactively
  changing the performance record.
- A positive simulated return does not establish investment profitability.
- Real Toss order endpoints and KIS VTS broker-order submission are
  **not used** by any of these commands.

## Regression checks

```sh
python tools/offline_checks.py
```

The suite includes no-network tests for append-only strategy progress,
restart idempotency, conflicting configurations, tampered fills, manual
order separation, read-only auditing, and static report safety. Tests
and CI must actually pass before these commands are operated without
supervision.


## Independent database backup and cross-market status

For native SQLite WAL-consistent snapshots, deep backup replay, and
on-demand nonaggregated crypto/stock auditing, see
[stock PAPER backup and unified monitor](STOCK_PAPER_BACKUP_MONITOR.md).
Stop active writers before backup. There is still no live broker order
connection or autonomous stock-market scheduler.

## Transactional economic reconciliation (2026-11-20)

An important strategy-ledger safety issue was corrected: independently
replayed equity was previously compared with stored equity **after**
committing the SQLite strategy cursor and simulated fills. The full
expected-equity comparison now occurs **inside the same IMMEDIATE write
transaction**, before commit. Any difference rolls back both the new
fill events and the updated processed-bar cursor; the caller cannot
observe a rejected economic replay with partially persisted state.

The regression suite injects a deliberately incorrect expected equity,
verifies unchanged cursor and fill counts, then confirms the valid
unmodified replay can resume from the original position. This fix is
specific to local offline paper state. It is not authenticated broker
reconciliation or an exchange execution guarantee.

## Independent retrospective risk report (2026-11-20)

The offline command below audits the existing SQLite stock strategy,
independently rebuilds all simulated fills, and calculates a
**bar-close paper-equity maximum drawdown** across the supplied
historical minute bars:

```sh
python tools/stock_paper_risk_audit.py data/toss-005930-paper.json \
  --database data/stock-strategy.sqlite \
  --initial-cash 1000000 --fee-pct 0.1 \
  --fast 5 --slow 20 --allocation-pct 20
```

The report includes initial paper cash, final independently verified
equity, highest/lowest hypothetical bar-close equity, maximum
peak-to-trough percentage drawdown, trade count and candle SHA-256.
The calculation uses exactly the previously simulated next-open fills;
it cannot authorize new orders or silently repair a historical fill.
If the SQLite ledger, candle history or strategy identity differs,
the report refuses to produce a valid result.

**This measures historical simulated bar-close risk, NOT real-time
drawdown protection.** It excludes intrabar liquidation/stop loss,
real spreads, taxes, splits, leveraged positions, broker execution
constraints and unknown missing session candles. Never interpret
the resulting number as a proven future risk budget or replace
server-side risk controls with this report.
