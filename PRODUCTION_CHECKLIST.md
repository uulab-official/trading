# Paper operating checklist and remaining live evidence

The supported paths include explicit public closed-candle capture, continuous
local paper consumption, verified non-executing backup/recovery bundles,
historical paper workflows and a read-only local observer. Follow
[MARKET_CAPTURE.md](docs/MARKET_CAPTURE.md) for collection and immutable export,
[CONTINUOUS_PAPER.md](docs/CONTINUOUS_PAPER.md) for one continuing paper account,
[PAPER_SERVICE.md](docs/PAPER_SERVICE.md) for guarded Linux pair supervision,
[PAPER_BACKUP.md](docs/PAPER_BACKUP.md) for paired backup/recovery verification,
[PAPER_RELEASE.md](docs/PAPER_RELEASE.md) for the verified minimal source release,
and [PRODUCTION_PAPER_OPERATIONS.md](docs/PRODUCTION_PAPER_OPERATIONS.md)
for exact commands, probe contracts, budgets, shutdown, recovery and rollback.
This checklist replaces the previous credential-and-live-switch deployment recipe.
No entry below is prechecked as evidence that an operator performed it.

## Before a local paper observation

- [ ] Record the exact code revision and Python version. Use the verified minimal release for its supported commands, or the existing package setup for broader monorepo workflows.
- [ ] Use operator-owned private directories (`0700`) and private files (`0600`). Keep accounts, frozen inputs, reports and evidence out of public delivery.
- [ ] For a historical run, select synthetic or explicitly supplied historical input and a **new** run directory; choose `--resumable` at creation if explicit historical recovery is required. For continuous consumption, follow the separate account checklist below.
- [ ] Confirm the intended source paths and independent source meanings. Admission reservations are not account balances or replay fills.
- [ ] Inspect a historical completed run with `paper_audit.py` and/or its persisted account with `paper_session_status.py`. Continuous accounts use `paper_stream.py check` and the stopped-owner `audit`; retain failures and all artifacts.
- [ ] Start the observer on `127.0.0.1` only. Do not expose it via public hosting, proxies, tunnels or port forwarding.
- [ ] Check liveness separately from configured-source readability using `paper_dashboard_check.py`.
- [ ] Interpret probe exit `0` as valid alive/ready, `1` as validated degraded/not_configured, `2` as arguments/transport/protocol failure, and `130` as interrupted.
- [ ] Treat unavailable data and previous-inspection warnings as unavailable/currently unverified, never empty accounts or fresh prices.
- [ ] Record resource limits and actual observations; do not call accepted limits a maximum-throughput or soak test.

## On error, maintenance or rollback

- [ ] Preserve incomplete runs and possible report publications. A publication-confirmation error is not permission to retry automatically.
- [ ] Use read-only preflight for incomplete opted-in historical runs and audit for completed opted-in runs; neither authorizes execution.
- [ ] Explicit recovery rechecks original engine/runtime fingerprints and owner locks. Saved-fill reconciliation requires the explicit recovery option after inspection; unknown intents without saved fills stay blocked.
- [ ] Stop the writer and confirm exit before preserving the whole quiescent historical directory. Preserve original files, modes and sidecars; copying only a live DB is insufficient. Continuous accounts use the separate paired-backup checks below.
- [ ] Stop the observer using SIGINT/SIGTERM; expected exits are `130`/`143`. Startup/configuration failure is `2`.
- [ ] Roll back code through a separately prepared exact pinned revision/environment, preserving data/evidence. Do not rewrite manifests, delete unknown intents or apply unverified migrations.

## Public capture operations

- [ ] Select a new private capture, fixed KRW market/unit and explicit retention; run the collector in the foreground.
- [ ] Check `/api/market-capture` or `market_capture.py check` separately from dashboard readiness. Current data does not prove owner liveness or authorize an order.
- [ ] Preserve overlap/gap conflicts, terminal provider refusals and interrupted fetching evidence. Resume refuses those states; investigate before starting another capture.
- [ ] Honor the saved retry cooldown. A stopped collector with a prior retryable error still waits conservatively on resume.
- [ ] Review window bounds and eviction counters before exporting. Retained candles are a rolling window, not a complete archival record.
- [ ] Use a new immutable export outside the capture, validate it with the original inspector and select a new historical paper run explicitly.
- [ ] Record actual provider/network/clock/disk conditions during a future soak. Controlled synthetic and loopback tests are not a provider-availability claim.

## Continuous paper account operations (v46)

