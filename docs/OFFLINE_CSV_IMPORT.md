# Historical OHLCV CSV → offline paper fixture

The CSV importer supports local historical research without credentials, live
market connections, account access, or automatic order placement.

The importer **does not** download prices or authenticate data. The operator
must verify the source, adjustment method, timeframe, UTC timestamp alignment,
and whether each bar is **complete** before running a strategy. Never treat
a converted dataset as exchange-verified history simply because it was parsed.

## Required CSV format

UTF-8 text, exact header:

```csv
timestamp,open,high,low,close,volume
1699920000000,100,110,90,105,1000
1699923600000,105,112,103,110,900
```

The timestamp is the **opening** Unix epoch in milliseconds (UTC). Rows must
be ascending, unique, and exactly aligned to the chosen fixed timeframe.
Prices must be positive/finite, volume finite/nonnegative and OHLC internally
consistent. The input is rejected on invalid numbers, extra/missing columns,
reordered rows, duplicate timestamps or overlaps.

**Missing bars are rejected by default.** Deliberately accept a gap with
`--allow-gaps`, which records that decision and the number of observed
gaps in the metadata. The importer never manufactures missing candles.

## Usage

```sh
python tools/import_ohlcv_csv.py history.csv \
  --symbol BTC/KRW --timeframe 1h \
  --output data/local-history.json

# If gaps are understood and intentionally accepted:
python tools/import_ohlcv_csv.py history.csv \
  --symbol BTC/KRW --timeframe 1h --allow-gaps \
  --output data/local-history-with-gaps.json

PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
python -m trading_bot.paper_runner data/local-history.json \
  --checkpoint data/local-history-checkpoint.json \
  --initial-balance 1000000

python tools/paper_report_html.py data/local-history.json \
  data/local-history-checkpoint.json \
  --output data/local-history-report.html
```

`--initial-balance 1000000` is an illustrative paper KRW amount, not
real capital or an investment recommendation. Use a **new checkpoint**
for a different file, strategy, or set of trading assumptions.
The converter emits `mode: paper` only and preserves the original
CSV unchanged. The output includes input SHA-256, gap count, row count,
source classification, and explicit limitations. A local 30 MB file cap
and 100,000-bar cap protect accidental oversized imports.

The report HTML is a read-only snapshot. It does not create orders,
run continually, or claim profitability. See
[offline paper report](OFFLINE_PAPER_REPORT.md).

For automated regression checks:

```sh
python tools/offline_checks.py
```
