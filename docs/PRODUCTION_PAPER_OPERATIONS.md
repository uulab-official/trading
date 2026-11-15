# Local paper operations runbook (v49)

The v46 consumer continues one paper account from the separately operated public
closed-candle capture. Follow [CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md) for
indicator-only activation, atomic account/strategy/risk/cursor commits and full
archived replay on explicit resume. [MARKET_CAPTURE.md](MARKET_CAPTURE.md)
describes the capture's foreground lifecycle, freshness and immutable export
into the original historical workflow. Starting a capture alone creates no account.

v47 adds [PAPER_BACKUP.md](PAPER_BACKUP.md): paired quiescent backups, read-only
full verification and separately verified non-executing recovery copies. These
preserve the original execution fingerprint, source binding and refusal evidence;
they do not authorize rollback, source rebinding or restored-account execution.

v48 adds [PAPER_SERVICE.md](PAPER_SERVICE.md): a guarded Linux foreground
supervisor for one existing account/capture pair, fresh read-only process-health
checks, bounded private attempts and inactive systemd user-unit generation.
Configuration and unit generation are inert. Explicit service `run` resumes the
existing public collector and its local PAPER consumer; it contacts the same
public endpoint through the unchanged capture runtime.

v49 adds [PAPER_RELEASE.md](PAPER_RELEASE.md): a deterministic minimal source
archive with external SHA-256 verification and strict private-tree extraction.
It includes no trading stores or secrets and performs no service activation.

This runbook covers continuous and historical local paper operation, saved
research and the read-only observer on a local POSIX filesystem with Python 3.10
or newer. Only an explicitly started capture, directly or through the supervisor,
contacts the public candle endpoint;
the consumer uses local saved data and no broker. No credentials are needed.
Controlled tests are not real-provider soak or deployment evidence.
The dashboard binds only to `127.0.0.1`; open it on the same computer.
Host/Origin/Fetch-Site checks, CSP and rejected writes are browser-origin
protections, not authentication. Public hosting, reverse proxies, tunnels,
port forwarding and remote account access are outside this operating contract.

## Prepare and pin the environment

For the broader repository workflows below, use an existing environment with
the repository packages and engine dependencies installed. The seven-command
source-release path has its own standard-library-only contract described below.
From the repository root, record the exact revision and Python version before
creating an experiment:

```bash
git rev-parse HEAD
python --version
mkdir -p .private
chmod 700 .private
```

One-time package setup, if needed, is
`python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server`.
Dependency installation may contact package registries; it is separate from the
local commands below. Keep `.private` owned by the operator and mode `0700`.
Run directories and private report files use `0700` and `0600` respectively.
Plaintext accounts, evidence, frozen inputs, source identifiers, HTML and reports
are private even when they contain no API secrets. Git ignore rules do not
provide filesystem access control. Do not broaden permissions to debug a reader.

`python -m trading_core.config_cli validate` validates the separate legacy YAML
configuration. A successful exit is settings validation only. Its
`is_live_configuration_complete()` predicate preserves the former live-settings
check; `is_production_ready()` always returns `False` in this revision. CLI
`validate` and `show` report operational readiness as unverified. Neither command
checks a live feed, broker, account reconciliation or recovery authorization.
The paper workflow below does not require YAML credentials or Telegram tests.

## Prepare a verified source runtime (v49)

Follow [PAPER_RELEASE.md](PAPER_RELEASE.md) from a trusted checkout. The fixed
profile is 55 payload files plus `paper-release.json`, preserving source layout
for seven tools: capture, stream, service, its guarded internal worker, backup,
synthetic backup drill and release verification. These supported commands use
only the standard library on CPython 3.10+ and Linux. The interpreter, standard
library and OS remain external; other monorepo workflows retain their existing
dependencies. The legacy Rust Docker image is a separate runtime.