Follow [CONTINUOUS_PAPER.md](docs/CONTINUOUS_PAPER.md) for complete commands,
options and fixed exit codes. These are operating checks, not preclaimed results.

- [ ] Start the capture separately, confirm `market_capture.py check` is current, and create a new account outside the capture with `paper_stream.py run --capture CAPTURE --directory ACCOUNT`.
- [ ] Ensure enough contiguous bootstrap history: at least `max(50, slow_period)` for MA or `max(50, rsi_period + 1)` for RSI, at most 500 bars. Confirm activation starts with cash only and discards every bootstrap signal.
- [ ] Record frozen source path/physical identity/configuration and the exact runtime/Python version. Creation fixes strategy, cost, risk and polling settings; resume accepts no overrides.
- [ ] Interpret fills as delayed next-open simulation: the following bar's opening price is learned after it closes. Record model event time separately from observation/commit time.
- [ ] Check the single account's atomic cursor/account/strategy/risk state with `paper_stream.py check` or GET/HEAD `/api/paper-stream`; keep these observations separate from the unchanged four-source readiness probe and full economic audit.
- [ ] Preserve freshness, identity and progress refusals. All available retained overlap, up to 5,000 capture rows, is compared against archived bootstrap/events; the 500-bar indicator window is not the overlap verification limit.
- [ ] Stop the writer and wait for exit before `paper_stream.py audit ACCOUNT`. Audit refuses an active writer. Review `verified_prefix`, preserved errors and source state before explicit `paper_stream.py resume ACCOUNT`.
- [ ] Allow startup time for mandatory full archived replay on every resume. Resume rechecks the capture after replay and refuses terminal errors regardless of saved phase; never clear a refusal by editing control metadata.
- [ ] Plan for the 100,000-consumed-bar / 256-MiB account capacity. Evidence is retained, capacity is terminal and there is no automatic archival, migration or account reset.
- [ ] Record applicable CI results for the published feature head. The configured Python 3.10/3.12/3.13 jobs are coverage targets, not evidence that the current head has already passed them.

## Verified minimal source release (v49)

Follow [PAPER_RELEASE.md](docs/PAPER_RELEASE.md). These checks remain unmarked
until performed for the intended artifact and host.

- [ ] Record the reviewed source revision, archive SHA-256 and actual published-head CI results. Configured Python 3.10/3.12/3.13 release jobs do not establish that the current head passed.
- [ ] Confirm CPython 3.10+ and the required Linux/POSIX host facilities. The seven supported tool paths need the standard library; the interpreter/OS remain external, and broader monorepo dependencies still apply outside this release.
- [ ] Build only the fixed 55 payload files plus manifest from a trusted checkout into a new archive outside the source tree. Retain the digest separately; internal hashes do not authenticate provenance.
- [ ] Supply the trusted digest to `check`, `extract` and `verify`. Confirm canonical archive validation and exact extracted-tree verification, including refusal of extra files; keep data, secrets and bytecode outside the release.
- [ ] Use new private outputs, with `0700` extracted directories and `0600` files. Preserve possible archives and partial trees after interruption or uncertainty; do not overwrite or retry automatically.
- [ ] Preserve original account/capture physical paths and existing service runtime/interpreter bindings. Inspect and explicitly create a new service configuration for a different runtime location; extraction performs no rebinding or activation.
- [ ] Assess host activation, provider/data freshness and live readiness separately. Source-release verification and legacy Rust Docker builds do not establish those outcomes.

## Guarded pair supervision (v48)

Follow [PAPER_SERVICE.md](docs/PAPER_SERVICE.md). These checks remain unmarked
until performed on the intended operator host.

- [ ] Confirm Linux parent-death signals, `/proc/self/fd`, private Unix sockets and supported local filesystem behavior on the actual host. Preserve the compatible interpreter path and checkout.
- [ ] Stop and confirm exit of consumer then capture; configure a new private service directory from the existing account. Verify the exact source pair and full preflight; do not rebind or create new trading stores through the service.
- [ ] Run the foreground supervisor explicitly. Verify current challenge-based process progress separately from native data freshness/account audit; a manager's `active` state is insufficient.
- [ ] Allow the bounded one-second service-lease admission window for transient probe contention. A prior owner may finish during this window; the explicit new invocation still requires fresh native preflight and valid previous cleanup evidence. No native operation or worker is automatically retried.
- [ ] Exercise ordered SIGINT/SIGTERM shutdown, unexpected child failure, forced cleanup and supervisor death on the target host. Preserve final outcomes and incomplete evidence; no automatic restart is configured.
- [ ] Review startup/replay, heartbeat and stop budgets against measured host behavior. The configured limits are not worst-case latency or throughput evidence.
- [ ] Preserve all bounded attempts. Missing/partial results or `cleanup_confirmed=false` block reuse; at 64 attempts, retain the directory and explicitly configure another one after inspecting original native state.
- [ ] If using systemd, inspect the generated inactive unit and independently verify installation, service ownership, manager deadlines, restart decisions and rollback. Generation and syntax tests are not host activation evidence.

