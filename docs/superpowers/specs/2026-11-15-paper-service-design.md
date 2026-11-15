# Supervised local PAPER service (v48)

## Intent and supported scope

Operate one existing continuous PAPER account and its exact bound public-candle
capture as a foreground supervised pair. Preserve native ownership, resume,
terminal-state and full-replay rules while adding parent-death containment,
ordered shutdown, bounded worker progress observation and retained attempt
evidence. Configuration and service-unit generation are inert.

Target Linux, Python >=3.10 and supported local POSIX filesystems. Linux parent-
death signals and `/proc/self/fd` are explicit requirements; no portable orphan-
containment claim is made. No new dependency is needed. This is engineering of
PAPER operations, not deployment activation, live trading or financial readiness.

The user repeatedly authorizes implementation, testing, feature publication and
Draft PR updates. Continue those actions without repeating a design-approval
menu. Preserve main, Draft/unmerged/no-auto-merge status and all 24 modules in
`paper_stream._engine_fingerprint()`. No provider/broker request, credentials,
external notification, browser operation or service activation during delivery.

## Approaches and implementation decisions

1. Two service-manager templates around the existing CLIs are small, but cannot
   coordinate failure evidence, ordered shutdown or parent-only process death.
2. A portable subprocess loop with advisory PID files does not establish orphan
   containment or safe detached control. It is excluded.
3. Chosen by the implementer: an additive foreground Linux supervisor, two fixed
   worker wrappers, a private read-only health socket and an inactive systemd
   user-service generator. Native APIs retain all economic and storage rules.

Initial creation of capture/account remains with existing explicit commands.
Service configuration derives the capture from the account; there is no second
independently selected capture, arbitrary command, provider URL, strategy override,
automatic retry, detached daemon or PID-file kill command.

## Interfaces and files

- `paper_service_protocol.py`: bounded canonical JSON, worker frame validation.
- `paper_service_worker.py` plus `tools/paper_service_worker.py`: Linux parent
  guard, native runtime adapters, owned-loop/progress frames and final outcome.
- `paper_service_state.py`: existing-pair preflight, private immutable service
  configuration, service lease and no-clobber attempt evidence.
- `paper_service.py`: fixed child launch, selector-driven supervision, ordered
  cleanup and fresh read-only health probe.
- `paper_service_unit.py`, `tools/paper_service.py`: inert systemd generation and
  strict configure/run/check/unit CLI.
- Tests use actual processes/signals plus native synthetic integration. Test
  injection is not exposed through production configuration or CLI.

Public APIs:

```text
async configure_service(account, directory, *, startup_seconds=180,
    heartbeat_seconds=30, stop_seconds=15, stop=None) -> dict
run_service(directory, *, stop=None) -> dict
probe_service(directory, *, timeout=1.0) -> dict
render_systemd_unit(directory, output) -> dict
```

Stop callbacks return exact integer 0/2/15. CLI handlers only record first numeric
intent and restore previous handlers. No asynchronous handler performs IO or
locking. Core exceptions: ServiceUnavailable, ServiceRefused(reason),
ServiceStorageError, ServiceInterrupted(signum).

## Configuration and preflight

Configure from a quiescent account. Derive and validate the frozen capture path,
physical identity, logical ID and configuration. Hold account audit SH then
capture SH during full archived replay and stable reads. Refuse terminal account
or capture state (including retained reasons in stopped state), uncertain
fetching, exhausted account capacity, future saved timestamps and incompatible
runtime. Existing readers never repair inputs.

`prepare_pair(account, *, expected=None, stop=None)` returns `pair` with immutable
account/capture paths, IDs, manifest digest, execution fingerprint and physical
bindings, plus `audit` with current verified-event/state/head evidence. Expected
pair comparison never freezes mutable event counts or control revisions.

The service directory is new, owned 0700 and outside both inputs. Files are owned
0600 single-link regular files. Pin parent/directory/file identities with existing
private IO primitives; do not modify them or the frozen runtime to fit this layer.
Freeze the destination parent before lengthy preflight and recheck originals and
destination after final publication sync. Preserve all possible output on error.

