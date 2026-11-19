# Paper Supervisor — operator runbook (public data, NO real orders)

`tools/paper_supervisor.py` adds opt-in supervised polling around the
existing `upbit_paper_cycle.py` one-shot public-data + checkpointed-paper
workflow. It is **not** a real-money trading daemon. It has no authentication
or private order connection, and it cannot set `mode=live`.

## Prerequisites

- Linux/macOS local filesystem supporting POSIX locks. Do not put archive
  or checkpoint files on NFS/cloud-synced/shared network storage.
- Python 3.10+ and the documented offline package requirements.
- Consistent system clock (NTP) and stable network for public quotation.
- Writable local private `data/` directory.
- All prior tests passed; public REST fetch behavior still requires real
  exchange-endpoint verification, which synthetic unit tests cannot prove.

## 1. One supervised cycle

```sh
python tools/paper_supervisor.py \
  --mode paper --once --market KRW-BTC --unit 60 --count 200 \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --heartbeat data/upbit-btc-1h-health.json \
  --initial-balance 1000000
```

`--once` is the default. This reads *public* market candles, replays them
locally using simulated fills, and exits. The heartbeat is written as an
atomic JSON snapshot with `mode: paper` and
`live_orders_enabled: false`.

## 2. Opt-in continuous paper sampling

```sh
python tools/paper_supervisor.py \
  --mode paper --loop --market KRW-BTC --unit 60 \
  --interval-seconds 3600 --max-failures 3 \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --heartbeat data/upbit-btc-1h-health.json
```

The polling interval cannot be less than the candle duration, avoiding
a flood of quotation requests. Network/API issues are **not** retried
within a single cycle. Every future attempt starts a fresh public-read
and independently verifies the previous checkpoint before replay.
After the configured number of consecutive failed cycles, the process
exits with code `2` and heartbeat `halted`. An operator must inspect
the failure before deliberately restarting. Never auto-restart a
`halted` loop indefinitely via process-manager policy.

This is a local process; ChatGPT does not start or monitor it in the
background. Do not run multiple supervisors over the same archive /
portfolio checkpoint. Co-operating cycles use a local owner lock,
and concurrent runs must be treated as an operational error.
On shutdown, the in-progress synchronous cycle may complete before
the process exits; an operating system hard kill interrupts it and
the next start verifies the last atomic checkpoint.

## Operator emergency STOP marker

The default local kill-switch path is the checkpoint path plus `.STOP`.
For example, if the checkpoint is `data/upbit-btc-1h-paper.json`,
create `data/upbit-btc-1h-paper.json.STOP` to prevent **any subsequent**
cycle from fetching data or advancing simulated trades:

```sh
touch data/upbit-btc-1h-paper.json.STOP
```

After observing this marker, the supervisor emits `halted` and exits
with code `3`. This stop is checked **between** cycles; it does not
cancel a synchronous cycle already processing a saved fixture. For
immediate OS-level interruption, terminate the process as well; its
checkpoint atomics protect the last completed bar.

Remove the marker **only after** investigating the incident and
deliberately authorizing paper operation again. Never use a marker
as an exchange-side cancellation mechanism: there are no live orders
in this tool. The path is configurable with `--kill-switch`.

## 3. Read-only liveness probe

```sh
python tools/paper_healthcheck.py \
  data/upbit-btc-1h-health.json --max-age-seconds 7200
```

Exit statuses:

- `0`: valid, recent heartbeat marked healthy
- `2`: invalid, stale, risk_blocked, degraded, halted or stopped; operator action required
- `4`: heartbeat missing or unreadable

This only probes the *local reported state*. It does not claim that
the displayed paper portfolio matches a broker account. Health files
omit raw failures and API responses to reduce accidental leakage.
Explicit `daily_loss_halted` and `drawdown_halted` flags identify risk
blocks on new simulated entries.

## 4. Fault handling

1. **Network unavailable/429/5xx** — cycle fails; do not fabricate bars.
   Read the heartbeat, verify the public endpoint and applicable API limits.
2. **Market gap / changed completed candle** — stop; never overwrite
   historical bars to make a strategy look successful.
3. **Checkpoint mismatch** — preserve both archive and checkpoint.
   Diagnose whether a strategy, account assumption, or historical
   data changed. A new experiment must use fresh paths.
4. **Disk full / lock owner** — repair access/storage and check for
   an existing running process. Never delete active lock files.
5. **System clock skew / stale feed** — correct time synchronization
   and verify exchange UTC timestamps before restarting.
6. **Loss/drawdown halted** — treat as a risk signal, not a reason to
   raise position limits or switch to live mode.

No built-in Telegram or hosted alert delivery is configured by this
supervisor. Connect the healthcheck exit code to your local process
manager/monitoring after validating the failure flow.

## Critical limitations