## Verified backup and recovery checks (v47)

Follow [PAPER_BACKUP.md](docs/PAPER_BACKUP.md). These checks record an operator's
own execution; implementation and synthetic tests do not pre-complete them.

- [ ] Preserve the exact execution revision and fingerprint-compatible Python environment. A successful archive check does not migrate the original code, source binding or account configuration.
- [ ] Stop the continuous consumer, then the bound capture, and confirm both processes have exited. `paper_backup.py create ACCOUNT --output NEW_BUNDLE` refuses active owners and never repairs hot journals.
- [ ] Keep every bundle private (`0700` directory, `0600` files), outside the account/capture, with a new no-clobber output name. Budget for 320 MiB of payload plus up to 64 KiB of metadata per bundle, filesystem overhead and preserved incomplete copies.
- [ ] Run `paper_backup.py check BUNDLE` in the frozen runtime and record full archived replay, the stream/capture IDs, event count and payload/head digests. A check is read-only and does not assert current data or service readiness.
- [ ] Run `paper_backup.py verify-restore BUNDLE --output NEW_RECOVERY_BUNDLE`; compare the preserved archive identity and digests. The result contains non-executing `.bin` payloads, not executable account/capture database names.
- [ ] Treat `execution_authorized=false`, `source_rebinding=false` and `rollback_protection=false` as operating limits. A valid old backup may omit later state or refusals; do not infer latest-state authority or rename payloads into a runnable account.
- [ ] Preserve original inputs and possible output after exit `2`, `4`, `130` or `143`. Do not clear errors, overwrite partial bundles or delete unknown artifacts as an automatic retry policy.
- [ ] Run `python tools/paper_backup_drill.py --directory NEW_PRIVATE_DIRECTORY` for a separate synthetic, network-forbidden drill and retain its private evidence. It does not accept an existing trading account or replace a real backup-media/host-disaster exercise.

## Evidence still required for any future live service

Configuration completeness is not operational readiness.
`ConfigManager.is_live_configuration_complete()` checks settings only;
`is_production_ready()` returns `False`. Config CLI validation retains its
settings-validation exit codes and always describes operational readiness as
unverified. Dashboard readiness checks readability of local sources only.

The following still require implementation where absent and recorded
operational verification before a live service could be assessed:

- [ ] Real operational verification of the implemented public capture continuity, timestamp/freshness, gaps, cooldown and restart rules; the current evidence is controlled.
- [ ] Real operational soak for the implemented continuous paper account, including source-retention loss, frozen-runtime restart, capacity and unconfirmed commit handling. Controlled local backup/recovery verification does not establish unattended service readiness.
- [ ] Host deployment and operating evidence for the implemented Linux pair supervisor: actual Unix socket probes, manager/cgroup cleanup, interruption, attempt retention and manual recovery decisions. Controlled process tests and inactive unit generation do not activate or certify a service.
- [ ] Real backup-media transport, retention and recovery evidence on the supported operating environment. Executable host-disaster restore or source migration still needs a reviewed latest-state/retirement/ownership contract; v47 recovery copies do not transfer execution authority.
- [ ] Authoritative broker/account reconciliation, partial fills, ambiguous submission outcomes, idempotence and settlement across restart.
- [ ] Operational risk controls and stop behavior against actual execution; modeled historical/continuous paper gates do not guarantee a maximum loss.
- [ ] A reviewed credential/permission boundary and deployment architecture appropriate to the intended service. The current observer is not an authenticated public web server.
- [ ] Reproducible dependency/build and deployment/rollback evidence for the actual runtime, separate from legacy Docker/Rust scripts.
- [ ] Long-duration soak, resource measurements, alert delivery, real storage-failure drills and deployment/recovery decisions on the supported operating environment.
- [ ] Actual browser visual/accessibility and end-to-end verification, with results recorded for the supported release.
- [ ] Explicit scope for OS/filesystem portability and concurrent service ownership.