Exact root entries: `.service.lock`, canonical `service.json`, private `attempts`
directory. Config maximum 16 KiB. Schema version 1, source `paper_service_config`,
random 32-hex service ID, paper/live false, created UTC time, immutable `pair`,
runtime interpreter path/repository/Python implementation+major.minor and policy:
startup 10..900 seconds (default180), heartbeat5..120 (default30), stop1..60
(default15), max_attempts64, auto_restart=false. Interpreter path preserves the
virtual-environment executable name; do not resolve it to a different environment.

Each explicit run acquires the service EX lease and repeats full read-only pair
preflight before launching either worker. Account/capture native checks remain
authoritative on actual resume; this is not atomic exclusion against unrelated
direct CLI users racing between preflight and launch.

## Attempts and evidence

Attempt directories are consecutive six-digit names 000001..000064 under
`attempts`, each new/no-clobber/0700. Validate the bounded directory inventory;
never delete or silently recycle records. Capacity exhaustion requires the
operator to preserve existing evidence and configure a new service directory.
There is no mutable latest-pointer file: the greatest sequence is the latest.

Before worker launch, fsync a canonical `started.json` (<=16 KiB) containing the
service/attempt IDs, sequence, config digest, UTC time and preflight audit. A new
32-hex attempt ID also binds worker messages and live probes. After actual child
cleanup, retain `result.json` (<=64 KiB) and bounded `capture.stderr`/`stream.stderr`
(<=256 KiB each), fsync files and parents, then emit the public result. A partial
or missing final record is unconfirmed evidence, not permission to restart.
The latest attempt must contain a valid result with `cleanup_confirmed` exactly
true and both bounded stderr files before another attempt can be allocated.
Otherwise refuse with `previous_attempt_unconfirmed`; preserve that service and
configure a new directory only after inspection and the original pair preflight.
Observers may still inspect incomplete evidence, without inferring liveness.

PrivateDirectory-backed ServiceDirectory exposes manifest/config_sha256, check,
owner_present, new_attempt(audit), latest_attempt and context-managed close.
Attempt exposes path, number, attempt_id, record, directory, check,
health_address, bind_health_socket and save_result(result, stderr).
Health socket paths use the held attempt FD under `/proc/self/fd`, avoiding long
filesystem socket names and ancestor redirection. Socket is owned0600, single
link and identity checked. Stale per-attempt sockets are never reused.

## Worker protocol and native behavior

Spawn only the fixed checked-in worker entrypoint using the configured current
Python, `-I -B -u`, shell=false, stdin DEVNULL, stdout/stderr pipes,
close_fds=true and start_new_session=true. Spawn on the supervisor's persistent
main thread. Never signal a PID from saved state: use only its live Popen handles.

Before any trading runtime/store import or operation, a worker checks expected
parent PID, arms PR_SET_PDEATHSIG(SIGKILL), verifies the setting, then checks the
parent again. Failure exits without runtime entry. Parent death before arming
is caught by the second identity observation; subsequent death kills the worker.
No exec/fork/credential change follows the guard in normal workers. SIGKILL can
leave uncertain native evidence; it is not a graceful shutdown claim.

Worker stdout is newline-delimited canonical JSON frames, maximum2048 bytes per
frame. Exact common keys: v=1, attempt(32hex), role(capture|stream), sequence(int
0..2^53-1), event(guarded|owned|progress|result). First event guarded/sequence0;
sequence increases exactly by1; owned occurs at most once, after guarded;
progress may precede ownership during replay; result is last. Result adds exact
keys exit_code(0/2/3/4/130/143), terminal(bool), state/reason(null or bounded safe
snake-case labels). No path/traceback is public. Duplicate keys, bool-as-int,
unknown fields, noncanonical JSON, wrong roles/attempts or sequencing are refused.

Use native collect_capture/run_stream APIs with their existing stop and sleep
hooks. The first wrapped sleep emits `owned` only after the native writer context
has been acquired; consumer reaches this point after full resume replay and a
poll. Wrapped stop emits progress at most once per second, without changing
returned stop intent. Wrapped sleep calls real sleep with the unchanged duration.
No production monkeypatch, altered clock, synthetic client or modified store.

Ownership/progress frames do not mean current market data or an admissible order.
Retryable cooldown/waiting is valid owned progress. Preserve native terminal
reasons even when native exit130/143 also records a signal. Derive terminal from
the actual final observation; never erase it to make shutdown look successful.

## Supervisor lifecycle and limits

