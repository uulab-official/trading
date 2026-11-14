# Current local dashboard and source contracts

The supported web entrypoint is `apps/dashboard/server.py`, a read-only local
observer at `http://127.0.0.1:8765`. Follow the
[operations runbook](PRODUCTION_PAPER_OPERATIONS.md) for exact commands,
private-file handling, probes, resource limits, lifecycle and recovery.
The [dashboard README](../apps/dashboard/README.md) has a short synthetic example.

This replaces the obsolete Compose/WebSocket/port-forwarding tutorial. The
current Compose file starts the separate Rust server; it does not connect the
Python runner's JSON checkpoint to this observer. Rust `/api/positions` and
`/api/orders` and the legacy `packages/monitor/web_dashboard.py` use separate
contracts. Their running/connected display is not paper execution evidence.
Do not copy files or rename checkpoints to pretend those contracts agree.

## Select local sources explicitly

| CLI flag | Saved source | Views / GET or HEAD endpoints |
|---|---|---|
| `--journal` | SQLite admission journal; reservations, not a cash ledger | `/`, `/api/health`, `/api/snapshot`, `/api/orders`, `/api/events` |
| `--replay-report` | Validated completed PaperRunner report, never its checkpoint | Replay section at `/`, `/api/replay` |
| `--research-report` | Validated saved walk-forward research report | `/research.html`, `/api/research` |
| `--paper-session` | Durable paper-account SQLite observation | `/paper-account.html`, `/paper-orders.html`, `/api/paper-session`, `/api/paper-session/orders`, `/api/paper-session/order` |
| No source flag required | Observer process probe | `/api/liveness` |
| All explicitly configured sources | Source readability probe | `/api/readiness` |

Flags may be combined; missing or invalid sources produce their own unavailable
response without concealing an independent valid source. Unconfigured probes
list sources as `not_configured`; configured unreadable sources are
`unavailable`. Source routes retain their existing validated schemas and return
`503` for unavailable data, not fabricated empty accounts. Account page/detail
preconditions retain their existing `409` changed-observation and `404` missing
order semantics. The browser can retain its previous good observation only with
an explicit warning. Dates displayed in KST and refresh/generation timestamps do
not imply current market prices. Currencies and reservations remain separate.

## Probe and runtime meaning

`/api/liveness` is `alive/200` with `scope=observer_process` and never reads source
files. `/api/readiness` is `ready/200` only when all configured sources pass their
existing validators; no configured sources is `not_configured/503`, any failure
is `degraded/503`. Its scope is `configured_source_readability`. Both accept
GET/HEAD without query parameters and include only the fixed version-1 local
paper observer contract, UTC observation time, and for readiness exactly the four
source statuses. They expose no account identifiers, source paths or metrics.
`/api/health` remains journal-only; it is not an aggregate readiness alias.

Use `python tools/paper_dashboard_check.py --port 8765` for strict direct-loopback
readiness validation, adding `--liveness` for process response. Exit `0` means
valid ready/alive, `1` valid degraded/not_configured, `2` invalid arguments,
transport/protocol/unexpected response, `130` interruption. No URL, host, proxy or
redirect options exist. See the runbook for timeout and byte bounds.

The server bounds concurrent handlers to eight, backlog to eight, socket
inactivity to two seconds and absolute connection lifetime to ten seconds.
Aggregate headers are capped at 16 KiB and request lines at 4,096 bytes.
SIGINT/SIGTERM close active sockets and wait at most two seconds for request
workers, with process exits `130`/`143`. Startup/configuration failure exits `2`.
Journal/report/account read budgets are source-specific and documented in the
runbook; finite application work cannot impose a hard deadline on a hung kernel
or filesystem call. These limits are not public-service or soak certification.

## Security and evidence boundaries

Local Host/Origin/Fetch-Site checks, CSP, no-store responses and write rejection
remain in force for all routes, including probes. They are not authentication.
Do not host publicly, use a proxy/tunnel, forward ports or broaden file
permissions. There are no web execution, recovery, mode-change or broker controls.
Read-only observation does not claim an owner lease, repair a database or
normalize an order. SQLite WAL reads may change `-shm` coordination bytes while
leaving logical database/WAL content unchanged; read-only does not certify
byte-immutable sidecars. Stored active mode is not owner liveness; a readable account
is not reconciled with a broker or permission to resume. Actual browser visual
QA remains unverified for the current release.

Detailed contracts and cumulative workflows:

- [Admission local API](DASHBOARD_LOCAL_API.md) and [journal](ORDER_JOURNAL.md).
- [Replay reports](PAPER_REPLAY_DASHBOARD.md) and [quant research](QUANT_RESEARCH.md).
- [Paper account observer](PAPER_SESSION_OBSERVER.md), [order browser](PAPER_ORDER_BROWSER.md) and [order detail](PAPER_ORDER_DETAIL.md).
- [Historical run](DURABLE_PAPER_RUN.md), [explicit recovery](DURABLE_PAPER_RESUME.md), [preflight](DURABLE_PAPER_PREFLIGHT.md) and [completion audit](DURABLE_PAPER_AUDIT.md).
- [Study comparison and run-then-report workflow](MARKET_PAPER_STUDY.md), [static research/input inspection](QUANT_RESEARCH.md#optional-research-input-inspection-v42), and [remaining operational evidence](../PRODUCTION_CHECKLIST.md).
