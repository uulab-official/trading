# Supervised local PAPER service (v48)

The PAPER service runs one existing continuous account and its exact bound
public-candle capture as a foreground pair. It starts the capture first, waits
for native ownership, then starts the consumer. A stop request shuts the
consumer down before the capture. Each explicit run preserves a separate attempt
record; workers are never automatically respawned.

Configuration and systemd unit generation are inert. They do not start the
collector, consume candles, submit orders or activate a service manager. An
explicit `run` or an operator's later `systemctl --user start` starts the pair;
the collector then uses its existing public provider transport. No credentials
or broker interface are part of this service.

The account retains the simulation and economic contract in
[CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md), including delayed next-open fills,
frozen strategy/risk configuration, full archived replay before resume and
binding to the original capture. A service does not change the account's
execution fingerprint or authorize a backup to run.

## Supported host and prerequisites

Use Linux, Python 3.10 or newer, the compatible existing Python environment and
an operator-owned local POSIX filesystem. Linux parent-death signals, local Unix
sockets and `/proc/self/fd` are required. Private directories, advisory locks and the native
SQLite rules must work on the selected filesystem. Windows, a portable daemon,
network-filesystem behavior and storage hardware durability are outside this
contract.

Keep the exact compatible checkout and Python environment available. Service
configuration records the repository, Python executable spelling, implementation
and major/minor version. A virtual-environment executable is preserved rather
than replaced by its resolved system interpreter. Upgrading Python, moving the
checkout or modifying frozen execution modules is not an automatic migration.

Create the original capture and account using their existing explicit commands
in [MARKET_CAPTURE.md](MARKET_CAPTURE.md) and
[CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md). This service interface accepts an
existing account and derives its capture. It has no alternate source, provider
URL, strategy override, arbitrary command or live flag.

Before configuration, request cooperative stop of the consumer and wait for it
to exit; then stop its capture and wait for that process to exit. Preserve their
final JSON results and any refusal evidence. A saved `stopped` field alone does
not prove that an owner has released its lock.

## Configure a new private service directory

Run from the known checkout with the account's compatible interpreter:

```bash
.venv/bin/python tools/paper_service.py configure /absolute/private/btc-account \
  --directory /absolute/private/btc-service
```

The destination must be a new leaf directory outside the account and capture.
Its parent must already exist. An existing destination, including an empty or
partially created directory, is not overwritten. Preserve possible output after
an interrupted or uncertain configuration result.

Configuration holds the account audit lock and then the bound capture lock
while it verifies the complete account history, source relationship and stable
input identities. It refuses incompatible runtimes, active owners, terminal
account/capture state, retained blocking reasons, uncertain acquisition state,
exhausted account capacity and future saved timestamps. Read-only preflight
does not repair any input.

| Configure option | Default | Allowed values and meaning |
|---|---:|---|
| `--startup-seconds` | 180 | Integer 10..900; each worker's startup/ownership budget |
| `--heartbeat-seconds` | 30 | Integer 5..120; maximum missing-progress budget after ownership |
| `--stop-seconds` | 15 | Integer 1..60; cooperative shutdown grace for each worker |

The startup budget includes imports, the capture's first acquisition/cooldown
and the consumer's full replay. Capture and consumer have separate startup
budgets. These settings bound process supervision; they do not relax native data
freshness or source continuity requirements. Options are frozen in the service
configuration. Repeated options, abbreviations and unrelated flags are refused.

The strict commands are:

```text
configure ACCOUNT --directory SERVICE [--startup-seconds N --heartbeat-seconds N --stop-seconds N]
run SERVICE
check SERVICE
unit SERVICE --output NEW_FILE
```

Every invocation emits one JSON result on stdout with `exit_code`. Diagnostics
are separate on stderr, and CLI failures use fixed private labels rather than
exception paths or tracebacks. The runbook is the usage reference; help-like
flags are handled as invalid arguments under the same JSON contract.
If stdout itself is broken or full, a JSON delivery cannot be confirmed. The CLI
returns `4` and emits only `paper_service_output_unconfirmed` to usable stderr;
it does not retry execution or erase already retained attempt evidence.

## Run and stop in the foreground

```bash
.venv/bin/python tools/paper_service.py run /absolute/private/btc-service
```

The command remains in the foreground and emits its final public JSON after
child cleanup and retained evidence have been confirmed. In another terminal,
use `check` below for the current service observation.

