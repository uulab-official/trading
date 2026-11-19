# One-shot public Upbit → paper portfolio cycle

This is the integrated **paper-only** entrypoint. It makes one public
quotation request, appends verified completed candles to a local archive,
and replays the resulting history through the deterministic spot paper
engine with a restart-safe checkpoint. It neither reads exchange balances
nor authenticates or submits orders.

## Run

```sh
python tools/upbit_paper_cycle.py \
  --market KRW-BTC --unit 60 --count 200 \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --initial-balance 1000000
```

After the command completes, the printed JSON explicitly contains
`mode: paper`, `live_orders_enabled: false`, number of appended
completed candles, processed bars, number of simulated fills, marked
equity in KRW, and entry-block flags.

Re-run the **same** command later. Overlapping historical candles must
remain identical. The checkpoint's saved prefix and assumptions are
independently verified before replay. If no new completed candle was
received, no additional fills are created. Before requesting the next
public candle snapshot, the cycle also independently replays and verifies
any existing checkpoint against its current local archive. A forged
balance, pending signal, or strategy state therefore stops the cycle
**before even fetching or appending market data**; both source files
remain unchanged. If a checkpoint has different
strategy or initial balance assumptions, the run fails closed; use a
different checkpoint for an intentionally new experiment.

The tool uses a single POSIX cycle lock covering **both** archive
capture and checkpoint replay. Concurrent cooperating cycle processes
cannot advance the same portfolio simultaneously. Interrupted cycles
retain the last completed atomic archive and checkpoint snapshots, but
**there is no multi-file transaction**. Subsequent replay validates the
existing prefix and continues; do not manually edit state to bypass it.

## Optional local snapshot report

```sh
python tools/paper_report_html.py \
  data/upbit-btc-1h-archive.json \
  data/upbit-btc-1h-paper.json \
  --output data/upbit-btc-1h-report.html
```

Open the generated HTML locally. This is a static report, **not** a live
exchange dashboard.

## Operational boundaries

- Not an always-on daemon or deployment: run one cycle manually, or
  explicitly configure your own scheduler at the completed-bar cadence.
- No auto-retry or emergency-stop networking control yet. Public feed
  outages and missing history cause **failures**, not invented prices.
- Current snapshot fetch has a maximum of 200 latest minute candles.
  Historical backfill outside this sliding window is **not** implemented.
- The runner uses simulated next-bar fills, not real Upbit orderbook
  depth, liquidity, price limits, partial fills, or fee schedules.
- Do not use an hour's newly opened candle as if it were complete.
  The public collector excludes the forming bar before replay.
- Supported POSIX local filesystems only; distributed locking and
  Windows restart behavior are not validated.
- Zero live order paths: even the `--initial-balance` is a paper number.

Tests:

```sh
python tools/offline_checks.py
```

See [production release gates](../PRODUCTION_CHECKLIST.md) before
considering any real-money execution.