`paper_release.py build --output NEW_ARCHIVE` creates a canonical uncompressed
USTAR and reports its digest. Preserve that SHA-256 with the reviewed revision
and trusted CI evidence. `check ARCHIVE --sha256 EXPECTED_SHA256`,
`extract ARCHIVE --sha256 EXPECTED_SHA256 --directory NEW_DIRECTORY` and
`verify DIRECTORY --sha256 EXPECTED_SHA256` require that external digest.
The manifest alone does not authenticate the source. Python 3.10/3.12/3.13 release
CI is configured; record actual results for the published head before claiming
those jobs passed.

Use new outputs outside the checkout and trading stores. Archive/source inputs
must be stable, operator-owned, regular single-link files without symlink
components or group/world write permission. Extraction creates private `0700`
directories and `0600` files after complete archive validation. Verification
checks the exact tree and rejects extra files. Run extracted commands with
`-I -S -B` and keep all data, secrets, generated evidence and bytecode outside it.
Preserve possible output and partial trees after any unconfirmed operation.

Release operations do not change existing account/capture paths, restore data,
authorize execution or assess readiness. Existing service configurations remain
bound to their exact runtime root and interpreter. Inspect the original state
and explicitly configure a new service directory when choosing another runtime
location; never rewrite saved bindings to force acceptance. Activation, provider
soak and live-readiness evidence remain separate operating work.

## Create and continue one paper account

Run a new capture in one terminal and leave it running:

```bash
python tools/market_capture.py run --market KRW-BTC --unit 1 \
  --history 120 --retention 5000 --poll-seconds 15 \
  --directory .private/btc-capture-001
```

In another terminal, check the source before starting the consumer:

```bash
python tools/market_capture.py check .private/btc-capture-001
```

Once the source check returns current/exit `0`, create a new account outside
the capture directory:

```bash
python tools/paper_stream.py run --capture .private/btc-capture-001 \
  --directory .private/btc-stream-001 --strategy ma_cross \
  --fast-period 3 --slow-period 5 --initial-balance 1000000 --poll-seconds 5
```

The original MA/RSI strategy warms up using at most 500 contiguous candles,
requiring at least `max(50, slow_period)` for MA or `max(50, rsi_period + 1)`
for RSI. All bootstrap signals are discarded. The initial account has cash,
zero holding/fees/fills and no pending signal. After activation, each bar commits
the complete account, original strategy/risk state, latest 500-bar indicator
window, cursor and optional fill atomically in `stream.sqlite3`.

Fills use delayed next-open simulation. The following bar's opening price is
available only after it closes; model event time is distinct from the later
`applied_at` observation/commit time. This is not broker execution. Source path,
physical identity and configuration are frozen; all available retained overlap,
up to 5,000 capture rows, is compared with the archived bootstrap/events. Stale
or temporarily unreadable data cannot authorize another economic event.

Use the passive CLI or independent local API while the consumer runs:

```bash
python tools/paper_stream.py check .private/btc-stream-001
python apps/dashboard/server.py --paper-stream .private/btc-stream-001 --port 8765
```

Read `http://127.0.0.1:8765/api/paper-stream`. There is no new browser page;
the endpoint and check inspect a bounded structural head, not the full economic
history. They do not prove owner liveness or change the four readiness sources.

Stop the consumer with Ctrl-C/SIGTERM, wait for its exit, and audit its saved
prefix before deciding whether to resume:

```bash
python tools/paper_stream.py audit .private/btc-stream-001
```

Audit refuses an active writer and returns `verified_prefix` on success. After
reviewing that result and source readiness, explicitly resume:

```bash
python tools/paper_stream.py resume .private/btc-stream-001
```

Resume accepts only the directory and optional `--max-polls`; source, strategy,
cost/risk and polling settings cannot be overridden. It requires the frozen
module/Python runtime fingerprint and replays every archived event before new
consumption, then re-reads the capture. A known terminal error remains refused
regardless of saved phase. No forced reset, automatic migration or replayed fill
submission is available.

The archive is bounded to 100,000 consumed bars and 256 MiB without financial
event eviction. Capacity is a preserved terminal result, not automatic archival
or creation of another account. Storage uncertainty stops the owner immediately;
preserve the account and use explicit reopen to determine the durable prefix.
Full commands, status fields and exit codes are in
[CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md).

## Supervise the existing pair on Linux