No live broker connection, credentials, external notification, public deployment,
actual visual QA or soak completion is implied by this checklist or synthetic
tests. Preserve the evidence and record unresolved findings before deciding any
future scope.

## Linux PAPER host capability preflight (v50)

The read-only/inert host capability probe is documented in
[docs/PAPER_HOST_PREFLIGHT.md](docs/PAPER_HOST_PREFLIGHT.md).

- [ ] On the **target host and intended filesystem**, prepare a new dedicated empty private directory (0700) outside accounts, captures and service stores.
- [ ] Run `python3 -I -S -B tools/paper_host_preflight.py --directory /absolute/private/probe`; retain the JSON result, host details and Python version.
- [ ] Verify all seven Linux/filesystem/IPC observations pass. A pass is point-in-time capability evidence **only**, not production, broker, data, or service readiness.
- [ ] Independently verify actual service start/stop, cgroup and parent-death cleanup, real network/provider/clock/disk soak, alert delivery and disaster recovery before any unattended operation.
- [ ] Keep the v49 source-release inventory unchanged; this v50 checkout tool is not part of the 55-file v49 runtime archive.

## Read-only PAPER operations monitor (v51)

Follow [PAPER_MONITOR.md](docs/PAPER_MONITOR.md). This is a one-shot inspection;
no notification delivery, schedule or service restart is created automatically.

- [ ] On the operator host, invoke `tools/paper_monitor.py` with exact absolute service, capture and account paths. Require all three probes for an aggregate observation of worker progress, saved market data and the account.
- [ ] Check the bounded JSON result and nonzero exit code. A missing, timed-out, malformed or unhealthy native result is not a healthy observation.
- [ ] Independently connect a reviewed scheduler/alert delivery system and demonstrate delivery, acknowledgement, deduplication, escalation and recovery notifications before claiming operational alerting.
- [ ] Validate alerting against real host/network/disk/provider faults and record evidence. Local unit tests and CI do not establish operational reliability.
- [ ] Do not treat an observed `healthy` value as full replay verification, market data authenticity, proof of profitability or production readiness.

## Private monitoring event history (v52)

Follow [PAPER_MONITOR_EVENTS.md](docs/PAPER_MONITOR_EVENTS.md) for the local,
explicit record/check commands. This component does not send any alert.

- [ ] Persist an actual completed full three-source v51 monitor JSON snapshot to a separate owner-only (0600), non-symlink file; do not submit an incomplete or failed monitor-invocation response as a valid observation.
- [ ] On a supported local filesystem, create an owner-only 0700 journal directory outside all trading stores. Record an observation and verify a private SQLite WAL history, unique transition sequence and repeated-observation deduplication.
- [ ] Verify normal-to-fault, fault-detail-change and fault-to-recovery transitions, and test multiple concurrent writers plus init-lock timeout on the intended operator host.
- [ ] Back up the entire quiescent SQLite monitor journal. No automatic eviction is performed after 10,000 distinct transitions; preserve evidence and explicitly plan capacity management.
- [ ] Integrate a separate, approved delivery/acknowledgement system. The event history is NOT evidence that any alert was delivered or read; no production readiness follows from it.

## Explicit local alert review and ACK (v53)

Follow [PAPER_ALERT_QUEUE.md](docs/PAPER_ALERT_QUEUE.md). These are unfulfilled
operator-host acceptance checks, not claims of active monitoring or delivery.

- [ ] Record a complete, consistent service+capture+account v52 monitor observation on the intended host before syncing the local alert queue.
- [ ] Run `paper_alert_queue.py sync` in the private monitor journal and inspect `list`. Confirm repeated sync preserves a single event per transition, and a later event requires resync before list/ack.
- [ ] ACK a specific event with an explicit operator reason. Confirm duplicate ACK and unknown event IDs are refused. An ACK means reviewed, **not** service repaired.
- [ ] Verify fault, changed-fault, and recovery transitions, concurrent monitor writes and queue sync/ack on the intended host, and restoration from an external backup containing SQLite WAL metadata.
- [ ] Separately establish real notification delivery, authentication, human acknowledgement and escalation; the queue has no external notifier or customer messaging.
- [ ] Keep PAPER-only and production-readiness false until long soak, host operating, real-provider data and authoritative broker risk/reconciliation gates are satisfied.

## Atomic three-source monitoring tick (v54)