Start capture and require its owned-loop frame within startup budget; then start
stream and require its owned-loop frame within its startup budget. No child is
ever respawned. Per-worker startup budget includes runtime imports, capture first
acquisition/cooldown or full consumer replay. New attempts do not trust old saved
running/current observations as proof of startup.

After ownership, a missed progress deadline ends the pair with uncertainty.
Track actual child exit/EOF and validate final result against actual return code.
Any unsolicited exit, including0 or130/143, ends the pair. Native policy refusal
is retained; invalid protocol, crashes, output overflow and storage failure are
uncertain failure. Consume bounded pipe chunks using selectors; never communicate
with unbounded buffering or write synchronous runtime output to the public caller.
Retain at most256 KiB stderr per worker; overflow ends the pair and cleanup keeps
draining/discarding. Protocol pending buffer<=2048; read chunks<=4096; frame rate
<=32 per worker per rolling/one-second window. No database read in the monitor loop.

On requested stop: signal and reap stream first, then capture. Continue draining
both while waiting. Each child has its configured cooperative grace, then SIGKILL
and a separate5-second reap budget. Forced/unconfirmed cleanup is exit4. A genuine
failure or terminal refusal cannot be overwritten by a simultaneous signal.
Unexpected parent exceptions enter the same cleanup path. Parent-only SIGKILL is
contained by each worker's kernel guard and optionally the manager's cgroup.

Public results always include schema_version1, source paper_service, operation,
mode paper, live_enabled false, status, exit_code, service/attempt identity,
actual child outcomes and explicit boundaries. Exit2: invalid/unavailable before
launch. Exit3: preserved native policy/refusal. Exit4: uncertainty/unexpected
termination/forced cleanup. Exit130/143: requested cooperative stop without a
higher-priority outcome. Configure/unit/check success is0; stopped check is1.

## Fresh health, separate from financial readiness

The only socket operation is a read-only probe. A fresh random 32-hex challenge
and current attempt identity must match in the response. Request<=256 bytes,
response<=8192; at most4 concurrent clients, one-second client deadline. Verify
same-UID SO_PEERCRED. Nonblocking client IO shares the bounded monitor loop.

Probe reports current supervisor response, each direct child's observed process
state, native owned-loop entry and progress age. Healthy service observation is
only both owned, unreaped and recent. Startup/stopping/degraded is exit1; malformed,
unavailable or unresponsive protocol is exit2. Lease-free state is not_running
and never uses a cached successful response or sends a signal. Native data
freshness and account audit remain separate existing commands, with
data_readiness=not_assessed and live_execution=false. A SIGSTOPed child eventually
loses progress health even though a PID/lock still exists.

## Packaging and verification

Generate a new inactive systemd user unit from an existing service configuration.
Conservative absolute paths only (ASCII letters/digits/underscore/dot/slash/dash);
reject shell/systemd interpolation characters rather than interpreting them.
Type=simple, Restart=no, KillMode=mixed, KillSignal=SIGTERM, SendSIGKILL=yes,
SuccessExitStatus=130 143, UMask=0077, NoNewPrivileges=yes, LimitCORE=0 and
TimeoutStopSec=2*(stop_seconds+5)+5. No shell, environment file, credentials,
enable/start/deploy command or legacy Rust/Compose path is invoked by generation.
`active` in systemd is process state; use the live service probe and separate
native observations. Validate generated syntax offline with systemd-analyze.

Use real processes, OS signals and subreaper tests for parent death, native lock
release, no orphan activity, child failure, repeated signals, escalation, floods,
duplicate instances, stale progress and path substitution. A native synthetic
integration must run both unchanged engines, stop, audit, explicitly resume and
stop again; deny network inside every spawned interpreter. Keep native fixture
injection in tests only. Verify all frozen bytes and previous v47 archives.

Final gates: full unittest, package/server pytest, Node, new Python matrix jobs,
independent whole-increment review, exact-tree feature publication, final-head CI,
guarded Draft metadata update and independent final readback. Do not claim actual
service activation, real provider soak, hardware durability, executable recovery,
browser QA or live readiness from these controlled tests.

Primary references checked2026-11-15: [Python3.10 subprocess](https://docs.python.org/3.10/library/subprocess.html),
[Linux parent-death signal](https://kernel.googlesource.com/pub/scm/docs/man-pages/man-pages/+/master/man/man2const/PR_SET_PDEATHSIG.2const),
[systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html),
[systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).
