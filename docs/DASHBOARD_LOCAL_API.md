# Local read-only paper dashboard

Run `python apps/dashboard/server.py --journal /path/to/paper.sqlite --port 8765` then visit `http://127.0.0.1:8765`. Endpoints: `GET /api/health` and `GET /api/orders`. No write endpoints. API binds to 127.0.0.1 only and reads SQLite in read-only mode.

The current HTML is a static skeleton and does not yet fetch these endpoints. The API does not expose balances, fills or performance because those data are not recorded by the journal. Do not expose this service publicly; loopback binding is not authentication. Real user accounts, authorization and TLS are required for any remote deployment.
