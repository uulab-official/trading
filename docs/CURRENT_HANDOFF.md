# Trading continuation handoff — 2026-11-15 (v49)

## Current scope and authorized delivery

Repository `uulab-official/trading`, feature `feat/smc-market-structure`,
[Draft PR #1](https://github.com/uulab-official/trading/pull/1).
This increment starts from feature `19f2f64767535ee473742d3b2d34bc4b7cd45543`,
tree `908d9efc7d69426cb781fb82107620efc6080a6c`. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

v46 introduced continuous PAPER accounts, v47 verified paired backup and
non-executing recovery, and v48 guarded Linux supervision. v49 packages their
actual standard-library source runtime into a deterministic, externally verified
release and exercises the extracted code independently of the checkout.

Code, tests, feature publication and guarded Draft title/body updates are
authorized. Main merge, host activation/deployment, credentials, provider/broker
requests, live orders, external notifications and browser operations remain
outside this delivery. Production/data/live readiness remain unassessed/false.
This tracked file describes the tested tree; exact future commit, artifact and
final CI identities belong in the PR and delivery receipt, avoiding a
self-referential follow-up commit.

## Recovery and compatibility

The previous scratch checkout was absent at continuation. A fresh clone recovered
the authoritative v48 source and Draft PR. An independent remote audit confirmed
the feature/main refs, tree/parent, unchanged title/full body and all four baseline
workflow runs: 19 jobs/checks, 17 success and only image publication/release jobs
skipped. Earlier scratch-only v46/v47 fixtures were not recovered. Their previous
results remain historical PR evidence; this turn does not claim to have reread
those missing files.

Every one of the 24 files in `paper_stream._engine_fingerprint()` remains
byte-identical to starting feature `19f2f64767535ee473742d3b2d34bc4b7cd45543`.
The Python 3.12 execution fingerprint remains
`1e2444790777873c87a4f73fb591f544021bf9f1fc9650997f293c45d221e492`.
The v49 tests create a new native synthetic baseline, then use the extracted
runtime to audit its original 120-event account and both backup/recovery copies,
checking that all original bytes stay unchanged. A separate extracted-runtime
drill reproduces 240 source bars, 120 bootstrap bars, 120 consumed/audited events
and 18 fills, with exact original engine and recovered payload comparisons.

The source release does not migrate account/source authority. Frozen engine
compatibility still depends on Python implementation and major/minor. Existing
service configurations retain exact repository and interpreter paths. A service
configured from the checkout is refused by the relocated runtime before native
preflight, attempt allocation or worker launch; create a separately inspected new
configuration rather than editing its metadata.

## Implementation map

| File | Responsibility |
|---|---|
| `apps/server/trading_bot/paper_release_format.py` | Fixed 55-file profile, canonical manifest/USTAR serialization, bounded raw validation, nonimporting execution fingerprint |
| `apps/server/trading_bot/paper_release.py` | Descriptor-pinned build/check/extract/verify, private new outputs, exact-tree observation, preservation after uncertainty |
| `tools/paper_release.py` | Strict four-command JSON CLI, first numeric signal intent, fixed private failures, stdout delivery handling |
| `tests/test_paper_release_format.py` | Independent USTAR fixtures, exact schema, malformed archive and size/canonicality boundaries |
| `tests/test_paper_release.py` | Actual filesystem, substitution/permission races, modes, writes/syncs/signals and descriptor cleanup |
| `tests/test_paper_release_cli.py` | Arguments, dispatch, report validation, real signals and failed stdout |
| `tests/test_paper_release_runtime.py` | Off-checkout imports, native compatibility/recovery, fixed worker, relocated configuration and complete extracted service suite |
| `.github/workflows/paper-release.yml` | No dependency installation; isolated Python 3.10/3.12/3.13 verification and source artifact retention |

The unchanged native service implementation remains documented in
[PAPER_SERVICE.md](PAPER_SERVICE.md). Complete release commands and host/trust
requirements are in [PAPER_RELEASE.md](PAPER_RELEASE.md).

## Release contract

```text
build --output NEW_ARCHIVE
check ARCHIVE --sha256 EXPECTED_SHA256
extract ARCHIVE --sha256 EXPECTED_SHA256 --directory NEW_DIRECTORY
verify DIRECTORY --sha256 EXPECTED_SHA256
```

Every read/extract/tree verification requires an externally supplied 64-character
lowercase SHA-256. Internal hashes establish consistency, not authenticated
provenance. Use a trusted verifier and retain the reviewed commit and digest from
trusted CI evidence; an untrusted archive cannot authenticate its own expected
hash.

The fixed payload contains 42 Python package modules, seven tools, five guides
and the license, plus `paper-release.json`. The manifest comes first, followed by
55 sorted payload members. Only canonical uncompressed USTAR regular files are
accepted: mode0600, uid/gid/mtime0, empty owner/group/link names, zero padding and
one exact 10240-byte archive boundary. No directory/link/PAX/GNU/sparse members,
compression, traversal, duplicates, unknown files or trailing data. Bounds are
8MiB per archive, 1MiB per payload and 64KiB per manifest. Canonical JSON refuses
duplicate keys, unknown fields, bool/int substitutions, floats/nonfinite values,
oversized integers and excessive nesting.

Build reads the fixed code root only. Payload bytes determine archive bytes;
source timestamps, paths, umask and Python patch version do not enter the archive.
The manifest states CPython>=3.10, Linux and no third-party runtime dependencies.
It does not package the interpreter, standard library, operating system, tests,
repository history, credentials or trading stores. The legacy Rust image and
full monorepo dependency setup remain separate.

Filesystem operations retain original ancestor/file descriptors, use no-follow
nonblocking opens and compare identity/content metadata before and after reads
and final syncs. Output parents are pinned before source/archive verification.
New destinations only, outside the trusted checkout and archive; no overwrite or
reuse of existing empty directories. Extraction uses0700/0600, writes manifest
last, confirms file/directory/parent sync and exact final inventory, and never
removes partial output. Restrictive-umask permission repair uses the originally
pinned Linux O_PATH descriptor through `/proc/self/fd`, never an unpinned name.
Ancestor traversal is iterative, keeping deep invalid paths an ordinary refusal.

The synchronous API closes descriptors before returning a point-in-time report.
JSON serialization and future execution are later events; there is no atomic
verification-to-execution claim. CLI exit0 confirms this operation,2 refuses
unavailable/invalid input before output,4 preserves storage/output uncertainty,
and130/143 represent requested interruption unless a higher-priority failure is
known. Broken/full stdout returns4 with a fixed stderr label and does not repeat
the operation. Always use `-B`; extra bytecode or other files invalidate an exact
release tree. Keep stores and generated reports outside it.

## Verification and review checkpoint

The first scoped independent review reproduced one Important defect: under a
restrictive umask, a pathname-based chmod could modify a substituted directory
before refusing its inode mismatch. The regression first failed with the unrelated
victim changed0755→0700; descriptor-bound repair preserves0755 and refuses the
substitution with storage uncertainty. A second Minor finding reproduced a raw
RecursionError for a deeply nested unavailable path; iterative traversal now
returns the fixed pre-output unavailable result without leaking descriptors.

The off-checkout harness reuses all143 existing service tests and only their
required test helpers. Its project paths resolve to the extracted release; no
production source is copied from the checkout into the harness. The unit test's
exact ExecStart expectation uses the resolved extracted tool path. Parent tests
run `-I -S -B`; actual native workers retain their existing `-I -B -u` argv.
Nonisolated test subprocesses inherit bytecode suppression, while the independent
complete-module import probe disables site processing and checks every project
module origin. A missing packaged module is explicitly refused despite an
adversarial PYTHONPATH pointing to the checkout.

Actual fixed stream launch is separately exercised through the extracted
supervisor's original `_spawn_worker`: guarded/owned frames, held native account
lock, SIGTERM, reaping, lock release and full native audit. The extracted suite
also covers two synthetic native runs on one account, exact economics, ordered
pair stop, health challenges and parent-only SIGKILL containment. Synthetic
transport and network denial remain test-only; no provider request is made.

Final local gates on the reviewed implementation:

| Gate | Observed result |
|---|---|
| Complete root unittest discovery | 1,566 methods: 1,563 passed, three explicit AF_UNIX capability skips; 377.001 s |
| New release methods, included above | 58 passed; the nested extracted service suite additionally ran 143 methods, 140 passed and the same three capability exclusions |
| Standalone isolated runtime suite | Eight passed in 101.968 s, including exact-source imports, original native account/archive checks and the fixed worker |
| Independent focused review gate | 50 format/filesystem/CLI methods passed with `-I -S -B`, zero skips |
| Package/server pytest | 309 passed plus 382 subtests; 926 existing deprecation warnings; 14.33 s |
| All Node client tests | 103 passed, zero failures/skips; 313.366459 ms |
| Python 3.10 syntax | All seven new Python files parsed |
| Frozen source comparison | All 24 execution files byte-identical to starting v48 feature |
| Local release delivery | 706,560-byte archive; all 55 payloads plus manifest checked, extracted and exact-tree verified |
| New workflow configuration | Python 3.10/3.12/3.13, no dependency install, pinned action commits, read-only token, actual sockets required |

The local release archive SHA-256 is
`a8c22a6c057c1284a705ce2ff90c41362148d5c9eee20d62f8f71f8930cd0da9`.
The payload totals 651,789 bytes; manifest SHA-256 is
`d87513f61df21763a05e80e849cf090ff1e22c6e60a03789366b60387dec02fd`.
The source bytes were frozen during final gates. Independent full-increment
review found no remaining Critical, Important or Minor finding after the two
repairs. Final-head CI, artifact retention, guarded publication/metadata and
independent remote readback are recorded in the PR and delivery receipt. The
local results above do not preclaim those future results. Strict lint and the
unchanged Rust Docker build remain actual final-head CI gates.

## Remaining operational evidence

The new source release closes the gap between repository tests and a separately
verified minimal PAPER runtime. It does not establish target-host installation,
activation/rollback/cgroups, real-provider/network/clock/disk soak, alert delivery,
external backup media, host-disaster recovery, executable account migration and
authority transfer, worst-case100000-event replay, broker-authoritative execution
or reconciliation, or browser visual/accessibility acceptance.

Local AF_UNIX capability restrictions must remain visible as explicit skips;
headless test-only coordinator observations are not live health-socket evidence.
Linux CI sets `PAPER_SERVICE_REQUIRE_SOCKET=1`, so socket capability skips fail
those jobs. Read actual final-head jobs/logs, not prior-head successes. CI source
artifacts are retained for seven days; they are neither deployments nor signed
release provenance.

Keep main unchanged and the PR Draft/open/unmerged with no auto-merge. Publish
only the exact tested tree using expected-head non-force feature update. Guard
the original full title/body before changing only those fields, then independently
reread refs/tree/parents/PR metadata/all applicable CI and local cleanliness.

Design: [v49 spec](superpowers/specs/2026-11-15-paper-release-design.md).
Plan: [v49 plan](superpowers/plans/2026-11-15-paper-operations-v49.md).
