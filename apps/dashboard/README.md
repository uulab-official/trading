# Local paper operations dashboard

A loopback-only, read-only observer for independently configured local admission
journals, completed offline replay/research reports and durable paper accounts.
It does not open a broker, execute a strategy, claim a writer lease or combine
reservation amounts with account balances. Existing Rust and legacy Python
monitoring services remain separate.

Follow [the operations runbook](../../docs/PRODUCTION_PAPER_OPERATIONS.md) for
setup, private files, budgets, probe exits, shutdown, recovery and rollback.
From the repository root, create a new synthetic historical account and observe
it (existing engine package setup required):

```bash
mkdir -p .private
chmod 700 .private
python tools/paper_operate.py --demo --resumable --directory .private/paper-001
python tools/paper_audit.py .private/paper-001
python apps/dashboard/server.py --paper-session .private/paper-001/account.sqlite --port 8765
```

In another terminal:

```bash
python tools/paper_dashboard_check.py --port 8765 --liveness
python tools/paper_dashboard_check.py --port 8765
```

Open `http://127.0.0.1:8765/paper-account.html` or `/paper-orders.html` on the same
computer. The synthetic demonstration is not real market evidence. Optional
`--journal`, `--replay-report`, `--research-report` and `--paper-session` may be
combined; an unavailable source does not hide an available independent source.

`GET/HEAD /api/liveness` checks only this observer process. `/api/readiness`
checks explicitly configured local sources through existing validators: all
available is `ready/200`, none configured is `not_configured/503`, otherwise
`degraded/503`. Neither takes parameters. Readability does not establish writer
liveness, current prices, broker reconciliation or recovery permission.
`/api/health` remains admission-journal-only. Probe CLI exit `0` means validated
alive/ready, `1` validated not_configured/degraded, `2` invalid arguments or
transport/protocol failure, `130` interrupted.

Eight handlers, backlog eight, two-second socket inactivity timeout and ten-second
absolute connection lifetime bound local HTTP use. Capacity exhaustion closes a
connection. SIGINT/SIGTERM stop the server with exits `130`/`143`; startup failures
exit `2`. Shutdown closes active sockets and bounds request-worker waiting at two
seconds. File/SQLite validation budgets and kernel/filesystem limitations are
specified in the runbook.

Host/Origin/Fetch-Site checks, CSP, no CORS, no arbitrary file routes and rejected
writes are not authentication. Do not expose this server through hosting, proxy,
tunnel or port forwarding. `live_enabled=false` describes this observer, not
other processes. Checkpoints, secrets and raw request payloads are not served.
Stored modes and generation/refresh times are not market liveness. Missing,
corrupt, busy, changed or over-budget sources fail closed; the UI may retain the
last good snapshot only with a warning.

See [the current endpoint guide](../../docs/WEB_DASHBOARD.md),
[replay integration](../../docs/PAPER_REPLAY_DASHBOARD.md),
[research](../../docs/QUANT_RESEARCH.md),
[account observer](../../docs/PAPER_SESSION_OBSERVER.md) and
[order browser](../../docs/PAPER_ORDER_BROWSER.md). No actual visual QA,
long-duration soak, live feed, broker reconciliation or public deployment is
certified by these instructions.
