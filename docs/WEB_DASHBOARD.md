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
| `--market-capture` | Validated rolling public candle capture directory | `/market-data.html`, `/api/market-capture` |
| `--paper-stream` | Continuous paper account directory with atomic account/strategy/risk/cursor state | `/api/paper-stream`; CLI/API only, no new page |
| No source flag required | Observer process probe | `/api/liveness` |
| Configured journal/replay/research/paper_session sources | Existing four-source readability probe | `/api/readiness` |

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
files. `/api/readiness` is `ready/200` only when all configured
journal/replay/research/paper_session sources pass their
existing validators; no configured sources is `not_configured/503`, any failure
is `degraded/503`. Its scope is `configured_source_readability`. Both accept
GET/HEAD without query parameters and include only the fixed version-1 local
paper observer contract, UTC observation time, and for readiness exactly the four
source statuses. They expose no account identifiers, source paths or metrics.
`/api/health` remains journal-only; it is not an aggregate readiness alias.

The v45 capture route is independent of those four readiness keys. It accepts
GET/HEAD without query parameters, returning `200` for any valid observation
(including stale/blocked/stopped) and `503` for unavailable capture data. Its
`status` describes separate candle age and successful observation age; a saved
`running` phase never asserts collector process liveness. Use the capture CLI
check for a `0` current / `1` valid noncurrent / `2` unavailable decision.
[MARKET_CAPTURE.md](MARKET_CAPTURE.md) defines the exact limits and lifecycle.
The observer never starts collection or invokes export, strategy or account work.

The v46 continuous-account route is also independent of those four readiness
keys. Start the observer with an already created account:

```bash
python apps/dashboard/server.py --paper-stream .private/btc-stream-001 --port 8765
```

GET/HEAD `http://127.0.0.1:8765/api/paper-stream` accepts no parameters and returns
`200` for a valid current or noncurrent observation, `503` for an unconfigured,
unavailable or invalid account. HEAD observes the same account and has no body.
The route exposes version-1 `source=continuous_paper_stream`, `mode=paper`,
`read_only=true`, `live_enabled=false` and
`scope=delayed_next_open_simulation`. The result includes the account, cursor,
pending signal, risk state, saved control/source progress and independent
consumer/source age measurements. Decimal values remain strings. Fixed errors
and observations omit filesystem paths and exception text.

Status can be current, catching_up, stale, waiting, stopped, blocked or clock_error.
It describes the saved observation at `observed_at`; it is not process liveness.
Inspect the preserved error as well as phase: a terminal error remains a resume
refusal regardless of the saved phase. `integrity.verification` is
`structural_head_only`. The route does not replay the full journal or repair it.

Account creation, execution, audit and resume remain explicit CLI operations:

```bash
python tools/paper_stream.py check .private/btc-stream-001
```

For a full economic prefix audit, stop the consumer and wait for exit before
`python tools/paper_stream.py audit .private/btc-stream-001`. Audit refuses an
active writer. Resume separately requires the original frozen source/configuration
and runtime fingerprint and verifies the complete archive before new work.
The 500-bar indicator window is distinct from continuity checking against up to
5,000 retained source rows. Account events are retained up to 100,000 consumed
bars and 256 MiB, with no automatic archival or reset. The
[continuous-paper guide](CONTINUOUS_PAPER.md) covers creation commands,
indicator-only bootstrap and delayed next-open model semantics.

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
normalize an order. Admission-journal WAL reads may change `-shm` coordination bytes while
leaving logical database/WAL content unchanged; read-only does not certify
byte-immutable sidecars. Stored active mode is not owner liveness; a readable account
is not reconciled with a broker or permission to resume. Actual browser visual
QA remains unverified for the current release.

Detailed contracts and cumulative workflows:

- [Admission local API](DASHBOARD_LOCAL_API.md) and [journal](ORDER_JOURNAL.md).
- [Replay reports](PAPER_REPLAY_DASHBOARD.md) and [quant research](QUANT_RESEARCH.md).
- [Paper account observer](PAPER_SESSION_OBSERVER.md), [order browser](PAPER_ORDER_BROWSER.md) and [order detail](PAPER_ORDER_DETAIL.md).
- [Historical run](DURABLE_PAPER_RUN.md), [explicit recovery](DURABLE_PAPER_RESUME.md), [preflight](DURABLE_PAPER_PREFLIGHT.md) and [completion audit](DURABLE_PAPER_AUDIT.md).
- [Continuous paper account](CONTINUOUS_PAPER.md): current-source activation, atomic progress, passive CLI/API and full-replay resume/audit.
- [Study comparison and run-then-report workflow](MARKET_PAPER_STUDY.md), [static research/input inspection](QUANT_RESEARCH.md#optional-research-input-inspection-v42), and [remaining operational evidence](../PRODUCTION_CHECKLIST.md).
