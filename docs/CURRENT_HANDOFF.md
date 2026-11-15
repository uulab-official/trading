# Trading continuation handoff — 2026-11-15 (v47)

## Current scope and delivery

Repository `uulab-official/trading`, feature `feat/smc-market-structure`,
[Draft PR #1](https://github.com/uulab-official/trading/pull/1).
Starting feature commit: `846385bd4fa717a5d97c14a79719fad8534b0b7e`, tree
`a06db23df8f2d8b9625c44d00533fcb7ef9a72d5`. Main remains
`a5750019dcd7a980a28783258c06c7ef4706afc1`.

The user requested continued production-quality work. v46 already implements a
continuous PAPER account with atomic per-bar persistence, original MA/RSI
accounting, source continuity refusal and full archived replay before explicit
resume. This increment adds private paired backups and independent verification
of recovered archive copies. It does not transfer execution authority.

Implementation, tests, feature publication and Draft PR title/body updates remain
authorized. Main merge, deployment, credentials, provider/broker requests, actual
orders and external notifications are outside this delivery. At turn start an
independent audit confirmed all prior refs/metadata and four successful v46
workflows. Read actual refs/PR when continuing; this handoff is an immutable
pre-publication checkpoint, not a claim about a future commit identity.

## Critical design decision

All 24 modules named by `paper_stream._engine_fingerprint()` must remain byte
identical to v46. They include both stream and capture runtimes/stores, original
accounting and strategy code. Editing them would invalidate existing accounts'
frozen code/runtime fingerprint. New tooling lives in separate modules.

An account's own physical path is not frozen, but its capture's path, device/inode
and owner/permission identities are. An executable account copy could therefore
run independently, while a copied capture cannot become its authorized source.
An older backup also cannot prove it includes later terminal refusals or account
progress. The initial original-path restore idea was rejected after independent
architecture review. This increment provides non-executing recovery evidence;
executable restore needs a new authority/retirement/rebinding/rollback protocol.

## New implementation

### Private bounded IO

`paper_backup_io.py` owns the new `BackupUnavailable`, `BackupStorageError`,
`BackupInterrupted(signum)` errors, exact numeric stop intent, `PrivateDirectory`
and streaming `file_info`, `copy_file`, `write_file` helpers. Read/write chunks
are at most 1 MiB. Input/output descriptors and names are pinned to the parent,
directory and file identity, UID/mode/link count, size and modification metadata.

`PrivateDirectory` requires an owned real parent and an owned 0700 leaf; files
are 0600 owned single-link regular files. No symlinks, special files or extra
bundle members. New files/directories are no-clobber. Short writes are completed;
payloads and newly created empty locks are fsynced and checked. Directory and
parent flushes confirm publication. Forked handles cannot operate, and closing
an inherited descriptor never explicitly unlocks its parent's lock.

A coordinator-held destination parent descriptor is acquired before potentially
long replay. The helper's `expected_parent=(canonical_path, identity)` is checked
before mkdir, preventing a replacement parent from silently becoming the output.
All possible output is preserved after partial writes or uncertainty.

### Paired creation and full checking

`paper_backup.py` exposes async:

- `create_backup(account_directory, output, *, stop=None, clock=None)`
- `check_backup(directory, *, stop=None)`
- `verify_restore(directory, output, *, stop=None)`

Create acquires the existing account audit SH lock, then the exact bound capture
SH lock, nonblocking. Both remain held across original full replay, copying and
final confirmation. Saved phase is not process liveness: an actual writer is
refused even when its saved state says stopped. Readers never repair nonempty
journals or alter source/account metadata.

The source must match the account's frozen physical binding and logical ID/config.
The bundle preserves exact source/account bytes and control/error states. This
pairing establishes archived identity/configuration, not freshness or admission
of new data; source-history conflicts are never cleared by backing up evidence.

Create reserves a new private output directory, holds its exclusive backup lock,
copies and fsyncs two payloads, writes canonical bounded metadata, fsyncs the
directory/parent, verifies the actual copied databases and full economic replay,
and rechecks original bindings/snapshots. Original bindings and stable content
are checked again after the final directory/parent sync, before success.
Directory reservation itself is not
atomic completed-bundle publication. Incomplete output may remain on failure.

Check takes a shared backup lock, validates exact layout/metadata/size/hash and
opens the two fixed binary payloads directly using read-only SQLite connections.
A small binding adapter lets the unchanged account reader and full `_verify`
replay operate on `account-database.bin`. No temporary executable layout or
input repair is used. Capture structure is validated by the original bounded
reader. Derived metadata is recomputed and compared with exact canonical types.
Payloads are hashed again after replay and stable identities are rechecked.

### Fixed inert bundle

Exactly four files, no arbitrary archive extraction:

| Member | Bound |
|---|---:|
| `.backup.lock` | empty |
| `backup.json` | 65,536 bytes |
| `account-database.bin` | 268,435,456 bytes |
| `capture-database.bin` | 67,108,864 bytes |

Manifest format `uulab.paper-backup`, integer version 1, mode paper/live false;
32-hex backup ID; canonical UTC creation time; original paths as provenance;
account ID, code fingerprint, manifest/state/event-head digests, verified event
count and exact control; capture ID/config/snapshot digests and exact state;
payload sizes/SHA-256; explicit non-authority boundaries.

Duplicate keys, extra keys, noncanonical encoding/types, unknown members and
oversize payloads are refused. Recorded original paths are not followed by check
or restore verification. Hashes detect corruption/inconsistency; they are not a
signature or an external latest-state/rollback anchor.

### Recovery copy and operation contract

Verify-restore holds the original bundle's shared lock, fully verifies it, copies
its exact bytes into another new private bundle, and verifies the actual recovered
files with full replay. Originals may be absent. Archive IDs and metadata stay
the same. The output never contains executable stream/capture database/lock
names. No source binding is rewritten, no account is launched, no old snapshot
is installed over a current account, and no refusal is normalized away.

Success schema1/source paper_backup_operation includes operation, mode/live/read_only,
verified_backup or verified_restore status, exit 0, archive/account/capture IDs,
verified event count, state/event digests, payload sizes/hashes and boundaries:
full_archived_replay, execution_authorized false, source_rebinding false,
rollback_protection false, provider_authenticity not_authenticated. Public result
and fixed errors omit private paths.

## Operator workflow

Use original fingerprint-compatible code/Python on a supported local POSIX
filesystem. Stop the continuous consumer first, then its bound collector, and
wait for both processes to exit. Retain their JSON results and any error state.

```bash
python tools/paper_backup.py create ACCOUNT_DIR --output NEW_BACKUP_DIR
python tools/paper_backup.py check BACKUP_DIR
python tools/paper_backup.py verify-restore BACKUP_DIR --output NEW_RECOVERY_DIR
python tools/paper_backup_drill.py --directory NEW_SYNTHETIC_DRILL_DIR
```

The backup CLI emits one JSON document. Exit 2 means invalid/unavailable/busy or
fingerprint/audit refusal; 4 means storage/unconfirmed output; 130/143 signals.
Creation/recovery failures conservatively report output_may_exist. Handlers only
record intent and are restored; a storage fault takes priority over a simultaneous
signal. A signal after a confirmed copy can still report interruption, so preserve
and inspect output. The synthetic drill follows the same storage-priority rule
and handles both its own interruptions and the core backup API's typed interruption.

The offline drill accepts only a new directory and creates its own synthetic
inputs. It denies connection/DNS/UDP calls, runs 120 bootstrap plus two 60-bar
continuations, checks 18 fills against the original engine, stops the owners,
backs up, temporarily moves only its own original paths, and verifies restored
binary snapshots/fills/risk/byte hashes. It proves both bundles refuse ordinary
stream/capture runtime opening. Original generated names are restored on normal
or error exit. `drill-report.json` is separate from the strict bundle contents.

Complete guide: [PAPER_BACKUP.md](PAPER_BACKUP.md). Original continuous-account
semantics remain in [CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md).

## Verification and review

Focused final gates: 38 private-IO methods, 25 coordinator methods,
10 CLI methods and 7 drill methods (80 new methods), all passed in 69.938 seconds.
Core first RED was 21
missing-feature failures. Parent substitution during original/input replay was
reproduced as two false-success failures and repaired with the held parent and
expected-parent precondition. Empty lock fsync/identity failures and signal versus
storage-priority faults have failing-before/passing-after regressions.

Actual process death during a payload copy leaves a refused incomplete bundle,
unchanged originals and released owner locks. Additional coverage includes real
owner contention, preserved terminal reasons, hot-journal refusal, bounded IO,
short writes, fork ownership, hash/metadata corruption, digest-consistent forged
cash refused by full replay, exact restore after originals disappear, and actual
OS signal delivery. No invented live-provider or power-loss result is claimed.

A fresh independent whole-increment review found no Critical findings and two
Important findings. Both were reproduced and repaired. Moving either original
directory during final output sync previously yielded false success; creation
now performs its stable-source check after that sync and preserves possible
output on refusal. The drill previously classified core `BackupInterrupted` as
generic failure; it now reports the signal exit code explicitly. Six actual OS
signal cases (SIGINT/SIGTERM at create/check/verify-restore) pass while the
simultaneous fsync-failure test still requires exit 4. The final handoff records the repaired local verification below.

Final local results on the repaired implementation:

| Gate | Observed result |
|---|---|
| Complete unittest discovery | 1,365 passed, 208.030 s, no skips |
| New backup/recovery subset | 80 passed, 69.938 s; included in the complete suite |
| Existing package/server pytest | 309 passed + 382 subtests, 14.85 s; 926 existing deprecation warnings |
| All Node client tests | 103 passed, zero failed/skipped, 317.674299 ms |
| Python 3.10 syntax compatibility | All eight new Python files parsed |
| Frozen runtime compatibility | All 24 fingerprinted files byte-identical to v46 |
| Prior v46 account | Full 120-event audit still verified; database bytes unchanged |
| Standalone repaired offline drill | 120 consumed events, 18 fills, exact original-engine economics |

The final drill independently rechecked original/backup/recovery SHA-256 equality
for both DB payloads, exact metadata, private modes and the report's archived
snapshot/fill/risk equivalence. Check and recovered-copy verification succeeded
with the generated original paths absent. Both bundles refused normal runtime
opening. This is synthetic local recovery verification, not executable failover
or maximum-capacity/hardware validation.

Exact final-tree publication and CI follow this checkpoint. Their actual results
are recorded in the PR and ignored receipts.
Existing optional formatting warnings or external coverage upload failures must
be reported separately from required test success if observed again.

## Remaining production work

Implemented: continuous PAPER operation, explicit verified resume, paired inert
backup creation/checking, and repeatable local recovered-copy verification.

Remaining: executable account/source migration with authority transfer and
rollback policy; actual external backup-media and host-disaster recovery;
real-provider/network/clock/disk soak; supervised service deployment/rollback,
alert delivery; worst-case 100,000-event replay latency; capacity archival beyond
100,000 consumed bars/256 MiB; broker-authoritative execution/reconciliation;
actual browser visual/accessibility inspection. Existing Rust/Compose/deploy
scripts do not package the continuous Python runtime as a verified service.

Readiness remains false. Do not infer liveness, current prices, investment
performance or live execution authorization from a valid archived prefix.

Design: [v47 spec](superpowers/specs/2026-11-15-paper-backup-design.md).
Plan: [v47 plan](superpowers/plans/2026-11-15-paper-backup.md).

## Publication discipline

Keep main and Draft/unmerged/no-auto-merge state. Publish the exact tested tree
using an expected-head non-force ref update. Verify all applicable PR and push
workflows on the published commit, including actual matrix logs and skipped image
publication/release jobs. Update only PR title/body after fresh comparison with
the saved metadata baseline, then independently reread refs/tree/metadata/CI.
