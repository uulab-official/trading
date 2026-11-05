# Existing offline runner results in the operations dashboard

The admission journal is NOT a cash ledger. This integration keeps reservations
separate and adds a second, explicit read-only view of completed results generated
by the existing `PaperRunner` and `PaperExchange`. No alternate simulator or new
live broker route was introduced.

## Run (macOS/Linux, local POSIX filesystem)

From the repository root, install the existing engine dependencies once:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
python tools/paper_replay_dashboard.py config/examples/offline_fixture.json \
  --checkpoint /tmp/uulab-replay-checkpoint.json \
  --report /tmp/uulab-replay-report.json
python apps/dashboard/server.py --replay-report /tmp/uulab-replay-report.json --port 8765
```

Open `http://127.0.0.1:8765` locally and use the **재생 결과** section. The bundled
fixture is synthetic, not a downloaded market history. An optional `--journal`
argument continues to show a separate admission journal; missing admission data
does not hide a valid replay report. Missing replay data is not an empty account.
Native Windows checkpoint execution is not supported by the existing POSIX lock
contract. This increment does not change that limitation.

The exporter supports the existing MA-cross and RSI configuration flags, including
initial balance, fees/slippage, position sizing and risk limits. It does not accept
live mode, broker credentials or a network data source. At most 10,000 local
OHLCV rows and 32 MiB of fixture data are accepted by this exporter.

## Consistency and failure behavior

- A single fixture read supplies both the engine input and the SHA-256 provenance.
- The existing runner validates/resumes its authoritative checkpoint. Its trades,
  cash and holdings are not recreated by the UI.
- Decimal report validation reconciles fills, quote cash, holdings, fee totals and
  final marked equity using the engine's precision/rounding convention. Total P&L
  includes unrealized marked holdings; it is NOT labeled realized P&L. Fees are
  already included in cash and are not deducted twice.
- Results are published with atomic no-clobber publication only after successful replay and
  validation, under a cooperating report writer lock. Another experiment requires
  a new report filename. Identical reruns retain the same report without new fills.
- Input, checkpoint, report and their lock files must be distinct, including
  symlink/hardlink aliases. Existing unrelated/corrupt reports are never overwritten.
- A publication failure may leave an advanced valid checkpoint, but not a partial
  report. Rerunning validates that checkpoint and regenerates the same result.
- The HTTP reader never opens or executes checkpoints. It validates a bounded
  version-1 read model (8 MiB / 10,000 fills maximum), returning the latest 100 fills.
  It uses the server-configured path only; query-selected files and writes are denied.
- Reports with missing, corrupt or inconsistent data return HTTP 503. The UI keeps
  the last valid result with an explicit stale/error notice, never invented zeros.

`GET /api/replay` is independent of `/api/snapshot`. Money is encoded as Decimal
text. `last_bar_ms` is the final bar's START timestamp; equity uses its closing
price. Generation and HTTP refresh times are distinct, not live market timestamps.
Amounts are not combined across KRW/USDT or with the admission journal.

## Trust and remaining boundaries

Accounting consistency and hashes do not authenticate a producer or prove the
fixture is real market data. Local files must be trusted. The read model is not
used to authorize trades. This remains local, read-only, offline observation:
no KIS/Upbit authentication, live feeds, partial-fill settlement, long-running
broker reconciliation, account control or internet hosting is enabled.

## Tests

```bash
python -m unittest discover -s tests -p test_replay_report.py -v
python -m unittest discover -s tests -p test_replay_export.py -v
python -m unittest discover -s tests -p test_replay_dashboard_api.py -v
python -m unittest discover -s tests -p test_replay_runner_integration.py -v
node --test tests/replay_client.test.mjs
```

The integration tests run the original engine with socket/DNS functions forbidden,
then exercise real loopback HTTP separately. They cover resume, damaged checkpoint,
failed report publication, KRW isolation, empty-fill runs and competing report locks.
