# Upbit public quotation snapshots (paper-only)

This tool creates a **one-shot** offline fixture from Upbit's **public** KRW
market minute-candle REST endpoint. It does not request credentials,
read balances, connect to any private API, create an order, or poll continuously.
The resulting JSON can be replayed through the existing offline paper engine.

Official API: https://docs.upbit.com/kr/reference/list-candles-minutes

The public API provides 1, 3, 5, 10, 15, 30, 60, and 240 minute
candles, with at most 200 candles per request. Upbit documents a candle
request rate limit and states that an interval with no transactions may
be omitted from results. This tool makes **one request** per invocation,
without automatic retries or pagination.

## Create snapshot

```sh
python tools/upbit_public_paper_snapshot.py \
  --market KRW-BTC --unit 60 --count 200 \
  --output data/upbit-btc-1h.json

# Inspect offline without placing orders:
PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
python -m trading_bot.paper_runner data/upbit-btc-1h.json \
  --initial-balance 1000000 \
  --checkpoint data/upbit-btc-1h-checkpoint.json

python tools/paper_report_html.py \
  data/upbit-btc-1h.json \
  data/upbit-btc-1h-checkpoint.json \
  --output data/upbit-btc-1h-report.html
```

For a different experiment, use a **different checkpoint**. This is one
snapshot, **not** an always-on real-time trading service, guaranteed
real-time feed, or evidence of investment profitability.

## Fail-closed safety

- The only URL used is the hard-coded Upbit **quotation** endpoint.
- Only explicit `KRW-BASE` symbols and documented minute units are allowed.
- Reject unexpected response market/unit, duplicate candles, invalid OHLCV,
  timestamp misalignment, excessive response size, and API errors.
- The newest forming candle is **excluded** using the UTC observation time
  and an optional safety delay (default: 5 seconds).
- No complete candle, or the newest complete candle older than two selected
  periods, causes a refusal instead of a stale-data trade.
- Gaps between returned candles cause refusal by default. Use
  `--allow-gaps` only after understanding Upbit's trade-dependent candle
  omissions; the count is included in fixture metadata. Missing bars are
  never filled with fabricated prices.
- The output is a standard `mode: paper` fixture containing
  `live_orders_enabled: false`; it never changes the live-feed guard
  in the core trading package.
- All regression tests inject mock API responses and prohibit network access.

## Verification limits

Public network access is required for a real snapshot invocation; it was
**not** exercised in the isolated unit tests. A single request cannot
prove market continuity, gap-free streaming, order reconciliation, or
live exchange compatibility. The runner and report remain **paper-only**.
Production trading must pass separate exchange and safety release gates.

```sh
python tools/offline_checks.py
```
