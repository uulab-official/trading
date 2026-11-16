# Verified PAPER backups and recovery verification (v47)

## Intent and constraints

The user requests continued production-quality implementation. v46 provides one
continuous PAPER account and mandatory archived replay before resume, but no
repeatable backup or recovery drill. This increment adds an immutable paired
account/capture bundle, complete verification, and verification of a separately
materialized recovery copy. It never transfers execution authority.

Preserve all 24 modules named by `paper_stream._engine_fingerprint()` byte for
byte, including capture and stream stores/runtimes. Preserve historical formats
and Python >=3.10. New code uses the standard library and existing installed
packages on supported local POSIX filesystems. No credentials, provider/broker
requests, real orders, deployment, main merge, or external messages are needed.
The existing feature branch and Draft PR delivery remain authorized.

## Approaches and decision

1. **Paired immutable bundle and non-executing recovery verification — selected
   by the implementer.** Preserves existing recovery compatibility, verifies real
   copied files, retains terminal states, and cannot become a runnable replica.
2. Original-path-only executable account restore could recover while the original
   source survives, but cannot establish that the selected backup contains later
   committed state or terminal refusals. An absent original path does not prove
   old-account retirement. It is not included.
3. Executable pair migration needs a new retirement/fencing, source rebinding and
   rollback protocol. Rewriting the immutable source binding is not migration.

These choices follow the user's repeated instruction to proceed. They are root
implementation decisions, not an invented record of user design approval. The
initial original-path idea was revised after independent architecture review.

## Bundle format

New owned directory, mode 0700; exactly four regular owned single-link files,
mode 0600:

| Name | Bound |
|---|---:|
| `.backup.lock` | exactly zero bytes |
| `backup.json` | 65,536 bytes maximum |
| `account-database.bin` | 268,435,456 bytes maximum |
| `capture-database.bin` | 67,108,864 bytes maximum |

No arbitrary archive extraction, symlinks, hardlinks, special files, extra
members, sidecars, decompression, or executable account/capture layout. The two
SQLite payloads remain byte-identical copies. A copied capture is archived
evidence, not a replacement authorized source. Reads/copies use 1-MiB chunks.

`backup.json` is canonical UTF-8 JSON (sorted keys, compact separators, no NaN,
no duplicate keys or trailing bytes), with these exact fields:

- `format`: `uulab.paper-backup`; integer `version`: 1; `mode`: `paper`;
  boolean `live_enabled`: false; `backup_id`: 32 lowercase hex characters;
  `created_at`: canonical aware UTC ISO timestamp.
- `origin`: exact keys `account_path`, `capture_path`, bounded absolute paths as
  provenance. Verification never follows these untrusted recorded paths.
- `account`: exact keys `stream_id`, `execution_fingerprint`,
  `manifest_sha256`, `engine_sha256`, `event_head_sha256`, `events_verified`,
  `control`. Derived from the validated account and full replay.
- `capture`: exact keys `capture_id`, `config_sha256`, `snapshot_sha256`, `state`.
  Derived from the validated captured-source snapshot.
- `payloads`: the two fixed payload names, each with exactly `size` (integer)
  and `sha256` (64 lowercase hex characters).
- `boundaries`: exactly `verification: full_archived_replay`,
  `execution_authorized: false`, `source_rebinding: false`,
  `rollback_protection: false`, `provider_authenticity: not_authenticated`.

The account's frozen capture path/ID/config must match the archived capture and
recorded origin. Pair verification proves identity/configuration and exact saved
bytes. It does not admit new source data, clear a source conflict, establish
freshness, or authenticate the provider. Both databases can preserve blocked,
retrying or stopped states. No phase, error, cooldown or timestamp is normalized.

## Creation and durability

`create_backup(account, output, *, stop=None, clock=None)` is async. Acquire the
existing account audit reader (shared nonblocking lock), then the pinned source
capture's shared nonblocking lock; retain both until final confirmation. Real
active owners are refused regardless of saved phase. All source access is
read-only; nonempty hot journals are refused without repair.