Stop and confirm exit of the consumer first, then its collector. Configure a new
service directory outside both native stores, using the compatible interpreter:

```bash
python tools/paper_service.py configure .private/btc-stream-001 \
  --directory .private/btc-service-001
python tools/paper_service.py run .private/btc-service-001
```

Full read-only native preflight precedes worker launch. The collector must enter
its owned loop before the consumer starts. Both workers arm Linux parent-death
SIGKILL before native runtime entry. No child respawns after a fault. The
supervisor holds the service lease and signals only its own live process handles.
Lease admission tolerates a brief health-probe collision with a bounded one-second
wait, before preflight or worker launch. A still-busy lease or changed binding is
refused. A previous owner that finishes during the wait can hand over only to
this explicitly requested invocation, subject to fresh preflight and retained
attempt checks.

Use `python tools/paper_service.py check .private/btc-service-001` in another
terminal. It returns current direct-process progress, with
`data_readiness=not_assessed`; existing capture/account checks still establish
their separate data observations. No saved status is substituted for a live
response. Readiness of the dashboard's four sources is unchanged.

Ctrl-C or SIGTERM starts consumer-first cleanup. Exit `130`/`143` means requested
cooperative stop without a higher-priority fault, `3` preserves native refusal,
and `4` means failure/forced/unconfirmed outcome. Inspect private attempt results
and native states before explicit restart. A missing final result or unconfirmed
cleanup blocks another run in the same service directory; preserve it and follow
the investigation/new-configuration procedure in [PAPER_SERVICE.md](PAPER_SERVICE.md).
Unit generation only writes a reviewed inactive file; manager activation,
operational soak and host-level recovery remain separate operator work.

## Create and inspect one historical account

Use a new name for every independent run. The demo is synthetic:

```bash
python tools/paper_operate.py --demo --resumable --directory .private/paper-001
python tools/paper_audit.py .private/paper-001
python tools/paper_session_status.py --database .private/paper-001/account.sqlite
```

`paper_operate` uses the original MA/RSI strategy and paper accounting. A supplied
historical fixture may replace `--demo`, using the exact format in
[DURABLE_PAPER_RUN.md](DURABLE_PAPER_RUN.md). A new leaf directory is mandatory;
an existing empty, failed or completed directory is never a fresh run. Saved
prices are historical, fees/slippage are assumptions, and zero trades is possible.
The completed run contains input, account, initial/final evidence, result and,
with `--resumable`, its original recovery manifest. Audit exit `0` means the
captured completed artifacts agree with the original replay, not profit or
permission to trade. A completion marker alone is not a verified completion.

| Command | Exit semantics |
|---|---|
| `paper_operate.py` | `0` completed halted historical run; `2` invalid/failed operation; `3` halt unconfirmed; `130` SIGINT/interrupted; `143` SIGTERM |
| `paper_audit.py` | `0` verified captured completion; `2` blocked/unavailable/invalid; `130` interrupted |
| `paper_session_status.py` | `0` validated read; `2` unavailable/invalid, fixed JSON error on stderr |
| `paper_preflight.py` | `0` compatible captured prefix; `4` saved-fill acknowledgement needs review; `2` blocked/unavailable/invalid; `130` interrupted |
| `paper_resume.py` | `0` completed halted historical recovery; `2` invalid/conflicting/failed; `3` halt unconfirmed; `4` reconciliation required; `130` SIGINT/interrupted; `143` SIGTERM |

See [shutdown semantics](PAPER_PROCESS_SHUTDOWN.md) for writer finalization. An
observer exit or saved active mode says nothing about whether an account owner
is alive. SIGKILL or power loss cannot run the writer's halt finalizer.

## Start and check the observer

In one terminal, keep this foreground process running:

```bash
python apps/dashboard/server.py --paper-session .private/paper-001/account.sqlite --port 8765
```

In a second terminal:

```bash
python tools/paper_dashboard_check.py --port 8765 --liveness
python tools/paper_dashboard_check.py --port 8765 --timeout 5
```

