# Local paper operations dashboard

Read-only views of the SQLite order-admission journal and, optionally, a separate
completed offline PaperRunner report. No broker credentials, web trading controls,
live feed or invented account balances. Reservation amounts and replay accounting
are intentionally never added together.

## Admission journal

```bash
python tools/paper_operations_demo.py --journal /tmp/uulab-paper-demo.sqlite
python apps/dashboard/server.py --journal /tmp/uulab-paper-demo.sqlite --port 8765
```

The demo requires a new filename and contains explicitly synthetic reservation
states, not executions. The UI shows persisted counts, per-currency reservations,
status filtering/pagination and recent audit events. It retains the last good
snapshot with a warning when refresh fails. The HTTP reader never migrates a DB.

## Existing runner results

See [the replay integration guide](../../docs/PAPER_REPLAY_DASHBOARD.md) for setup,
limits and verification. On a local POSIX filesystem with engine dependencies:

```bash
python tools/paper_replay_dashboard.py config/examples/offline_fixture.json \
  --checkpoint /tmp/uulab-replay-checkpoint.json \
  --report /tmp/uulab-replay-report.json
python apps/dashboard/server.py --replay-report /tmp/uulab-replay-report.json --port 8765
```

Open `http://127.0.0.1:8765` on the same computer. Both `--journal` and
`--replay-report` may be provided. A missing source does not make the other source
unavailable. The bundled replay fixture is synthetic; no return proves profitability.

## Security and boundaries

Loopback only. Host/Origin checks, strict CSP, no CORS, no arbitrary file routes and
no write methods. These are not authentication. Do not expose the server through
public hosting, a proxy or a tunnel. `live_enabled=false` describes this observer,
not other processes. Raw checkpoints and request payloads are not served.

`/api/health`, `/api/snapshot`, `/api/orders`, `/api/events` describe the admission
journal. `/api/replay` describes the separately validated offline report. Dates
are displayed in KST. Generation/refresh times are not live market timestamps.

The admission journal is still not coordinated with a broker or fill-settlement
service. Existing Rust/Python account dashboards remain separate. KIS/Upbit feeds,
authentication, authoritative broker reconciliation and live controls are not enabled.