Validate the bound source and run existing `_verify(reader, snapshot, stop)`
while the same owner is held. Check time is valid and no earlier than either
saved control timestamp. Validate destination separation from account/capture
before creation. Reserve a new output directory without clobber, hold its
exclusive backup lock, copy and fsync both payloads, write/fsync metadata, fsync
directory and parent, then verify the actual copied payloads through the original
SQLite validators and full economic replay. Recheck original bindings/content
and snapshots before reporting success.

Existing input bytes never change. New output may remain incomplete after any
failure. The creation lock prevents cooperating check/restore readers observing
an in-progress publication. No recursive cleanup, overwrite, or blind retry is
performed. Directory reservation is not an atomic completed-bundle publication;
the complete manifest, payload validation and final flush define success.

## Read-only verification

`check_backup(directory, *, stop=None)` acquires a shared nonblocking backup
lock. It validates exact file names, private identities, bounds, canonical
metadata and streaming SHA-256 values. Open payload SQLite connections with
`mode=ro`, trusted_schema off, query_only on, bounded existing readers and stable
binding checks. Adapt the existing account `_Reader` to the fixed payload name,
then reuse `_verify`. No executable staging layout or input repair is needed.

Recompute all derived metadata from actual payloads and compare exact canonical
types/values, including terminal fields. Verify file content stability and
hashes after replay before returning. All code/runtime fingerprints are checked
by the unchanged verifier. The original source/account paths need not exist.

## Recovery verification

`verify_restore(directory, output, *, stop=None)` holds the original bundle's
shared lock through full input verification, exact copying, destination replay
and final input confirmation. It requires a distinct new private destination,
outside the source bundle, and never overwrites a prior recovery copy. The new
bundle retains the same archive ID, metadata and payload bytes. It contains only
the non-executing four-file bundle layout; no `stream.sqlite3`, `capture.sqlite3`
or operational owner lock is created. The original source may be unavailable.

Success means the recovered files are a verified archived prefix. It is not
permission to resume, source rebinding, an account reset, or proof of the newest
known state. SHA-256 is local consistency evidence, not authentication or an
external rollback/ownership anchor. General host-disaster execution recovery is
still separate engineering work.

## Operation result and errors

All successes return exact keys: `schema_version: 1`,
`source: paper_backup_operation`, `mode: paper`, `live_enabled: false`,
`operation: create|check|verify-restore`, `read_only` (true only for check),
`status: verified_backup|verified_restore`, `exit_code: 0`, `backup_id`,
`stream_id`, `capture_id`, `events_verified`, `engine_sha256`,
`event_head_sha256`, `payloads`, `boundaries`. No private paths in public results.

Exceptions live in new `paper_backup_io`: `BackupUnavailable(ValueError)`,
`BackupStorageError(OSError)`, `BackupInterrupted(signum)`. Signal callback must
return exact integer 0, 2 or 15. Check it throughout chunked IO and replay.
Handlers record intent only and are restored. CLI emits exactly one JSON result:
0 success; 2 invalid/unavailable/busy/fingerprint/audit refusal; 4 storage or
unconfirmed output; 130/143 interruption. Errors after mutation retain artifacts.
Signal results never claim no output; storage uncertainty takes precedence.

## Verification and operational scope

Real-file tests cover both owner locks, terminal-state preservation, source
identity changes, mutation during copy, hot journals, payload/metadata tampering,
digest-consistent forged economic state, corrupt schema, unexpected files,
permissions/links/special files, short writes, capacity, process death, signal
boundaries, fsync uncertainty, original-path independence and non-executability.

A checked-in network-forbidden drill creates 120 bootstrap +120 future synthetic
bars, backs up the one account/capture, verifies it, makes the generated original
locations temporarily unavailable, and verifies a recovered copy. It compares
exact payload bytes, account/risk state and full audit evidence. It never resumes
a recovered copy. CI exercises this against supported Python runtimes.

No maximum-size recovery SLA, actual power-loss/hardware certification, real
provider soak, service deployment, external backup media, alerts or browser QA is
claimed. Generic automatic restarts remain inappropriate: existing failure codes
can represent terminal refusal or storage uncertainty. Use explicit inspection
and existing resume semantics for original accounts.

## Primary technical references

- https://sqlite.org/backup.html — snapshot/copy concurrency assumptions.
- https://sqlite.org/atomiccommit.html — rollback-journal and flush assumptions.
- https://docs.python.org/3/library/os.html — descriptor-relative file operations.
