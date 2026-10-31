# Local paper operations dashboard

Read-only observation of the SQLite **order-admission journal**, not a live broker or portfolio. No credentials, web trading controls, market feed, fill accounting or invented balances.

## Run an explicitly synthetic example

From the repository root, choose a NEW output filename in an existing writable directory:

```bash
python tools/paper_operations_demo.py --journal /tmp/uulab-paper-demo.sqlite
python apps/dashboard/server.py --journal /tmp/uulab-paper-demo.sqlite --port 8765
```

Open `http://127.0.0.1:8765` on the same computer. On Windows replace `/tmp/...` with a new local path such as `%TEMP%\uulab-paper-demo.sqlite`. The demo refuses to overwrite an existing file and labels its source `synthetic_demo`. Its six orders exercise reserved, acknowledged, unknown, pending cancellation, cancelled and rejected states. The displayed reservation amounts are not account cash, investment results or simulated fills.

To observe an existing journal, omit the demo command and pass that journal to the server. The HTTP reader never migrates or writes a database. Opening an old journal through `OrderJournal` performs a transactional schema migration; back up existing files before upgrading.

## Operations

The UI shows real persisted counts, currency-separated reservations, unresolved transmission/cancel outcomes, paginated status filters and the last 50 audit records. It refreshes every 15 seconds while visible, times requests out after 5 seconds and never overlaps refreshes. A failed refresh preserves the last good snapshot with an explicit stale-data warning. All displayed times are KST; stored timestamps are UTC. Values are inserted as text, never HTML.

Endpoints: `GET /api/health`, `/api/snapshot`, `/api/orders` (compatible snapshot alias), `/api/events?client_id=...`. Snapshot parameters: `limit` (1–100), `offset` (0–1,000,000), optional exact `status`. Summary totals cover the complete journal, not just the page/filter. HTTP 503 means absent, corrupt or unsupported data, not an empty order list.

## Security and boundaries

Loopback only. Local Host and same-origin checks, no CORS permission, strict CSP, no arbitrary file routes, no write methods. These measures do not authenticate local users. Do not expose this service through a proxy, tunnel or public deployment without a separate authentication/authorization design. `live_enabled=false` means this observer cannot submit orders; it is not a claim about another runner's state.

The older JSON checkpoint `PaperRunner` and Rust/Python account dashboards remain separate. Neither is silently imported into this journal. Broker reconciliation and fills/fees/positions require explicit integration before live operation.

## Verify

```bash
python -m unittest discover -s tests -p 'test_paper_operations*.py' -v
python -m unittest discover -s tests -p 'test_dashboard_operations.py' -v
node --test tests/dashboard_client.test.mjs
```

Optional browser smoke test (Playwright + Chromium required): `python tools/check_paper_dashboard.py`. `--offline-render` uses local assets and SQLite snapshots without browser HTTP; this checks layout/client behavior only, not browser-to-server policy headers. API integration tests separately exercise real loopback HTTP.
