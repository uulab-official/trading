# Trading continuation handoff — 2026-11-14 (v44)

## Resume point and supported scope

Repository: `uulab-official/trading`. Continue on `feat/smc-market-structure`
and [Draft PR #1](https://github.com/uulab-official/trading/pull/1).
The verified starting head was v43 `2c3bcf288deb62160c09c85fba195636e8e6afb0`,
tree `5a20277b14aaf3f1fa7a7597042f80a8c6b6b154`. Remote inspection found no
divergence, all four prior exact-head runs successful, PR open/Draft and unmerged
with no auto-merge, and main unchanged at
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user requested progress to production quality. This increment addresses
reproduced resource, observation, file-durability and false-readiness defects in
the implemented local paper/research workflow. It does not certify a public HTTP
server, a continuously connected market feed or a live broker runtime. Existing
feature-branch/Draft PR delivery authorization applies. Main merge, deployment,
credentials and live execution remain outside this continuation. The clean
existing feature checkout and installed environment were reused.

## Delivered v44 implementation

### Bounded observer and independent probes

The supported dashboard now uses a standard-library bounded runtime with at most
eight request handlers and an eight-connection backlog. It closes an excess
connection before spawning another worker. A two-second idle timeout and a
ten-second absolute connection lifetime also cover trickled request bytes.
Aggregate headers are capped at 16 KiB and request lines at 4,096 bytes. Parser,
request and startup errors are sanitized. Existing loopback binding, Host/Origin
checks, CSP, read-only routes and HTTP/1.0 behavior remain.

SIGINT/SIGTERM handlers only record shutdown, without acquiring an Event lock.
The owner checks the request at intervals of at most 0.1 seconds, stops the serving
thread, closes active sockets, drains workers for at most two seconds and
restores previous handlers. Repeated close is idempotent. Exit 130 means SIGINT,
143 SIGTERM, 2 startup/configuration failure and 0 ordinary completion.

`GET`/`HEAD /api/liveness` reads no source and reports only observer process
responsiveness. `/api/readiness` separately validates configured `journal`,
`replay`, `research` and `paper_session` sources with the existing readers, once
per configured source. Every source remains visible as `available`,
`unavailable` or `not_configured`. All configured sources readable means
200/`ready`; any failure means 503/`degraded`; no sources means
503/`not_configured`. Paths, account identifiers, metrics and raw exceptions are
not returned. Existing `/api/health` remains journal-only.

```bash
python apps/dashboard/server.py --paper-session .private/paper-001/account.sqlite --port 8765
python tools/paper_dashboard_check.py --port 8765 --liveness
python tools/paper_dashboard_check.py --port 8765 --timeout 5
```

The CLI connects only to fixed `127.0.0.1`, without proxies or redirects. It
validates the exact bounded JSON contract, status agreement and UTC observation
time. A total network deadline also stops trickled response headers; response
bodies are capped at 8,192 bytes. Exits: 0 valid ready/alive, 1 validated degraded
or not-configured, 2 arguments/transport/protocol/wrong-service failure, 130
interruption. Readiness asserts readable saved state, not writer liveness,
market freshness, permission to resume or trading readiness.

### Bounded journal observation

The admission journal rejects a DB or any existing WAL/SHM sidecar larger than
64 MiB, more than 10,000 orders, oversized text/payloads and excess metadata.
Payload validation uses the original 16,384-byte UTF-8 bound, with a bounded SQL
projection accounting for UTF-16 databases. It validates the entire bounded order
set for exact Decimal totals while retaining only the requested order page.
Audit output remains at most 50 events. Sorting uses the raw source event ID so
a projection sentinel cannot hide a malformed newest entry.

One read transaction has a 1,000,000 SQLite VM-operation budget and one-second
cooperative monotonic budget including Python validation. Deep/invalid data and
budget failures map to generic `JournalUnavailable`, with no partial totals.
Native readonly SQLite WAL access can change SHM coordination bytes; logical DB
and WAL data are not mutated. These limits cannot preempt a hung kernel call.

### Stable report inputs and confirmed publication

A shared dependency-free local I/O module now pins file and named-parent
identity, rejects final symlinks/special files, uses nonblocking/no-follow file
descriptors and bounds reads. Replay/research retain their original 8 MiB/2 MiB
report limits and validators. Quant research fixture/candidate reads are bounded
to 32 MiB/16,384 bytes and preserve original raw-byte identity and input rules.

Report publication writes private 0600 staged bytes, fsyncs the file, uses a
descriptor-anchored no-clobber link, checks publication identity, fsyncs the
parent and confirms the final observation. Existing output is preserved.
Research equal-report collision resolution stays inside the original parent
binding through staging cleanup and final confirmation, preserving the original
generation timestamp and bytes. Replay retains its original collision refusal.

If a link may have published but final confirmation fails,
`ReportPublicationUncertain` preserves any output and emits a fixed diagnostic:
inspect saved state explicitly; never automatically rerun or repair. Original
research I/O exit 3 and replay export I/O exit 4 remain. Failed replay export can
already have advanced its checkpoint, so preserve both checkpoint and report.

### Truthful configuration and operations documentation

`is_live_configuration_complete()` retains the former settings-only predicate.
Legacy `is_production_ready()` always returns false in this revision. Config
CLI validate/show distinguish settings completeness from unverified runtime/feed/
broker readiness while preserving validation exits. Synthetic credentials in
tests are inert; no credential discovery or provider connection is involved.

[PRODUCTION_PAPER_OPERATIONS.md](PRODUCTION_PAPER_OPERATIONS.md) documents exact
create/audit/status/probe commands, source/runtime limits, explicit historical
recovery, quiescent backup and pinned-code rollback. README, dashboard/research/
replay guides and the production checklist agree on this supported path.
Legacy Rust/Compose/monitor deployment remains separate and unvalidated here.
The checklist does not mark operator, browser, soak or live gates complete.

## Verification in this continuation

| Final local regression | Result |
| --- | --- |
| Development unittest | 1,109 passed in 107.476 seconds |
| Package/server pytest | 309 tests and 382 subtests passed in 14.25 seconds; 926 existing deprecation warnings |
| Node clients | 79 passed in 209.008 milliseconds |

These final gates ran after both the collision-parent and signal-lock repairs. Complete
base-to-final-working-tree whitespace checks also passed.

- New isolated operations suites cover 22 journal, 15 runtime, 9 readiness,
  11 probe and 25 report-I/O test methods. Six configuration tests cover complete
  settings without a production-ready claim. Real sockets/subprocesses,
  filesystem fault injection and actual SQLite databases are used.
- Runtime/readiness/probe behavior was demonstrated RED before implementation.
  Added startup resource-failure and repeated-close regressions also failed
  before correction. A real SQLite alias-ordering regression failed before the
  raw-ID ordering fix. Journal UTF-16 and payload/metadata/work bounds passed.
- The initial report-helper import RED was supplemented by behavior-level RED
  against the original reader/writer: final symlink acceptance and absent parent
  fsync. Original report/research behavior remains covered by 189 tests.
- Independent whole-increment review identified one Important/P2 collision-parent
  race. Two real EEXIST/cleanup replacement tests failed before the fix; stable
  equal and conflicting collisions were retained. The narrow repair's 214
  report/replay/research tests passed. No execution-core change was needed.
- The independent reviewer then verified all four collision regressions and
  closed the finding. Final review has no Critical/Important/Minor findings and
  no declined-to-judge items, within the documented local scope.
- Initial exact-head Quant CI on `b0bda93` exposed a SIGINT timeout on Python 3.10
  and TCP reset observations on 3.10/3.11. Investigation independently reproduced
  a real signal-handler deadlock locally: `Event.set` reentered the condition
  lock held by the interrupted owner in `Event.wait`. The failed CI process has
  no captured stack, so its particular timeout is not proved to have that cause.
  Both forced SIGINT/SIGTERM cases failed before the lock-free handler repair.
  The original four-second subprocess deadlines remain; a closed socket accepts
  either EOF or TCP RST. Runtime/readiness/probe then passed 35 methods, and
  independent review passed both signal methods with no remaining finding.
- All 16 execution-fingerprinted source files remain byte-identical to v43.
  Engine, arithmetic, strategy and account/recovery identities are unchanged.
  Changed Python sources parse with Python 3.10 grammar; local runtime is 3.12.
- A 10,000-order journal measured about 0.05 seconds and 450,000 VM operations;
  a two-row projection used 11,733 Python-traced peak bytes. This is one measured
  fixture, not a throughput or service-level guarantee.
- Guarded original-engine export separately produced 12 replay fills and two
  evaluated research folds. Actual directory-fsync failure preserved replay and
  checkpoint, called the original runner once and returned export exit 4.
- The integrated synthetic operating demonstration reused its completed account,
  verified its completion audit, exported a two-fill replay and configured all
  four observer sources. Ready and liveness exited 0; corrupting only research
  produced degraded exit 1 while the other three stayed available; restoration
  returned ready 0; SIGTERM returned 143; the closed port produced probe exit 2.
  All recorded source/account/checkpoint bytes were unchanged and account/report
  modes were 0600. External network was prohibited by a loopback-only audit guard.
- The demonstration initially passed `min_training_fills=0`, correctly refused by
  the original minimum of 1 before output. Only that demo argument was corrected.
  Its resulting six research folds were all skipped; readiness validation does
  not infer selection, execution or profitability from a readable report.
- New suites are wired into existing Python 3.10/3.11/3.12 isolated CI and
  3.10/3.12 original-engine integration. Existing package coverage, server tests,
  order-safety gates and Docker/offline Rust CLI gates are preserved.

## Retained boundaries and next useful work

Observed local descriptor/metadata checks are not authenticated storage or a
sandbox against a privileged writer changing files after the final observation.
Fsync confirmation does not guarantee every hardware/filesystem failure mode.
Bounded application work does not prove sustained service capacity. Readiness
reflects independent saved-source readability; a halted account can be readable.
Historical recovery still requires its original compatible frozen execution and
explicit operator action. No automatic recovery, restart or order resubmission
was added.

Actual desktop/mobile/print/accessibility browser QA remains unverified. The
prior browser path was unavailable and local-file access was refused; no
localhost/alternate-browser workaround was used. HTTP and structural tests are
not visual QA. No long-duration soak, Windows/other-filesystem certification,
prospective feed, broker-authoritative reconciliation or live trading is claimed.

The next production milestone requires an explicit supported runtime/provider
architecture, live-market timestamps and reconnect/gap handling, authoritative
broker/account reconciliation, and controlled deployment/monitoring/soak evidence.
The legacy Python/Rust/Compose paths cannot be treated as one integrated service
without that work. Build on the original durable outcomes and explicit recovery
contracts; do not create parallel account/strategy validators or infer live
readiness from settings or historical simulation. Actual visual QA still needs
an authorized available browser path.

Design: [v44 spec](superpowers/specs/2026-11-14-production-operations-design.md).
Plan: [v44 plan](superpowers/plans/2026-11-14-production-operations.md).

## Delivery verification

Publish only to the existing feature branch with an expected-head fast-forward
check and compare the remote tree with the verified local tree. Keep PR #1
Draft/open/unmerged with no auto-merge and main unchanged.

The initial published v44 checkpoint was `b0bda932eb1d5eae6fe2e7e53e633ec7dc4b371c`.
Its failed Quant jobs prompted the signal repair above; it is not a completed
delivery. Publish the corrected tree as a non-force child of that head, preserving
the earlier verified v43 parent and the initial v44 history.

Final publication and exact-head CI follow this handoff commit. On resumption, inspect
the actual PR head and its Quant Regression, Order Outcome Safety and CI/CD runs;
previous-head success is not evidence for a later commit. Local implementation
checkpoint `718e9b4` precedes the reviewed collision fix `8d3d74c` and handoff; connected GitHub
publication may consolidate local history into a different remote commit.
Final delivery results are recorded in the current PR description, head-filtered
checks and ignored local delivery receipt.
