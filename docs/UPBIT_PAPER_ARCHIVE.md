# Append-only Upbit paper candle archive

`tools/upbit_paper_archive.py` makes **one** public read-only Upbit
quotation request per run and maintains a cumulative, local JSON
`mode: paper` fixture. It never accesses private account endpoints,
uses exchange credentials, sends real orders, or runs as a daemon.

It is intended to support repeated scheduled one-shot captures (for
example, via a locally configured job scheduler) without silently
discarding prior prices as Upbit's most-recent-200 window advances.

## First capture

```sh
python tools/upbit_paper_archive.py \
  --market KRW-BTC --unit 60 --count 200 \
  --archive data/upbit-btc-1h-archive.json
```

## Subsequent capture

Run the **same command** later. It verifies overlap with archived
complete candles and appends only previously unseen completed bars.
A repeated identical snapshot returns `appended_bars: 0` without
rewriting the archive.

If an old completed candle differs, the provider window no longer
overlaps local history, a different market/timeframe is selected, or
the new data contains an unacknowledged gap, the operation fails
**without altering the saved archive**. Never clear or force-edit
the archive/checkpoint to bypass those safeguards. Upbit may legitimately
omit candles with no trades; `--allow-gaps` must be an explicit decision.

A local POSIX file lock prevents cooperating capture writers from
overwriting one another. The archive is atomically replaced only after
all new bars validate. Data is capped at 100,000 bars. If more history
is required, implement and test archival rotation first rather than
silently truncating it. In particular, very long offline gaps may
outlast the API's 200-bar window; they must be recovered and verified
before the archive can continue.

## Replay paper trades on the growing archive

```sh
PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
python -m trading_bot.paper_runner \
  data/upbit-btc-1h-archive.json \
  --initial-balance 1000000 \
  --checkpoint data/upbit-btc-1h-paper.json

python tools/paper_report_html.py \
  data/upbit-btc-1h-archive.json \
  data/upbit-btc-1h-paper.json \
  --output data/upbit-btc-1h-report.html
```

Subsequent replays use the **same checkpoint** and will verify its
processed prefix before applying appended bars. The broker emulator
remains offline and spot-only. The capture and replay commands are
separate; after a successful capture, run replay explicitly. This
separation avoids automatically promoting a data source to live
orders. The archive tool does **not** guarantee continuous monitoring
or crash-atomic commit across both the archive and replay state:
the runner's independent checkpoint validation handles safe replay
after partial progress.

Run tests:

```sh
python tools/offline_checks.py
```

Public API contract:
https://docs.upbit.com/kr/reference/list-candles-minutes

No observed returns from this workflow establish a positive expectancy
or the correctness of real-world exchange fills.