Open `http://127.0.0.1:8765/paper-account.html` or
`http://127.0.0.1:8765/paper-orders.html` locally. Optional `--journal`,
`--replay-report` and `--research-report` paths select other independent sources.
Only explicitly configured journal/replay/research/paper_session sources count
toward the existing readiness schema; an omitted source is
`not_configured`. A missing, changed, busy, oversized, invalid or corrupt
configured source is unavailable. It does not become zero balances or hide
another readable source. The UI may retain a previous successful observation
with an explicit warning; its refresh time is not a market timestamp.

| Probe | Meaning | HTTP |
|---|---|---|
| `/api/liveness` | Observer process responds, `scope=observer_process`, `status=alive`; no source read | `200` |
| `/api/readiness` | Existing validators can read every configured source among journal/replay/research/paper_session; `scope=configured_source_readability`, `status=ready` | `200` |
| `/api/readiness` with none of those four sources configured | `status=not_configured` | `503` |
| `/api/readiness` with any of those configured sources unavailable | `status=degraded`; independent per-source statuses retained | `503` |
| `/api/health` | Legacy admission-journal observation only | `200` or `503` |
| `/api/market-capture` | Independent closed-candle capture observation; not a readiness key | `200` valid current/noncurrent; `503` unavailable |
| `/api/paper-stream` | Independent bounded continuous-account head observation; not a readiness key or full audit | `200` valid current/noncurrent; `503` unavailable |

The liveness/readiness probes support GET/HEAD with no parameters. Their schema
is version `1`, `source=local_paper_dashboard`, `mode=paper`, `read_only=true`,
`live_enabled=false`, with UTC `observed_at`. Readiness lists exactly `journal`,
`replay`, `research`, `paper_session`, each `available`, `unavailable` or
`not_configured`. Probes expose no paths, account identifiers, metrics or raw
exceptions. Ready means readable saved observations. It does not prove writer
liveness, market freshness, broker reconciliation or permission to resume.

The check CLI connects directly to fixed `127.0.0.1` and strictly validates the
contract. It accepts only `--port` (`1..65535`, default `8765`), `--timeout`
(`0.1..10` seconds total network deadline, default `5`) and optional `--liveness`; there is no host, URL,
proxy or redirect support. It reads at most `8192` response bytes and prints one
JSON stdout document. Exit `0`: valid ready/alive; `1`: valid degraded or
not_configured; `2`: invalid arguments, transport/protocol/unexpected response;
`130`: interrupted. A refused connection is different from a validated degraded
observation. Readiness is a bounded read, not a recommended high-frequency poll.

## Runtime and source budgets

The observer has at most eight request handlers and an eight-connection backlog.
At capacity, a connection is closed before another worker starts. Connections
have a two-second inactivity timeout and a ten-second absolute lifetime,
including trickled request bytes. Aggregate request headers are capped at 16 KiB
and the request line at 4,096 bytes. HTTP/1.0 single requests are retained. These
bounds can reject an overloaded local check; they do not establish service SLA.
SIGINT/SIGTERM only request shutdown in the signal handler; the owner stops the
serving thread, closes active sockets and waits at most two seconds for request
workers. Previous signal handlers are restored. Server exit `130` is SIGINT,
`143` SIGTERM, `2` startup/configuration failure, `0` ordinary completion.
Request/parser errors and startup errors are sanitized; they omit raw source
paths and tracebacks. Stop the foreground server with Ctrl-C or send SIGTERM to
its recorded PID; do not kill an unrelated account writer.

