# Trading Bot Monorepo

암호화폐 자동매매 개발 모노레포 · 로컬 모의매매·연구·운영 상태 점검

## Development branch: continuous paper and audited research

`feat/smc-market-structure` supports continuous local paper consumption from a
public closed-candle capture, alongside imported historical data, durable
historical accounts, explicit recovery/audit and fixed MA/RSI candidate studies.
The v46 consumer keeps one account across polls and verified restarts; its
operator guide is [CONTINUOUS_PAPER.md](docs/CONTINUOUS_PAPER.md).
v47 adds [verified PAPER backup and recovery bundles](docs/PAPER_BACKUP.md),
including a network-forbidden local drill; recovered bundles remain non-executing.
v48 adds [guarded Linux PAPER supervision](docs/PAPER_SERVICE.md) for the existing
capture/account pair, fresh process-progress checks, retained attempt evidence
and an inactive systemd user-unit generator. Explicit startup preserves the
original account/source bindings; there is no automatic worker restart.
v49 adds a [verified minimal PAPER source release](docs/PAPER_RELEASE.md):
a deterministic archive with an externally pinned SHA-256, strict validation
and extraction into a new private runtime directory.
[PR #1](https://github.com/uulab-official/trading/pull/1)
remains Draft. The latest continuation state is in
[CURRENT_HANDOFF.md](docs/CURRENT_HANDOFF.md).

To run an explicitly selected local source and candidate plan, then export a fresh
comparison as a private local HTML file:

```bash
python tools/paper_study.py run .private/btc-hourly-001.json \
  --plan config/examples/market-study-plan.json \
  --directory .private/study-001 \
  --html .private/study-comparison-001.html
```

The source must already use the imported-market fixture format. Both the study
directory and HTML filename must be new; their parents must already exist, and
the HTML parent must be owned by you with permissions `0700`. HTML stays outside
the study. This command
executes the original paper study once, inspects it afresh and exports that
inspection. Its JSON keeps the original execution and later inspection separate;
the outer status describes the inspection. Plain `run` remains unchanged.

To inspect an existing study and export another comparison without executing it:

```bash
python tools/paper_study.py check .private/study-001 \
  --html .private/study-comparison-002.html
```

The report retains every planned candidate and shows original audited metrics
and incomplete/blocked states. It is a static historical simulation snapshot.
After any execution or reporting failure, preserve the study and any HTML, then
use this explicit `check` command with a new report filename; execution is never
retried automatically. See [MARKET_PAPER_STUDY.md](docs/MARKET_PAPER_STUDY.md)
for the workflow JSON, assumptions, output permissions and exit codes.

Saved walk-forward research has a separate read-only snapshot command:

```bash
python tools/paper_research_report.py .private/research-001.json \
  --html .private/research-001.html
```

It checks an existing research JSON and displays all training/test windows and
available cost diagnostics without running research again or starting a server.
Add `--fixture config/examples/offline_fixture.json` to compare an explicitly
supplied local file with the report's recorded raw-byte SHA-256. The comparison
has a separate status: a match exits `0`, a mismatch exits `1` and can still
produce diagnostic HTML. Without the option, the source file is not opened.
Add `--validate-fixture` with `--fixture` to apply the original research-input
rules under the report's candidates/settings/schema and compare its market,
timeframe, bar count and included first/last timestamps. This uses the same bytes
as the hash check and reports data acceptance separately; invalid data exits `1`
with optional diagnostic HTML. See the [input-inspection guide](docs/QUANT_RESEARCH.md#optional-research-input-inspection-v42).
This is **saved-report consistency checking**; it does not authenticate source
data or engine execution and has no market-study completion receipt. See
[QUANT_RESEARCH.md](docs/QUANT_RESEARCH.md#static-research-snapshot-v40) for the
complete synthetic example, output permissions and evidence boundaries.

## Continuous public candle capture (v45)

The explicit foreground collector records closed Upbit KRW minute candles in a
private transactional rolling window. It verifies overlap, rejects gaps and
revised history, preserves retry/cooldown state across restart, and exposes data
age separately from the age of the last successful observation. No account or
strategy is automatically attached to this collector.

```bash
mkdir -p .private
chmod 700 .private
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/market-btc-001
```

In another terminal, inspect `python tools/market_capture.py check
.private/market-btc-001` or start `python apps/dashboard/server.py
--market-capture .private/market-btc-001 --port 8765` and open
`http://127.0.0.1:8765/market-data.html`. The check exits `0` only for a valid
current data observation, `1` for a valid noncurrent observation and `2` for
invalid/unavailable data. Saved phase never proves collector process liveness.
The existing four-source dashboard readiness remains unchanged.

To hand an explicit historical snapshot to the original paper workflow:

```bash
python tools/market_capture.py export .private/market-btc-001 \
  --output .private/market-snapshot-001.json
python tools/paper_market.py inspect .private/market-snapshot-001.json
python tools/paper_market.py run .private/market-snapshot-001.json \
  --directory .private/market-paper-001
```

Export and run destinations must be new. A window retains at most 5,000 candles;
evictions are counted and visible, so it is not a complete archive. Stop with
SIGINT/SIGTERM and preserve all artifacts on errors. Explicit `resume` reuses
frozen configuration and refuses uncertain in-flight or terminally blocked
states. See [MARKET_CAPTURE.md](docs/MARKET_CAPTURE.md) for exact limits,
restart/cooldown decisions, JSON/exit contracts and evidence boundaries.
Controlled tests cover these paths; real-provider soak remains unverified.
The separate v46 consumer below explicitly attaches a new paper account to a
current capture.

## Continuous paper consumption (v46)

Keep the capture above running, and first confirm that its check returns
`status=current` with exit `0`. Then start a separate consumer in the foreground:

```bash
python tools/paper_stream.py run \
  --capture .private/market-btc-001 \
  --directory .private/stream-btc-001 \
  --strategy ma_cross --fast-period 3 --slow-period 5 \
  --initial-balance 1000000 --poll-seconds 5
```

The new account directory must be outside the capture. Activation warms the
original MA/RSI strategy with the latest 50–500 eligible contiguous bars and
discards every bootstrap signal. Initial holding, fills and fees are zero, with
no pending signal. Each new closed bar atomically saves the account,
strategy/risk state, indicator window of at most 500 bars, cursor and optional
fill in a private SQLite account.

This is delayed next-open simulation: a following bar's opening price becomes
available after that bar closes. Simulated event time differs from the later
observation/commit time; no broker execution or timely quote is implied.
The source path, physical identity, configuration and runtime fingerprint are
frozen. New work requires a current source and consistent overlap across the
available capture window, up to 5,000 rows, against the account archive.

Observe without advancing the account:

```bash
python tools/paper_stream.py check .private/stream-btc-001
python apps/dashboard/server.py --paper-stream .private/stream-btc-001 --port 8765
```

The local GET/HEAD endpoint is `http://127.0.0.1:8765/api/paper-stream`; no new
browser page or execution control is added. The four readiness keys remain
unchanged. Check exit `0` means a current observation, `1` a valid noncurrent
observation and `2` unavailable; none establishes process liveness or a full audit.

Stop the consumer with SIGINT/SIGTERM and wait for exit before auditing:

```bash
python tools/paper_stream.py audit .private/stream-btc-001
```

Audit refuses an active writer. After reviewing a `verified_prefix` result and
source readiness, resume explicitly with the same source/configuration/runtime:

```bash
python tools/paper_stream.py resume .private/stream-btc-001
```

Resume performs full archived replay before consuming a new bar, then rechecks
the source. Terminal errors remain refused regardless of the saved phase.
All financial events are retained up to 100,000 consumed bars and a 256 MiB
database. Capacity is a preserved terminal result; there is no automatic archive
or account reset. See [CONTINUOUS_PAPER.md](docs/CONTINUOUS_PAPER.md) for exact
bootstrap requirements, frozen options, recovery decisions and JSON/exit codes.

## Verified minimal PAPER source release (v49)

[PAPER_RELEASE.md](docs/PAPER_RELEASE.md) describes `tools/paper_release.py`
`build`, `check`, `extract` and `verify`. Build records the archive SHA-256;
the other operations require that digest from a separately trusted record.
The canonical uncompressed USTAR contains exactly 55 fixed payload files
(source, tools, license and guides) plus `paper-release.json`. Validation checks
the exact profile; extracted-tree verification rejects missing or extra files.
Build and extraction require new outputs, with private files and directories.

The supported seven tool paths are `market_capture.py`, `paper_stream.py`,
`paper_service.py`, the guarded internal `paper_service_worker.py`,
`paper_backup.py`, `paper_backup_drill.py` and `paper_release.py` under `tools/`.
They use the Python standard library on CPython 3.10+ and Linux; the interpreter,
standard library and operating system remain external. This dependency scope
applies to the release commands; other monorepo components retain their dependencies.
The legacy Rust Docker image does not package this Python PAPER runtime.

The archive includes no trading data or secrets. Extraction neither activates a
service nor changes account/capture paths or existing service runtime/interpreter
bindings. A different runtime location requires an explicitly new service
configuration. Digest checks do not establish signed provenance or data/live
readiness. Python 3.10/3.12/3.13 release CI is configured; inspect the published
head's actual results before treating it as passed.

## Supervise an existing PAPER pair (v48)

After stopping the consumer and its exact bound capture and waiting for both
owners to exit, configure a new private service directory with the same Python
environment and unchanged execution modules:

```bash
python tools/paper_service.py configure .private/stream-btc-001 \
  --directory .private/service-btc-001
python tools/paper_service.py run .private/service-btc-001
```

The run remains in the foreground. In another terminal,
`python tools/paper_service.py check .private/service-btc-001` challenges the
current supervisor over its private Unix socket. Exit `0` establishes recent
native owner-loop progress from both direct workers; data freshness and account
audit remain the separate native checks. SIGINT/SIGTERM stops the consumer first,
then the capture, and returns `130`/`143` after confirmed cooperative cleanup.
Failure, missed progress or unexpected child exit ends the pair without respawn.

Linux parent-death guards contain child lifetime if the supervisor dies. Each
attempt retains bounded private diagnostics and actual child outcomes. Incomplete
cleanup evidence blocks another attempt in that service directory. Optional
`unit SERVICE --output NEW_FILE` generates an inactive systemd user unit with
`Restart=no`; it does not install or activate anything. See
[PAPER_SERVICE.md](docs/PAPER_SERVICE.md) for operating limits, exact exit codes,
manual manager workflow and the 64-attempt bound.

## Verified PAPER backups and recovery checks (v47)

Stop the continuous consumer, then its capture, and confirm both processes have
exited. Create a paired backup in a new private destination, check its complete
archived execution, then verify a separately copied recovery bundle:

```bash
python tools/paper_backup.py create .private/stream-btc-001 \
  --output .private/backup-btc-001
python tools/paper_backup.py check .private/backup-btc-001
python tools/paper_backup.py verify-restore .private/backup-btc-001 \
  --output .private/recovery-btc-001
```

Both outputs contain private account/capture `.bin` payloads and verification
metadata, with `0700` directories and `0600` files. The original code/Python
fingerprint, source binding, economic state and terminal refusals remain intact.
Verification replays the complete archive; it does not authorize execution,
rebind a copied capture, prove this is the latest state, or authorize rollback.
No executable `stream.sqlite3` or `capture.sqlite3` layout is produced. Preserve
inputs and possible incomplete outputs after any error or interruption.

To exercise the workflow with its own synthetic capture/account and network
access forbidden:

```bash
python tools/paper_backup_drill.py --directory .private/backup-drill-001
```

Use a new drill destination. The drill creates local evidence and never accepts
an existing trading account. It does not certify real backup media, an executable
host-disaster restore, service deployment or live execution. See
[PAPER_BACKUP.md](docs/PAPER_BACKUP.md) for quiescence, bounds, preservation,
exit codes and the non-executing recovery contract.

## Verified offline paper workflow

For finite fixture-based experiments, use the deterministic offline paper runner.
Start with the [personal offline trial guide](PERSONAL_TRADING_GUIDE.md) for exact
commands, expected output, and duplicate-free resume. It uses supplied synthetic
candles, not an ongoing market-connected paper service. The local observer reads
an explicit completed replay report or durable paper
account, not this runner’s JSON checkpoint. Legacy Rust/Python dashboards remain
separate.
See [OFFLINE_PAPER_RUNNER.md](docs/OFFLINE_PAPER_RUNNER.md) for local fixture replay,
simulated fees/slippage, marked holdings, risk limits, and validated checkpoint
resume. Run `python tools/offline_checks.py` for the targeted offline checks.

The existing backtest engine now has separate
[offline accounting regressions](docs/OFFLINE_BACKTEST_ACCOUNTING.md). Run
`python tools/offline_checks.py --include-backtest` with pandas/numpy available
to check causal fills, spot balances, fees, marked equity, and repeatable runs.

Run `python tools/offline_trade_report.py` for a network-forbidden report of the
local synthetic fixture using [fee-inclusive FIFO trade reporting](docs/OFFLINE_REALIZED_REPORTING.md).
It also includes [elapsed-time CAGR](docs/OFFLINE_ANNUALIZATION.md) with period,
units, undefined-value handling, and an explicit extrapolation warning.
Checkpointed paper replay now protects saved progress with one-writer ownership
and tested interruption/retry behavior on local POSIX filesystems.
The installed `trading-paper` command and module entrypoint share successful JSON
output and documented failure/recovery exit codes; Python callers keep the result
dictionary API.

The legacy live/Rust deployment and backtest references linked below
are not evidence of production readiness. The legacy live-feed adapter remains
unavailable; the separate explicit public candle collector above does not enable
that adapter or a broker. No real trading was validated.

## Local paper operations and configuration truth (v48)

The supported observer now has separate liveness and configured-source
readability probes, bounded HTTP handlers/connections and signal-aware shutdown.
Use [PRODUCTION_PAPER_OPERATIONS.md](docs/PRODUCTION_PAPER_OPERATIONS.md) for exact
local commands, source/read budgets, private files, continuous and historical
recovery, verified continuous-account backup bundles and pinned-code rollback.
Start with a new synthetic account, then keep the observer running in the
foreground:

```bash
mkdir -p .private
chmod 700 .private
python tools/paper_operate.py --demo --resumable --directory .private/paper-001
python tools/paper_audit.py .private/paper-001
python apps/dashboard/server.py --paper-session .private/paper-001/account.sqlite --port 8765
```

In another terminal, run `python tools/paper_dashboard_check.py --port 8765`.
Add `--liveness` to check only the observer process. Exit `0` means valid
ready/alive; `1` valid degraded/not_configured; `2` arguments/transport/protocol
failure; `130` interruption. Ready means saved configured sources are readable,
not writer liveness, market freshness, reconciliation or permission to resume.
Open `http://127.0.0.1:8765/paper-account.html` on the same computer. Keep the
server local; Host/Origin controls are not authentication and it is not a public
web service. See [WEB_DASHBOARD.md](docs/WEB_DASHBOARD.md) for current sources and
endpoints; the old Compose/WebSocket dashboard tutorial is superseded.

Legacy configuration CLI `validate` and `show` separate complete live settings
from operational readiness. `is_live_configuration_complete()` retains the
configuration-only test; `is_production_ready()` always returns `False` in this
revision. Successful configuration validation keeps its existing exit behavior
and cannot certify feed, broker or runtime readiness. The paper workflow needs
no credentials or outbound notification test.

Report readers reject unstable/special files and keep existing validators.
Publication preserves private/no-clobber/equal-report idempotence. If publication
may have occurred but final confirmation fails, preserve files and inspect them;
do not automatically retry execution or repair evidence. The replay and research
guides document the same explicit preservation and inspection procedure.

Continuous paper consumption and paired backup/recovery verification have
controlled local regression coverage. Actual browser visual QA, long-duration
real-provider soak, real backup-media recovery, executable host-disaster migration,
broker-authoritative reconciliation, service deployment and live trading
completion remain unverified.
[PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) records
the paper operating checks and remaining evidence for any future live service.

## Project structure and documentation

The Python engine/configuration lives in `packages/core`, market-data helpers in
`packages/data`, strategies in `packages/strategy`, the paper CLI in `apps/server`
and the local observer in `apps/dashboard`. The separate Rust server and legacy
infrastructure remain in the repository; their presence is not deployment
verification. Runtime data, accounts, reports and secrets must remain private.

| Guide | Scope |
|---|---|
| [PERSONAL_TRADING_GUIDE.md](PERSONAL_TRADING_GUIDE.md) | Synthetic offline trial and duplicate-free checkpoint resume |
| [MARKET_CAPTURE.md](docs/MARKET_CAPTURE.md) | Public closed-candle capture, freshness, bounded retention and explicit export |
| [CONTINUOUS_PAPER.md](docs/CONTINUOUS_PAPER.md) | Continuous paper account, indicator-only activation, atomic progress and full-replay resume/audit |
| [PAPER_RELEASE.md](docs/PAPER_RELEASE.md) | Fixed source archive, external digest checks, private extraction and exact-tree verification |
| [PAPER_BACKUP.md](docs/PAPER_BACKUP.md) | Verified paired backups, read-only full replay, non-executing recovery copies and local drills |
| [PRODUCTION_PAPER_OPERATIONS.md](docs/PRODUCTION_PAPER_OPERATIONS.md) | Current local paper runbook, probes, shutdown, recovery, backup and rollback |
| [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) | Paper operating checklist and remaining live-readiness evidence |
| [WEB_DASHBOARD.md](docs/WEB_DASHBOARD.md) | Current local source/endpoint contracts and legacy-service boundary |
| [PAPER_REPLAY_DASHBOARD.md](docs/PAPER_REPLAY_DASHBOARD.md) | Original runner reports kept separate from admission reservations |
| [PAPER_SESSION_OBSERVER.md](docs/PAPER_SESSION_OBSERVER.md) | Read-only durable paper-account observation |
| [DURABLE_PAPER_RUN.md](docs/DURABLE_PAPER_RUN.md) | Original historical strategies into a new private account |
| [DURABLE_PAPER_RESUME.md](docs/DURABLE_PAPER_RESUME.md) | Explicit opted-in historical recovery; no automatic restart |
| [DURABLE_PAPER_PREFLIGHT.md](docs/DURABLE_PAPER_PREFLIGHT.md) / [DURABLE_PAPER_AUDIT.md](docs/DURABLE_PAPER_AUDIT.md) | Read-only incomplete-prefix / completed-run consistency checking |
| [MARKET_PAPER_STUDY.md](docs/MARKET_PAPER_STUDY.md) | Candidate comparisons and v43 one-command run-then-report workflow |
| [QUANT_RESEARCH.md](docs/QUANT_RESEARCH.md) | Walk-forward research, static reports and v42 explicit input inspection |
| [CURRENT_HANDOFF.md](docs/CURRENT_HANDOFF.md) | Cumulative development state and outstanding evidence |

Legacy [customer setup](CUSTOMER_SETUP.md),
[zero-downtime deployment](docs/ZERO_DOWNTIME_DEPLOYMENT.md),
[Telegram](docs/TELEGRAM_SETUP.md), [monitoring](docs/MONITORING_SETUP.md) and
[project summary](PROJECT_SUMMARY.md) remain historical/reference material,
not steps required by this paper runbook or proof of a working live deployment.

## Local development

Use Python 3.10 or newer and a local POSIX filesystem for checkpoint/owner-lock
workflows. For full repository development, install the repository packages
in an existing environment:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
python tools/offline_checks.py
```

The seven-command [PAPER source release](docs/PAPER_RELEASE.md) and targeted
stdlib dashboard/research suites remain independent of the engine package setup.
Full engine/backtest checks require their existing dependencies.
See the workflow guides and CI for checks matching the component being changed.
No network/provider activation is needed for synthetic paper operation.

## License and support

[MIT License](LICENSE). This software supports research and historical/continuous
paper simulation; modeled results and consistency checks do not establish future
performance. Report issues at
[GitHub Issues](https://github.com/uulab-official/trading/issues).
