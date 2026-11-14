# Local paper operations runbook (v44)

This runbook covers the supported local historical paper runner, saved research
and read-only observer on a local POSIX filesystem with Python 3.10 or newer.
It does not start a prospective market feed or a broker. No credentials are
needed. The dashboard binds only to `127.0.0.1`; open it on the same computer.
Host/Origin/Fetch-Site checks, CSP and rejected writes are browser-origin
protections, not authentication. Public hosting, reverse proxies, tunnels,
port forwarding and remote account access are outside this operating contract.

## Prepare and pin the environment

From the repository root, use an existing environment with the repository
packages and engine dependencies installed. Record the exact revision and Python
version before creating an experiment:

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
Only explicitly configured sources count toward readiness; an omitted source is
`not_configured`. A missing, changed, busy, oversized, invalid or corrupt
configured source is unavailable. It does not become zero balances or hide
another readable source. The UI may retain a previous successful observation
with an explicit warning; its refresh time is not a market timestamp.

| Probe | Meaning | HTTP |
|---|---|---|
| `/api/liveness` | Observer process responds, `scope=observer_process`, `status=alive`; no source read | `200` |
| `/api/readiness` | Existing validators can read all configured local sources, `scope=configured_source_readability`, `status=ready` | `200` |
| `/api/readiness` with no configured source | `status=not_configured` | `503` |
| `/api/readiness` with any unavailable configured source | `status=degraded`; independent per-source statuses retained | `503` |
| `/api/health` | Legacy admission-journal observation only | `200` or `503` |

The two new probes support GET/HEAD with no parameters. Their schema is version
`1`, `source=local_paper_dashboard`, `mode=paper`, `read_only=true`,
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

## Investigate and explicitly recover

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

Stop the writer through its supported shutdown path and confirm it has exited;
inspect the saved account, including any unconfirmed halt. Stop observers before
maintenance. Back up the **whole quiescent account/run directory**, including
frozen input, manifest, SQLite and any sidecars, evidence and result, to an
operator-owned private destination. Keep the original directory and modes.
Copying only `account.sqlite` from a running writer is not this backup procedure.
Keep independent report/checkpoint files together with their recorded revision.
An archive is a separate observation, not a command to resume it or repair it.

Record a known exact commit before upgrading. For rollback, prepare a separate
checkout/environment at that recorded commit, stop the current observer, and
start the pinned observer against preserved sources after read-only inspection.
For historical recovery use the original fingerprint-compatible execution code
and Python environment. Do not reset/delete data, replace evidence with an older
copy, rewrite hashes/manifests, or run schema repair as rollback. A changed source
or refusal requires investigation. A restored archive is not a new execution
account. The legacy deployment/rollback scripts are not validated by this runbook.

No actual visual QA, long-duration soak, public deployment, continuously connected
feed, broker-authoritative reconciliation, Windows/filesystem portability or
live trading completion is certified here. Use the remaining evidence checklist
in [PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md).