| Source/work | Bound and failure behavior |
|---|---|
| Admission journal | DB and each existing `-wal` / `-shm` sidecar at most 64 MiB; 10,000 orders; 16,384 UTF-8 bytes per payload; at most 16 metadata entries; at most 50 returned audit events with bounded text |
| Journal work | One read transaction; 1,000,000 SQLite VM operations and one-second monotonic work budget, including Python validation; validate all rows before publishing totals and retain only the requested order page |
| Replay report | Existing 8 MiB / 10,000-fill validation; HTTP returns the last 100 fills |
| Research report | Existing 2 MiB report bound and original schema/candidate/work validation |
| Research inputs | Fixture at most 32 MiB; candidate file at most 16,384 bytes; original data/strategy rules still apply |
| Durable paper account | Existing 64 MiB DB / 16 MiB bundle bounds, at most 10,000 orders; WAL and hot journals requiring recovery are refused; details follow the existing bounded page contracts |
| Durable replay input | At most 4 MiB / 5,000 bars; audit/recovery replays history in memory and is an explicit CLI operation |
| Public capture | At most 5,000 retained bars and 64 MiB per DB/sidecar; separate data/observation freshness, exact overlap and visible eviction counters |
| Continuous paper account | At most 100,000 consumed bars and a 256-MiB DB; bounded structural head with a 500-bar indicator window; no financial event eviction |
| Continuous paper audit/resume | Explicit full archived replay with bounded memory; stopped-owner audit holds a shared lock and refuses an active writer; resume rechecks the source afterward |
| Paired backup/recovery bundle | Fixed private four-file layout; at most 256 MiB account + 64 MiB capture payloads and 64 KiB metadata; full archived replay, no-clobber output and no executable account/capture layout |

Bounded application work cannot guarantee a hard deadline for a hung kernel or
filesystem call. These are acceptance/resource limits, not demonstrated maximum
throughput or long-duration soak results. Journal failure is generic
`journal_unavailable`; it never publishes partial totals as complete. SQLite
read-only journal observation performs no logical database or WAL mutation;
native WAL reads can change `-shm` coordination bytes. Read-only does not promise
byte-immutable coordination sidecars or unchanged filesystem access times.

Replay/research reads use regular files, refuse final symlinks and special files,
read through bounded nonblocking/no-follow descriptors and check file/parent
identity before and after. Changed observations are rejected. Their publication
uses private staged bytes and pinned parent descriptors, fsync and no-clobber
publication with final identity confirmation. Equal existing reports preserve
idempotence; conflicting reports are never overwritten. A possible publication
followed by failed confirmation raises `ReportPublicationUncertain`: preserve
any output and inspect it explicitly, never automatically rerun or repair.
Research I/O exits `3`; replay export I/O exits `4`. A failed export may already
have advanced its checkpoint, so keep both. See the existing
[replay guide](PAPER_REPLAY_DASHBOARD.md) and [research guide](QUANT_RESEARCH.md)
for their original validators, result schemas and aligned explicit
failure/inspection guidance.

## Investigate and explicitly recover a historical run

These commands apply to the original historical account/recovery schema.
Continuous accounts use `paper_stream.py check/audit/resume` as described above;
their `stream.sqlite3` files are not historical `account.sqlite` inputs.

1. Preserve the entire run and any reports after an error. Record process exits
   and the code/Python versions. Do not delete intent, lock, manifest, account,
   evidence or possible publication files to make a retry succeed.
2. Read the account using `paper_session_status.py`. Use `paper_audit.py` for an
   existing completed opted-in run; use preflight for an incomplete opted-in run:

   ```bash
   python tools/paper_preflight.py .private/paper-interrupted-001
   ```

3. A compatible preflight is a captured prefix consistency result, without a
   writer lease or resume permission. The original recovery command rechecks
   files, fingerprints, account state and ownership under its own locks. Only
   for an incomplete run created with `--resumable`, deliberately invoke:

   ```bash
   python tools/paper_resume.py .private/paper-interrupted-001
   ```

4. If saved simulated fills have lost acknowledgements, inspect their evidence
   first. Only an explicit decision to read back those saved fills uses:

   ```bash
   python tools/paper_resume.py .private/paper-interrupted-001 --reconcile-saved-fills
   ```

   An unresolved intent without a saved fill remains blocked even with this
   option. Never blindly resubmit or infer rejection. Completed runs refuse
   resume; unmarked older runs cannot acquire recovery by editing a manifest.

Recovery performs deterministic historical catch-up with the original engine;
it is not an always-on restart policy. Changed execution code or Python
fingerprints refuse recovery without a force switch. See
[DURABLE_PAPER_RESUME.md](DURABLE_PAPER_RESUME.md),
[DURABLE_PAPER_PREFLIGHT.md](DURABLE_PAPER_PREFLIGHT.md) and
[DURABLE_PAPER_AUDIT.md](DURABLE_PAPER_AUDIT.md).

