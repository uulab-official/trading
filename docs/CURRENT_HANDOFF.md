# Trading continuation handoff — 2026-11-15 (v48)

## Current scope and delivery

Repository `uulab-official/trading`, feature `feat/smc-market-structure`,
[Draft PR #1](https://github.com/uulab-official/trading/pull/1).
Starting feature commit `0a1fd5579471a8ad8773ae1be38f3ede8b7bcad4`, tree
`20f1368f708d1e7e089f9e3d712a2bade1ba487a`. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user repeatedly requested production-quality continuation. v46 implemented
continuous PAPER accounts and v47 added verified paired archives and non-executing
recovery copies. v48 adds a Linux foreground supervisor for one existing account
and its exact bound capture, fresh process-progress probes, bounded retained
attempt evidence and inactive systemd user-unit generation.

Code, tests, feature publication and Draft title/body updates are authorized.
Main merge, activation/deployment, credentials, provider/broker requests, live
orders, external notifications and browser operations are outside this delivery.
The independent starting audit confirmed 33 previous ref/metadata/CI checks.
Reread actual refs and PR on continuation: this tracked document is a tested-tree
checkpoint, not a self-referential future commit identity.

## Compatibility and authority

All 24 files included by `paper_stream._engine_fingerprint()` stay byte-identical
to v47/v46. The accounting, strategy, stream and capture modules remain unchanged.
The existing v46 account still verifies 120 events; the previous v47 backup and
recovery copies still fully verify without changing their bytes. Current Python
3.12 execution fingerprint is
`1e2444790777873c87a4f73fb591f544021bf9f1fc9650997f293c45d221e492`.

The supervisor derives its capture from the account manifest. It cannot create,
rebind, migrate or restore executable stores, override strategy/cost/risk, supply
an alternate provider URL or run an arbitrary command. Full archived replay and
native terminal/source rules remain authoritative. A clean process observation
never grants data or execution readiness. Existing readiness remains false.

## Implementation map

| Module | Responsibility |
|---|---|
| `paper_service_state.py` | Read-only pair preflight, immutable private configuration, exclusive service lease, bounded no-clobber attempts |
| `paper_service_protocol.py` | Strict canonical bounded JSON, identities, sequences and latched frame validation |
| `paper_service_worker.py` | Parent-death guard before native entry, unchanged native stop/sleep adapters, owned/progress/final frames |
| `paper_service.py` | Fixed child launch, bounded selector loop, cleanup, actual child outcomes and fresh health probe |
| `paper_service_unit.py` | Private inactive systemd unit, conservative path grammar, final config recheck |
| `tools/paper_service.py` | Strict configure/run/check/unit CLI with one private JSON result |
| `tools/paper_service_worker.py` | Fixed internal worker entrypoint; no injection flags |

### Preflight, configuration and evidence

Configuration pins the destination parent before full native audit. It holds the
account audit SH owner then capture SH owner, checks immutable source identity,
full engine replay, terminal/retry evidence, current capacity, compatible engine
fingerprint and future saved timestamps. No reader repairs journals or inputs.
The new service root has exactly `.service.lock`, `service.json` and `attempts`;
owned directories are 0700 and files 0600/single-link/no-follow.

Configuration is canonical and at most 16 KiB. It records a random service ID,
paper/live-false, original pair bindings, current interpreter path/repository/
Python implementation+major.minor, and fixed policy. Interpreter paths preserve
a virtual environment's executable name. Policy: startup10..900/default180,
heartbeat5..120/default30, stop1..60/default15, max_attempts64, auto_restart=false.
Run requires the recorded current runtime exactly and repeats full preflight
under the service EX lease before any worker starts. Passive inspection holds no
persistent shared service lease; its brief owner probe can momentarily hold SH.
EX admission waits up to one monotonic second for genuine flock contention,
checking the pinned root/lock/config/attempts bindings and numeric stop intent
before retries and after acquisition. Other errors are not retried. No native
preflight, attempt or worker starts during this wait. A previous owner that ends
inside the window can hand over to this explicit invocation, still subject to
fresh native preflight and previous-attempt validation.

Attempts are consecutive 000001..000064 with unique IDs, no mutable latest
pointer and no recycling. Before launch, `started.json` (<=16 KiB) is fsynced.
After child cleanup, bounded capture/stream stderr (<=256 KiB each) and
`result.json` (<=64 KiB) are published without clobbering, synced and checked.
Public output follows confirmation; possible partial output is preserved on
failure. The last attempt must have a valid result, exact-true cleanup_confirmed
and both stderr files before another attempt is allocated. Otherwise
previous_attempt_unconfirmed refuses reuse. Inspection plus a new service
configuration does not bypass original native terminal/preflight rules.

Physical bindings are rechecked throughout. Configuration retains its newly
created attempts-directory descriptor through final sync. Attempt allocation
transfers the originally created directory descriptor into the retained Attempt;
it never closes then trusts a reopened replacement. Socket inodes and all
original names are checked while their descriptors remain held.

### Guarded workers and bounded supervision

Only the fixed Python worker is launched with `-I -B -u`, stdin DEVNULL,
stdout/stderr pipes, close_fds and a new session. Spawn occurs on the persistent
main thread. Before native imports/actions, a worker verifies expected parent,
arms Linux PR_SET_PDEATHSIG(SIGKILL), reads it back, and verifies parent again.
No saved PID is used as signaling authority. Worker runtime stdout goes to stderr;
only bounded validated frames are written to stdout.

Capture must emit owned before stream launch; stream emits owned after its native
writer acquisition, full resume replay and first poll. The first native sleep
callback provides this attestation and calls real sleep with unchanged duration.
Stop callbacks preserve numeric signal intent and emit progress at most once per
second. Native cooldown/waiting can be live progress without current market data.

The supervisor never respawns. Startup/progress deadlines, unexpected exits
(including0/130/143), invalid protocol and output overflow end the pair. Pipe
chunks are <=4096 bytes, frames <=2048, frame rate <=32 per second; pending frame
storage and stderr are bounded. No database reads occur in the monitor loop.
A read-only same-UID Unix socket handles at most four clients, one second each,
requests<=256 and responses<=8192. It uses a fresh challenge and current attempt,
with no cached success or mutable heartbeat file. Held directory FD paths avoid
long Unix path limits and ancestor redirection.

On shutdown the consumer is signaled/reaped before the capture; both outputs
continue draining. Each has its cooperative grace, then SIGKILL and a separate
five-second reap budget. A received final frame is given exit grace without
another signal, preventing restored worker handlers from destroying an already
reported native refusal. Actual return codes must match final protocol outcomes.
Forced/unconfirmed cleanup and storage/protocol faults take precedence over
native refusal and operator signals. Parent-only SIGKILL is contained by the
kernel guard; abrupt native state remains subject to original preflight rules.

### CLI and inactive unit

```text
configure ACCOUNT --directory SERVICE [--startup-seconds N --heartbeat-seconds N --stop-seconds N]
run SERVICE
check SERVICE
unit SERVICE --output NEW_FILE
```

One JSON on stdout, fixed private errors and numeric first-signal intent. No help
or argument abbreviation output bypasses the JSON contract. Exit0: configure/unit
or fresh running probe;1: valid nonrunning/degraded probe;2: unavailable before
launch/invalid probe;3: policy refusal;4: failed/unconfirmed cleanup/evidence;
130/143: requested cooperative stop without a higher-priority outcome. A broken
or full stdout cannot deliver JSON; after cleanup the CLI reports only a fixed
stderr label where possible and returns4, preserving retained evidence.

The generated unit is never installed or activated. Paths use only conservative
ASCII absolute characters. Type=simple, Restart=no, KillMode=mixed,
KillSignal=SIGTERM, SendSIGKILL=yes, SuccessExitStatus="130 143", UMask0077,
NoNewPrivileges=yes, LimitCORE0, TimeoutStopSec=2*(stop_seconds+5)+5. There is no
Install section, boot/login activation policy, arbitrary shell or environment
file. Syntax checking with systemd-analyze is separate from host activation.

Complete operator workflow: [PAPER_SERVICE.md](PAPER_SERVICE.md).
Account semantics: [CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md).
Backup semantics: [PAPER_BACKUP.md](PAPER_BACKUP.md).

## Verification and review checkpoint

A fresh independent review covered the complete v48 change and reported
zero Critical, zero Important and one Minor finding. The Minor was reproduced
in eight actual subprocess cases: full/broken stdout after invalid arguments or
a completed refusal/failure/interruption. Final output OSError previously returned
exit1 and exposed a traceback; the repaired CLI closes the failed buffer, emits a
fixed private stderr label when possible, and returns4 without repeating work.
All28 CLI/unit tests passed after repair (8.571 seconds). The reviewer independently
ran all19 monitor tests successfully. The final whole-suite result is recorded
in the table below for the repaired implementation.

The first published candidate `9129927ce678e432da8222b6fba04cfb2b813c3d`
exposed an admission race in actual-socket Python3.12 CI: two startup tests
returned unavailable while all other service tests and five other service matrix
jobs passed. An actual separate-process observer SH barrier reproduced real
flock errno11 and the resulting refusal. Bounded EX admission repairs that cause;
there is no native-operation retry. Eight new regressions cover overlap,
long-held ownership, descheduling beyond the deadline, lock replacement,
non-contention errors, actual signals during/after acquisition and exact stop
validation. An independent repair review found no issues and independently ran
all eight successfully in3.002s. The author's complete46-method state gate passed
44 methods with the same two local socket-capability skips. The earlier failed
CI remains recorded and cannot substitute for repaired-head CI.

Final local gates on the repaired implementation:

| Gate | Observed result |
|---|---|
| Complete unittest discovery | 1,508 methods: 1,505 passed, three explicit AF_UNIX capability skips; 274.005 s |
| New service methods, included above | 143 methods: 140 passed, the same three capability skips |
| Existing package/server pytest | 309 passed + 382 subtests; 926 existing deprecation warnings; 14.36 s |
| All Node client tests | 103 passed, zero failed/skipped; 285.247399 ms |
| Python 3.10 syntax | All 14 new Python files parsed |
| Frozen runtime compatibility | All 24 execution files byte-identical to v47/v46 |
| Prior native account/archive compatibility | Existing 120-event account and v47 backup/recovery copies fully verified; bytes unchanged |
| Actual native pair lifecycle | Two runs preserve one account, 2→4→6 events, original full audits and exact economic/event prefix |
| Actual parent-only SIGKILL | Both native children reaped, both native locks released, databases stable; incomplete attempt cannot be reused |
| Inactive systemd unit | Actual offline systemd-analyze verification passed; no manager activation |

The local environment does not have flake8 installed; strict lint remains an
actual final-head CI gate. No local lint pass is inferred from compilation.

Local executor capability: AF_UNIX socket creation returns EPERM. No permission
escalation or alternate network transport is used. Actual socket tests explicitly
report that limitation. Coordinator process/signal tests can use a test-only inert
pipe listener and test observations; they establish no real health-socket result.
The GitHub Linux service steps set PAPER_SERVICE_REQUIRE_SOCKET=1, making any
socket capability skip fail the job. All spawned integration interpreters deny
provider/network operations; synthetic native transport exists in tests only.

Evidence is retained in the ignored v48 progress/task logs. Final CI must include
actual service socket/probe tests on Python3.10/3.12/3.13, alongside the existing
account/archive regressions, package/server suite, Node and strict lint/build.

## Remaining production evidence

Implemented scope is guarded local PAPER supervision plus previous native
accounts and inert archive recovery. The following remain outside completed
operational evidence: actual host service activation/rollback and cgroup behavior;
real-provider/network/clock/disk soak; alert delivery; external backup media and
host-disaster exercises; executable source/account migration with authority
transfer/retirement/rollback policy; worst-case100,000-event replay and capacity
archival; broker-authoritative execution/reconciliation; browser visual and
accessibility inspection. Legacy Rust/Compose builds do not package this Python
service or establish its deployment readiness.

Keep main and Draft/unmerged/no-auto-merge state. Publish only an exact tested
tree through expected-head non-force update. Inspect final-head PR and push CI
jobs/logs; update only guarded title/body; independently reread refs/tree/metadata
and actual CI. Do not call implementation completion service activation or live
readiness.

Design: [v48 spec](superpowers/specs/2026-11-15-paper-service-design.md).
Plan: [v48 plan](superpowers/plans/2026-11-15-paper-service.md).