- Only one KRW spot symbol and illustrative fees/slippage per paper book.
- No order-book-aware fills, liquidity guarantees, partial orders, or
  live account reconciliation.
- No historical pagination beyond public 200-candle snapshots;
  missing overlap remains an explicit fail-closed incident.
- No promise of investment returns, positive expectancy, or safe real
  order execution.
- A local polling loop is **not** sufficient to certify
  production-grade live automated trading.

See [production readiness gate](../PRODUCTION_CHECKLIST.md).


## Optional macOS launchd (local machine only)

A conservative template is provided at
`infra/launchd/co.uulab.trading.paper.plist.example`.
It is **not installed or started automatically** by the repository.
Copy the example into a user-specific LaunchAgents location, replace
**every** `REPLACE_WITH_ABSOLUTE_...` placeholder with actual local
paths, and ensure the virtual environment contains required packages.

```sh
mkdir -p "$HOME/Library/LaunchAgents"
cp infra/launchd/co.uulab.trading.paper.plist.example \
  "$HOME/Library/LaunchAgents/co.uulab.trading.paper.plist"
# Edit all absolute paths before running:
plutil -lint "$HOME/Library/LaunchAgents/co.uulab.trading.paper.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/co.uulab.trading.paper.plist"
```

The template sets `KeepAlive=false` so launchd **does not
automatically restart the bot** after its failure threshold. It runs
the explicit `--mode paper --loop` supervisor only. The data
directory must exist and be private; log files need independent local
retention/rotation. If you need to stop it:

```sh
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/co.uulab.trading.paper.plist"
```

Installing or stopping this service affects only the user's local
machine. An uninterrupted running Mac, network access, and OS permission
configuration are required for continuing operation. The file is a
template, not proof of deployed or successfully monitored runtime.


## Concurrency, performance, and risk-state correctness (2026-11-19)

- `--loop` now holds a **supervisor lifetime owner lock** across the
  entire capture, replay, and idle period. Two cooperating daemons
  targeting the same paper checkpoint cannot both remain active. A
  rejected second daemon exits with a nonzero status; its lock file
  should never be manually deleted while the first is running.
- The paper replay maintains the existing version-1 checkpoint SHA-256
  format using **incremental prefix hashing** instead of repeatedly
  rebuilding the full historical prefix at every bar. The historical
  hash format remains identical, so no migration or forced checkpoint
  reset is permitted. The separate initial checkpoint verification
  still replays recorded history.
- When the existing simulated daily-loss or drawdown guard blocks
  entries, the supervisor reports `risk_blocked` (not `healthy`).
  `paper_healthcheck.py` exits nonzero for this state. The paper
  supervisor **continues processing completed bars** so simulated
  exits can still be evaluated; there is no live broker connection.
  Operators must inspect loss conditions rather than hiding them with
  a green availability check.
- The checked-in code and regression tests are not evidence that an
  independent production CI run, a long-running macOS deployment,
  or real-money execution has passed.


## Shutdown heartbeat semantics (2026-11-19)

A graceful `--loop` shutdown writes `status: stopped` rather than leaving a
recent `healthy` heartbeat. The health probe treats `stopped`,
`risk_blocked`, `halted`, and `degraded` as operator-action-required states.
The supervisor does not automatically restart itself. A hard power loss or
SIGKILL cannot rewrite the heartbeat; age-based stale detection remains the
fallback and is not instantaneous.


## Persistent halt latch and manual recovery (2026-11-19)

A repeated-failure `halted` heartbeat is now an **inter-process
restart latch**. A restarted supervisor first reads the previous health
file. If its last status is `halted`, it refuses to fetch quotation data
or advance the paper ledger. This remains true after a machine reboot or
process-manager restart, provided the health file is preserved. An active
operator STOP marker also remains effective.

After diagnosing the incident, to re-enable **paper only**, retain the
halted heartbeat for investigation and deliberately move it out of the
active path before starting a new process:

```sh
# Stop any running process first; do not change live accounts.
mv data/upbit-btc-1h-health.json \
  "data/upbit-btc-1h-health.halted-$(date -u +%Y%m%dT%H%M%SZ).json"

# If and only if an operator previously created a STOP marker and
# has verified a safe restart, remove THAT file separately.
# rm data/upbit-btc-1h-paper.json.STOP

python tools/paper_supervisor.py --mode paper --once \
  --market KRW-BTC --unit 60 \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --heartbeat data/upbit-btc-1h-health.json
```

The saved archive and paper checkpoint must **not** be deleted or reset
as part of recovery; they are independently verified on restart.
Malformed or non-paper prior heartbeat files are also refused rather
than overwritten. Moving the heartbeat only resets the supervisor
monitoring latch, not strategy risk guards or the underlying paper ledger.
This control does not replace exchange-side risk controls for real orders.