## Back up and roll back without losing evidence

For historical runs, stop the writer through its supported shutdown path and
confirm it has exited; inspect the saved account, including any unconfirmed halt.
Stop observers before
maintenance. Back up the **whole quiescent account/run directory**, including
frozen input, manifest, SQLite and any sidecars, evidence and result, to an
operator-owned private destination. Keep the original directory and modes.
Copying only `account.sqlite` from a running writer is not this backup procedure.
Keep independent report/checkpoint files together with their recorded revision.
An archive is a separate observation, not a command to resume it or repair it.

### Continuous account backup and recovery verification

Stop the continuous consumer first, then its bound capture, and confirm both
processes have exited. Use the original fingerprint-compatible code and Python
environment. From the repository root:

```bash
python tools/paper_backup.py create .private/btc-stream-001 \
  --output .private/btc-backup-001
python tools/paper_backup.py check .private/btc-backup-001
python tools/paper_backup.py verify-restore .private/btc-backup-001 \
  --output .private/btc-recovery-check-001
```

Each output is a new private `0700` directory with `0600` files. Creation holds
the actual account/capture locks through full replay and copying; a saved phase
does not replace process quiescence. Read-only `check` verifies the actual
archived payloads without scratch extraction or input mutation. `verify-restore`
copies the exact payloads/manifest and fully verifies the separate copy.

Both outputs retain `.bin` payload names instead of `stream.sqlite3` or
`capture.sqlite3`. They preserve account state, timestamps, errors and the original
source binding. A copied or relocated capture is not an authorized replacement.
Successful verification proves a consistent archived prefix, not the newest state,
absence of later refusals, permission to roll back or transfer of execution
authority. Do not rename these payloads into a runnable account or rewrite the
manifest to admit a different source.

The payload bound is 320 MiB plus at most 64 KiB of metadata per bundle; allow
additional filesystem overhead and space for both input and recovery copies.
No existing destination is overwritten. For any failure or interruption, retain
the inputs and possible output, including partial/unconfirmed files. Backup
commands return `0` on verified success, `2` on input/ownership/verification
refusal, `4` on storage uncertainty or unexpected failure, and `130`/`143` on
interruption. Runtime errors expose a conservative `output_may_exist` flag.
Read-only verification never repairs a nonempty journal or clears a refusal.
See [PAPER_BACKUP.md](PAPER_BACKUP.md) for exact schemas and preservation rules.

Run a separate local drill with fresh synthetic inputs and network access
forbidden:

```bash
python tools/paper_backup_drill.py --directory .private/paper-backup-drill-001
```

The drill accepts a new private directory, creates its own capture/account and
records local backup/recovery evidence. It does not use an existing trading
account or certify real backup-media transport, hardware durability, executable
host-disaster recovery or service deployment. Keep automatic restart disabled:
temporary runtime waits/retries are already explicit, while terminal refusal,
fingerprint mismatch and uncertain storage require an operator decision.

### Pinned-code rollback

Record a known exact commit before upgrading. For rollback, prepare a separate
checkout/environment at that recorded commit, stop the current observer, and
start the pinned observer against preserved sources after read-only inspection.
For historical recovery and continuous resume, use the original
fingerprint-compatible execution code and Python environment. Do not reset/delete
data, replace evidence with an older
copy, rewrite hashes/manifests, or run schema repair as rollback. A changed source
or refusal requires investigation. A restored archive is not a new execution
account. The legacy deployment/rollback scripts are not validated by this runbook.

The implemented public capture, continuous consumer and non-executing paired
backup/recovery workflow have controlled local regression coverage. Real
backup-media recovery and an executable host-disaster migration remain unverified.
No actual visual QA, long-duration real-provider soak, service deployment,
broker-authoritative reconciliation, Windows/filesystem portability or live trading
completion is certified here. Use the remaining evidence checklist
in [PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md).
