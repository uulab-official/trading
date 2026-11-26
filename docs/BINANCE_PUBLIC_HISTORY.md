# Binance public history archive and recovery

This is a separate, credential-free local spot market-data collector. It is not the
Rust WebSocket worker, a broker, a paper fill engine, or a live trading service.
Only the fixed production public market-data host is used, with GET /api/v3/time
and GET /api/v3/klines. No testnet data is mixed with production data.

## Run locally

Choose an aligned UTC starting timestamp and a new private local directory.
The sample timestamp below is historical; choose a recent start for small
initial catch-up runs. Do not start an automated loop before inspecting a
one-shot result.

```sh
mkdir -p data/binance-history
python tools/binance_public_archive.py init data/binance-history/btc.sqlite \
  --symbol BTCUSDT --interval 1h --start-ms 1764547200000 --max-failures 3
python tools/binance_public_archive.py sync data/binance-history/btc.sqlite --pages 3
python tools/binance_public_archive.py status data/binance-history/btc.sqlite
# Explicit opt-in local polling:
python tools/binance_public_archive.py sync data/binance-history/btc.sqlite --loop --poll-seconds 60
```

The collector reads exchange server time, checks bounded local-clock skew and
selects bars that closed before a two-second grace. It accepts fixed UTC
intervals from 1m through 1d; calendar/variable intervals are not modeled.
Each response must contain exactly the requested contiguous opens and closes.
OHLCV decimal strings are validated and canonicalized without float conversion.
No missing, forming, reordered or revised overlap bar is silently substituted.
A cycle validates a bounded prefix (up to ten pages, 1,000 rows per page)
before appending it in a SQLite transaction. Large gaps are recovered across
cycles; catching_up is not a current feed. The last committed bar is requested
again and must match before extending the archive. This is REST catch-up, not
WebSocket reconnection. Older historical revisions outside the checked overlap
cannot be detected by this query pattern.

The metadata pins the venue, symbol, timeframe, initial timestamp and failure
budget. All bars, sequence continuity and the SHA-256 head are replay-checked
before reads/writes. Hashes detect internal inconsistency, not an adversary
who can replace the entire DB/head. Keep trusted heads independently.
WAL/FULL is used on a trusted local filesystem. The writer transaction is
held across bounded requests; the CLI also holds a POSIX owner lock across
polling/sleep. Do not alias DB files, WAL files, lock files or STOP paths.
The development archive is bounded to 100,000 bars and audits full history
each cycle; it is not an unbounded high-frequency journal.

HTTP 5xx/transport failures consume the durable consecutive failure budget;
successful cycles reset it. 418/429 and other client/protocol/integrity errors
latch halted immediately (no automatic rate-limit retries). Polling backs off
on transient failures and stops on halt. Create <archive>.STOP to stop at the
next cycle or page boundary; the latch persists after marker removal. A hard
stop may interrupt a transaction. Status is read-only and returns nonzero
when capture is stale, halted, catching up, or initializing. It does not claim
a live trade or a healthy broker account.

## Recovery, backup and replay input

```sh
python tools/binance_public_archive.py backup data/binance-history/btc.sqlite \
  data/binance-history/btc-backup.sqlite
python tools/binance_public_archive.py status data/binance-history/btc-backup.sqlite \
  --expected-head TRUSTED_SHA256
python tools/binance_public_archive.py export data/binance-history/btc.sqlite --count 240
```

Native SQLite backup is verified against the source read transaction and never
overwrites a file. Preserve the emitted head in a separate trusted location.
A verified backup is itself a valid archive; do not run two active copies of
the same logical archive. Stop the collector before replacing any operational
reference. Failed backup candidates are not automatically promoted or deleted.
Independent retention, scheduled publication, external checkpoint service and
disaster-recovery drills remain operator work.

After diagnosing an intentional halt (including honoring Retry-After for rate
limits), stop the process, preserve evidence, remove the STOP marker only if
appropriate, and explicitly resume with the trusted head:

```sh
python tools/binance_public_archive.py resume data/binance-history/btc.sqlite --expected-head TRUSTED_SHA256
```

Export can replace the bars field in a version-1 Rust replay scenario template
using --template. The base/quote pair and timeframe must match. Provide real
market unit rules independently; the collector does not discover tick sizes
or fit parameters. The Rust replay CLI is developed separately in PR #6.
Use a validated, unchanged research template and a fresh replay output directory.
Exported prices and volume remain decimal strings. The raw export includes
archive identity and head; the Rust scenario schema itself is unchanged.

## Validation and limits

Run `python -m unittest discover -s apps/server/tests -p test_binance_public_archive.py`.
Tests use injected public responses, not actual Binance requests. The standard
offline safety suite includes this module. No private account permissions,
real order execution, long-running deployment, or profitability is validated.
Official protocol: https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market .