Each run acquires the service's exclusive lease and repeats full read-only
preflight before starting workers. Admission can wait up to one second for actual
lock contention, including a brief shared probe from `check`; it preserves held
path identities and observes stop intent during that wait. A lease still held at
the deadline is refused. If a prior service finishes within the window, this
explicit invocation can acquire the lease, then must pass fresh native preflight
and the previous-attempt evidence checks. No worker or native operation is retried.
This does not
make preflight atomic with unrelated operators using the original direct CLIs;
native writer ownership and resume validation remain authoritative when each
worker actually opens its source/account.

Press Ctrl-C in the foreground terminal, or request SIGTERM through the trusted
controller that owns that live process. Handlers record only the first numeric
signal intent. The supervisor signals and reaps the consumer first, then the
capture, while continuing to drain both output streams. There is no PID-file
kill command, detached mode or saved-PID authority.

Each child receives its configured cooperative grace. If it does not exit, the
supervisor sends SIGKILL and allows a separate five-second reap budget. Forced
or unconfirmed cleanup is an uncertain result, not a successful graceful stop.
A terminal refusal or storage failure takes priority over a simultaneous signal.
Successful cooperative SIGINT/SIGTERM stops return 130/143.

Before entering any native runtime, each worker arms Linux's parent-death
SIGKILL guard, verifies it and checks the expected parent again. If the parent
dies before or after that handshake, the worker must not continue unsupervised.
This contains worker lifetime; abrupt SIGKILL can still leave uncertain native
storage/acquisition evidence. It does not promise graceful shutdown after parent
death, physical power loss or a hostile privileged process rewriting state.

After a clean stop, inspect the final result and the native observations. An
explicit later `run` resumes the same compatible account only if preflight and
native resume rules still allow it. There is no retry loop. Creating another
service directory does not clear a terminal account or capture refusal.

## Check fresh process progress separately from data readiness

```bash
.venv/bin/python tools/paper_service.py check /absolute/private/btc-service
```

A live check uses a new challenge over the private current-attempt Unix socket
and verifies the attempt identity and same-UID peer. A successful observation
means both direct workers entered their native owned loops, remain unreaped and
reported recent progress. It is not inferred from a saved `running` field, a PID
or the existence of a lock.

Startup, stopping or degraded progress returns 1. A lease-free service reports
`not_running` with exit 1. An unavailable, malformed or unresponsive live probe
returns 2. A check never starts a process, sends a signal, trusts an old successful
response or uses a cached healthy result. A SIGSTOPed worker eventually loses
progress health even while its process and lock still exist.

Native cooldown and waiting can be healthy process progress. They do not mean
current market data, an admissible trade or a profitable strategy. Service
results explicitly retain `data_readiness=not_assessed` and
`live_execution=false`.

Use the separate native observations for data/account state:

```bash
.venv/bin/python tools/market_capture.py check /absolute/private/btc-capture
.venv/bin/python tools/paper_stream.py check /absolute/private/btc-account
```

After stopping the pair and waiting for both owners to exit, use the full account
audit when required:

```bash
.venv/bin/python tools/paper_stream.py audit /absolute/private/btc-account
```

## Retained attempts and the 64-attempt limit

The service root is owned `0700` and has exactly `.service.lock`, immutable
`service.json` and an owned `0700` `attempts` directory. Configuration is bounded
to 16 KiB. Owned files are private `0600`, regular and single-link; symbolic
links, unexpected members and observed substitutions are refused.

Attempts are consecutive `000001` through `000064`. Each has its own random
identity, with no mutable latest-pointer file. The greatest sequence is the
latest attempt. An allocated attempt is preserved even if it never reaches a
confirmed final result.

| Attempt evidence | Purpose and bound |
|---|---|
| `started.json` | Fsynced before launch; identities, configuration digest and preflight evidence; at most 16 KiB |
| `result.json` | Actual child outcomes after cleanup, written before final publication sync; at most 64 KiB |
| `capture.stderr`, `stream.stderr` | Retained worker diagnostics; at most 256 KiB per worker |
| Private health socket, when present | Read-only live probe for that attempt; never reused as another attempt's endpoint |

Before allocating another attempt, the latest retained result must be
structurally valid, contain `cleanup_confirmed` as the exact boolean `true`, and
have both stderr files present. Otherwise `previous_attempt_unconfirmed` refuses
another run in the same service directory. Preserve all evidence. Only after
inspection and native owner/preflight checks may the operator configure a new
service directory from an eligible original account; a new directory does not
clear a native refusal or establish that earlier cleanup succeeded.

`result.json` is written before the final publication sync. Its presence,
including a readable `cleanup_confirmed=true` field, is not by itself proof that
publication was confirmed. The public final JSON follows confirmation of the
retained result, diagnostics and directory sync. An interrupted final write or
missing public confirmation requires inspection rather than an assumption of
success.

