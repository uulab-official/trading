# Trading Bot Monorepo

암호화폐 자동매매 개발 모노레포 · 로컬 모의매매·연구·운영 상태 점검

## Development branch: audited market-history studies

`feat/smc-market-structure` extends the original offline workflow with imported
historical data, durable local paper accounts, explicit recovery/audit and fixed
MA/RSI candidate studies. [PR #1](https://github.com/uulab-official/trading/pull/1)
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
Controlled tests cover these paths; real-provider soak and continuous strategy
execution remain unverified/unimplemented respectively.

## Verified offline paper workflow

The deterministic offline paper runner is the tested entrypoint for this revision.
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

## Local paper operations and configuration truth (v44)

The supported observer now has separate liveness and configured-source
readability probes, bounded HTTP handlers/connections and signal-aware shutdown.
Use [PRODUCTION_PAPER_OPERATIONS.md](docs/PRODUCTION_PAPER_OPERATIONS.md) for exact
local commands, source/read budgets, private files, historical recovery,
quiescent-directory backup and pinned-code rollback. Start with a new synthetic
account, then keep the observer running in the foreground:

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

No actual browser visual QA, long-duration real-provider soak, continuous
strategy/account execution, broker-authoritative reconciliation, public deployment
or live trading completion is claimed. [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) records
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
workflows. In an existing environment, install the repository packages once:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
python tools/offline_checks.py
```

The targeted stdlib dashboard/research suites remain independent of the engine
package setup. Full engine/backtest checks require their existing dependencies.
See the workflow guides and CI for checks matching the component being changed.
No network/provider activation is needed for synthetic paper operation.

## License and support

[MIT License](LICENSE). This software supports research and historical paper
simulation; modeled results and consistency checks do not establish future
performance. Report issues at
[GitHub Issues](https://github.com/uulab-official/trading/issues).
