# Trading operations dashboard

Run from repository root:

```bash
python apps/dashboard/server.py --journal /absolute/path/to/paper.sqlite --port 8765
```

Open http://127.0.0.1:8765/ in the same computer. The responsive Korean UI fetches the latest 100 paper order records from `GET /api/orders` and refreshes every 15 seconds while the tab is visible. When disconnected, it displays an explicit error state rather than fabricated data. Values are rendered as text, not HTML.

This is a local read-only operations view. It does **not** execute trades, display broker balances, authenticate users, or expose a remote API. Never publish this local server to the internet. Broker credentials must not be embedded in the UI. See `docs/DASHBOARD_LOCAL_API.md`.