Follow [PAPER_MONITOR_TICK.md](docs/PAPER_MONITOR_TICK.md). This is a single
operator invocation, not an installed scheduler or external notifier.

- [ ] Run `paper_monitor_tick.py` against the actual existing supervisor, its correct capture/account and the private journal location; record reviewed runtime and host context.
- [ ] Prove an unchanged healthy observation only increments the observation counter, a degraded observation exits 1 and queues a new event, and recovery creates another event.
- [ ] Cause a queue-consistency refusal during a new observation and verify no partially recorded event remains after the transaction rollback.
- [ ] Confirm that all three absolute paths refer to the **same** configured service/capture/account pair. The tick now refuses a healthy aggregate if private service-manifest path bindings differ, but this is not a substitute for a stopped native full binding audit.
- [ ] Independently test real host scheduling, actual alarm transport/receipt/ack, service stop/recovery and external backup restoration before any unattended-operation claim.
- [ ] Keep live execution and production-ready status false; a passing local journal operation does not validate broker execution or financial risk control.

## Read-only monitoring freshness acceptance (v55)

See [PAPER_MONITOR_GATE.md](docs/PAPER_MONITOR_GATE.md). This is a local
point-in-time inspection; no recurring check or external alert delivery is
configured by this implementation.

- [ ] Run the actual three-source monitoring tick at a documented interval on the target host; verify the v52 journal and v53 ACK queue stay synchronized.
- [ ] Run `paper_monitor_gate.py` with an explicit age threshold appropriate to the host and monitor period. Confirm an old healthy snapshot fails as stale; a future timestamp is refused.
- [ ] Cause a fault followed by recovery without ACK. Confirm the gate still reports the outstanding fault and only becomes healthy after explicit operator acknowledgement and a fresh healthy observation.
- [ ] Prove missing/inconsistent databases, unsuitable private directory permissions, host clock rollback and failed SQLite reads cannot create a false healthy result.
- [ ] Record actual externally delivered alerts and their independent acknowledgement and escalation. A local ACK history does not establish external delivery.
- [ ] Keep overall production readiness false pending real host soak, backup/restore, provider continuity and broker-authoritative reconciliation.

## Inactive systemd monitoring timer review (v56)

See [PAPER_MONITOR_SYSTEMD.md](docs/PAPER_MONITOR_SYSTEMD.md). The v56
renderer returns **unit text only**; source/CI tests do not install, enable
or activate any systemd timer.

- [ ] On the intended operator host, inspect the exact Python interpreter, reviewed v54 monitor tool and service/capture/account/journal paths; no symlink or path migration assumptions.
- [ ] Render and independently review the systemd user service/timer, then run `systemd-analyze verify` on the actual intended unit files and test the host's supported user-manager security directives.
- [ ] Confirm the monitoring interval fits the actual three native probe timeouts and operator resource budgets; an unhealthy tick exits nonzero without restarting workers.
- [ ] If a timer is later manually installed, verify systemd ownership, failures, missed ticks, monitor gate staleness, clock shifts, boot/logout behavior and rollback/disable on the actual host.
- [ ] Implement and independently verify delivered alert transport, acknowledgement, escalation and external backup restoration. A rendered timer is not operational monitoring acceptance.
- [ ] Keep broker execution disabled and production readiness false until provider, host and brokerage gates are independently satisfied.

## Offline monitoring incident and recovery acceptance (v57)

See [PAPER_MONITOR_DRILL.md](docs/PAPER_MONITOR_DRILL.md). This is **synthetic**
contract validation against the actual v52/v53/v55 implementation and is
not evidence of delivered alerts or production host availability.

- [ ] Run `python3 -I -S -B tools/paper_monitor_drill.py` in the reviewed checkout; require a zero exit, `verified_synthetic`, exactly three transition events and five completed stages.
- [ ] Confirm a repeated healthy observation increments count without creating a new transition and remains fresh. The v55 gate must not demand identical latest-observation and last-transition timestamps.
- [ ] Confirm the gate refuses the unsynchronized queue after a new fault, reports an unacknowledged fault after recovery, and returns healthy only after the explicit simulated ACK.
- [ ] Verify the duplicate ACK is refused and no real account, capture, broker, provider, external alert, systemd timer or public network resource is used.
- [ ] On the **actual operating host**, independently repeat the corresponding real incident, stale tick, alert delivery/receipt, backup restore and rollback drills before accepting unattended PAPER operations.
- [ ] Continue refusing live trading until broker-authoritative order/fill/balance reconciliation and real operational risk controls are independently verified.