Missing/partial final records are unconfirmed evidence. Do not delete, edit,
renumber or recycle attempts to manufacture a clean state or free a slot. At 64
preserved attempts, retain the entire service directory and explicitly configure
a new one from the still-eligible original account. The underlying
account/capture limits continue to apply independently.

Worker messages, socket requests/responses and stderr are bounded. Malformed
protocol, output flooding, an unsolicited child exit, missed progress or failed
cleanup ends the pair. No child is automatically restarted, including one that
exits with code 0.

For a stopped pair's durable archive, use
[PAPER_BACKUP.md](PAPER_BACKUP.md). A verified backup or recovered copy remains
non-executing evidence and cannot be supplied as an executable service account.

## Generate an inactive systemd user unit

Generation reads immutable service configuration without taking a shared
service lease, so it also works while an owner holds the exclusive lease. It
does not claim that the running pair is healthy.

Use absolute paths containing only ASCII letters, digits, underscore, dot,
slash and dash for the service, interpreter, repository and output. Spaces,
non-ASCII names, percent expansion, dollar expansion and other interpolation
characters are refused. The interpreter's virtual-environment path is kept.
The output parent must already exist; choose a new output outside the service,
account and capture directories.

```bash
.venv/bin/python tools/paper_service.py unit /absolute/private/btc-service \
  --output /absolute/private/units/btc-paper.service
systemd-analyze verify /absolute/private/units/btc-paper.service
```

The generator publishes a new `0600` file without clobbering existing files or
symlinks. It rechecks the held configuration after the final output directory
sync. If confirmation fails, preserve any possible output; do not treat the
file's existence as proof of successful generation.

The generated unit uses this fixed process contract:

| Setting | Value |
|---|---|
| `Type` | `simple` |
| `ExecStart` | Recorded Python, `-I -B -u`, checked-in `tools/paper_service.py run`, recorded service directory |
| `Restart` | `no` |
| `KillMode` / `KillSignal` | `mixed` / `SIGTERM` |
| `SendSIGKILL` | `yes` |
| `SuccessExitStatus` | `130 143` |
| `UMask` / `NoNewPrivileges` / `LimitCORE` | `0077` / `yes` / `0` |
| `TimeoutStopSec` | `2 * (stop_seconds + 5) + 5`; default 45 seconds |

The timeout provides both cooperative child graces, both five-second reap
budgets and five seconds of manager margin. `KillMode=mixed` lets the supervisor
perform ordered shutdown first while allowing manager cgroup cleanup at its
deadline. It complements each worker's kernel parent-death guard. The unit has
no `[Install]` section or automatic boot/login activation policy.

### Explicit manager actions on the operator's host

If the operator chooses systemd management, make a reviewed new unit available
under the intended user's `~/.config/systemd/user/` directory. The same `unit`
command may generate directly to a new absolute filename there; it still only
writes the file. The following commands are manual operator actions:

```bash
systemctl --user daemon-reload
systemctl --user start btc-paper.service
.venv/bin/python tools/paper_service.py check /absolute/private/btc-service
systemctl --user stop btc-paper.service
```

After reviewing the stopped attempt and any native refusal/uncertainty, an
explicit restart can request a new attempt:

```bash
systemctl --user restart btc-paper.service
```

Each restart repeats preflight and consumes a new attempt only through the normal
service lifecycle. `systemctl` reporting `active` establishes manager process
state, not recent worker progress or data readiness. Use the service probe and
native observations above. Unit generation and the offline tests perform no
install/start/stop/restart/enable operation. No `enable` or lingering change is
performed or prescribed by this workflow.

## Exit codes and unverified operating limits

| Exit | Meaning |
|---:|---|
| 0 | Configuration/unit generation succeeded, or a live probe observed current worker progress |
| 1 | Check observed not-running, startup, stopping or degraded process progress |
| 2 | Invalid/unavailable input or probe; no successful launch/health claim |
| 3 | Preserved native policy or refusal; inspect retained evidence |
| 4 | Storage uncertainty, unexpected termination/protocol failure or forced/unconfirmed cleanup |
| 130 / 143 | Requested cooperative SIGINT/SIGTERM stop without higher-priority refusal or uncertainty |

The focused tests use controlled local files, synthetic native input and real
OS signals; unit syntax is checked with `systemd-analyze verify`. Those tests do
not establish real provider availability, an unattended production soak,
arbitrary-privilege isolation, physical storage durability, executable disaster
recovery, browser behavior, profitability or readiness for live trading.
