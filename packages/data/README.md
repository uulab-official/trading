# trading-data: offline only

This package restores the local candle and indicator imports used by the
repository's strategies and offline paper replay. It requires only the Python
standard library at runtime. It does not read credentials, fetch data, open
sockets, connect brokers, launch the trading bot, or execute live trades.

`BinanceWebSocket` is an explicit fail-closed compatibility stub. Construction
and `connect()` raise `LiveMarketDataUnavailable`. Its presence does **not** mean
that the repository has a working live market-data adapter.

## Candle and buffer contracts

- `Candle(timestamp, open, high, low, close, volume)` is immutable
- Timestamps must be nonnegative integers in Unix milliseconds, excluding bool
- OHLC prices are finite positive Decimals, with `low <= open/close <= high`
- Volume is finite and nonnegative; numeric inputs normalize through decimal
  text rather than binary floating-point conversion
- `CandleBuffer(max_size=500, timeframe="1h")` stores completed bars in strictly
  increasing timestamp order. It accepts a Candle or a six-element OHLCV list
- Read-only `duration_ms` exposes the validated fixed timeframe duration for
  completed-bar interval and causality checks by callers
- Exact duplicates still retained in the buffer are idempotent no-ops;
  conflicting timestamps, unseen out-of-order bars, and evicted replays fail
  before mutation. `add_candle` returns True for insertion and False for a replay
- `current` is the latest **completed** bar, or None. `count` and all history
  methods exclude pending tick bars. Returned lists are defensive copies
- `clear()` resets both completed and pending data

`add_tick(timestamp, price, volume=Decimal("0"))` aggregates only ticks supplied
by the caller. Fixed UTC/Unix-epoch-aligned timeframes accept positive integer
durations in `ms`, `s`, `m`, `h`, `d`, or `w`; calendar months are unsupported.
Tick volume is incremental. An identical latest tick replay is ignored;
conflicting same-timestamp or late ticks fail. Already completed buckets cannot
be reopened. Empty intervals are not fabricated.

When a tick crosses into the next bucket, `add_tick` returns the prior completed
Candle without inserting it. Call `add_candle(completed)` explicitly, as the
tick-fed compatibility interface requires. The last pending candle remains uncommitted unless
a subsequent supplied tick completes it; completed-bar replay should use
`add_candle` directly. Do not mix a supplied completed bar with further ticks
for that same bucket.

## Indicator contracts

All periods are positive integers, excluding bool. Every calculation is stateless
and uses a fixed 28-digit, half-even Decimal context, independent of the caller's
Decimal precision. Histories are ordered iterables of finite positive prices;
scalar text, mappings, and unordered sets are rejected. Invalid or insufficient
inputs raise ValueError; no values are silently substituted.

- `SMA(period).calculate(prices)`: latest-period arithmetic mean; at least period
  prices required
- `EMA(period).calculate(prices)`: seed with first-period SMA, then smooth every
  later price using alpha = 2 / (period + 1); at least period prices required
- `RSI(period, oversold=30, overbought=70).calculate(prices)`: Wilder seed from
  period price differences, followed by Wilder recursive averaging; at least
  period + 1 prices required. Flat history returns 50, all-gain history 100,
  and all-loss history 0. Thresholds satisfy 0 <= oversold < overbought <= 100
- `ATR(period).calculate(highs, lows, closes)`: equal-length valid HLC histories,
  first true range high minus low, gap-aware subsequent true ranges, first-period
  arithmetic seed followed by Wilder smoothing; at least period bars required

Compatibility limit: the original `RSIStrategy.analyze` only checks period prices,
one short of a full Wilder seed. A caller must enforce period + 1 warmup; the
default RSI(14) is covered by the offline runner's 50-bar warmup. Nondefault RSI
or divergence configurations need the same validation. Strategy timestamps and
strategy-specific behavior remain the caller's responsibility.

## Safe test command (no installation required)

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=packages/data python -m unittest discover -s packages/data/tests -v
```

Only this package is imported. No live application is launched.
