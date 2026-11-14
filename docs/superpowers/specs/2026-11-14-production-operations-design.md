# Production operations hardening design — v44

## Intent

The user asks to continue to production quality. Improve the implemented local
paper/research operating path until its current resource, lifecycle and evidence
claims match tested behavior. Base: 2c3bcf288deb62160c09c85fba195636e8e6afb0.
This is an engineering hardening batch, not evidence of a live trading launch.

## Approach and decisions

Considered: extend the legacy Rust/live deployment, add a second web framework,
or harden the existing supported Python observer and research I/O. Choose the
third: it preserves current account/engine contracts and immediately closes
reproduced operational defects. The choice is the implementer's under the user's
continuation instruction, not a separately claimed design approval.

The local dashboard remains a loopback-only operator utility. Python's
http.server is not recommended for public production serving:
https://docs.python.org/3.12/library/http.server.html. The bounded local runtime
is not an authenticated public web service. An externally accessible product
needs a separately designed production HTTP/authentication boundary.

## Supported runtime

Keep create_server(...) and all existing routes/response schemas compatible.
Use a bounded ThreadingHTTPServer subclass with 8 handler slots, backlog 8,
2-second socket inactivity timeout, and 10-second absolute connection lifetime.
Capacity exhaustion closes a connection before spawning a worker. Track sockets
and threads, close expired sockets even if a client trickles bytes, release slots
on every worker completion/start failure, and retain HTTP/1.0 single requests.
Tests can construct the runtime with shorter positive bounded timeouts.
Aggregate request headers are capped at 16 KiB and request lines at 4,096 bytes;
parser errors never echo arbitrary target/method/version text. Repeated close
calls must not restart the two-second request-worker drain budget.

SIGINT/SIGTERM handlers only record a stop request. A serving thread runs the
accept loop; the owner stops it from another thread, closes active sockets,
and waits at most 2 seconds for request workers. All request work is read-only.
Restore prior signal handlers. Return 130/143 for the respective signals, 2 for
startup/configuration failure, and 0 for ordinary completion. No raw paths or
tracebacks in operator errors. Existing Host/Origin/fetch-site policy, CSP and
write rejection apply to new routes. Unexpected request/parser/method errors
produce bounded sanitized responses; process interruptions are not swallowed.
Finite application/read bounds do not guarantee a hard deadline for a hung
kernel/filesystem call.

## Observer probes

Add GET/HEAD /api/liveness and /api/readiness. No query parameters.
Both use schema_version=1, source=local_paper_dashboard, mode=paper,
read_only=true, live_enabled=false, and UTC observed_at.

Liveness: scope=observer_process, status=alive, HTTP 200; no source access.
Readiness: scope=configured_source_readability; sources contains exactly
journal, replay, research, paper_session, each available/unavailable/not_configured.
Only explicitly configured sources are read, through their existing validators.
No sources => status=not_configured, HTTP 503. All configured sources available
=> ready/200. Otherwise degraded/503, retaining independent source status.
No account identifiers, paths, metrics, exception messages or trading permission.
Readiness means readable local observations, not writer liveness, market freshness,
account reconciliation, or permission to resume. /api/health remains journal-only.

Add tools/paper_dashboard_check.py: direct fixed 127.0.0.1 HTTP connection,
--port (1..65535, default 8765), --timeout (0.1..10, default 5), optional --liveness.
No URL/host/proxy/redirect support. Max response 8192 bytes and strict validation
of the fixed probe contract before success. One JSON stdout document. Exit 0 for
valid ready/alive, 1 for valid not_configured/degraded, 2 for invalid arguments,
transport/protocol/unexpected response; 130 for interruption. Errors are generic.

## Bounded journal observer

Keep one read transaction, query_only/trusted_schema settings, exact Decimal
totals, all-row validation and existing response fields. Bounds: database and
each existing WAL sidecar <=64 MiB, orders <=10,000, payload <=16,384 UTF-8 bytes,
metadata <=16 entries, returned events <=50 with bounded textual fields.
Only retain the requested order page, not every matching projection. Detect
overflows before publishing any summary; never return partial totals as complete.
SQLite progress budget 1,000,000 VM operations and 1-second monotonic work
deadline; check elapsed work during Python validation too. Validate a real
10,000-row small-payload fixture; adjust a bound only if measured evidence calls
for a documented ruling. Deep JSON/encoding/SQLite errors become generic
journal_unavailable and do not mutate the source.

## Stable local report I/O

Use shared stdlib-only primitives for research/replay report reads and the
quant_research fixture/candidate inputs: regular file, no final symlink,
nonblocking no-follow descriptor, bounded read, before/after descriptor/name
identity and parent identity. Reject changed observations and special files.
Preserve original report/input validators, byte hashes, schemas and result math.
Research fixture bound 32 MiB; candidate file 16,384 bytes; report limits retain
existing constants.

For research and replay JSON publication, preserve new-file/no-clobber semantics,
existing equal-report idempotence, accepted parent permissions and private 0600
output. Pin parent and staging descriptor, fsync bytes, publish through anchored
no-clobber link, fsync parent, and verify observed publication/parent identity.
Research equal-report collision resolution must retain the original pinned parent
through the validated observation and staging cleanup, then reconfirm that parent
before returning success. Replay retains its original collision refusal.
No success after failed final confirmation; introduce a fixed
ReportPublicationUncertain (OSError) after a possible publication. Preserve any
possible output; never auto rerun or repair. Existing CLI I/O exit 3 for research
and 4 for replay remain; uncertainty gets explicit sanitized wording before
generic OSError. Before-publication failure leaves previous files untouched.
No new research orchestration or strategy selection in this batch.

## Truthful configuration and operations documentation

Expose configuration eligibility separately from production readiness.
Legacy is_production_ready() must return false in this revision because the
runtime/feed/broker readiness is not established. Provide an explicitly named
is_live_configuration_complete() for the former configuration-only predicate.
CLI validate/show distinguish complete configuration from operational readiness,
never claim Production Ready from credentials. Configuration validation exit
behavior stays unchanged.

Replace the misleading live-switch checklist with the supported paper operating
runbook and an evidence-based remaining live-readiness list. Update README and
dashboard docs, and replace contradictory legacy dashboard guide with current
source/endpoint/limit/runbook links. No fake soak test or visual QA claim.
Document explicit historical recovery, source-specific unavailable behavior,
read-only observation, private files, backup of quiescent account directories,
probe exit semantics, and rollback by pinned code without deleting evidence.

## Verification and delivery

New correctness/resource tests run RED before product changes. Exercise actual
loopback HTTP, idle/trickle connections and subprocess signals, temporary SQLite
and report files, publication fault injection and original-engine exports.
Preserve all existing tests except intentionally changed documented contract.
Run relevant task suites, all development tests, package/server tests and Node
tests. Add new suites to existing appropriate CI jobs without weakening gates.
Independent review of the integrated increment, then exact-tree feature-branch
publication and exact-head CI, Draft PR update, final remote audit. Main stays
a5750019dcd7a980a28783258c06c7ef4706afc1. No deployment, real orders, credentials,
messages to others or browser-policy workaround is implied by development.

## Remaining evidence boundaries

No continuously connected paper/live feed, broker-authoritative reconciliation,
long-duration soak, actual browser visual QA, Windows/filesystem portability or
public multiuser service is certified by this batch. Record genuine blockers
rather than treating the new observer readiness as system-wide trading readiness.
