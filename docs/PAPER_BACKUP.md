# Verified PAPER backup and recovery bundles (v47)

This workflow preserves a stopped continuous PAPER account and its bound public
capture in a private backup bundle. It verifies the account's complete archived
execution before reporting success. A separate recovery command copies that
bundle to a new destination and fully verifies the copied data again.

**A verified recovery bundle does not authorize execution.** The commands never
start or resume a collector, strategy account or broker. They preserve the saved
source binding, progress, account state and terminal refusal evidence. The
payload names are deliberately different from the executable account/capture
database names.

## Prerequisites and operating boundary

Use the existing repository packages and the original fingerprint-compatible
Python environment on an operator-owned local POSIX filesystem:

```bash
python -m pip install -e packages/core -e packages/data -e packages/strategy -e apps/server
mkdir -p .private
chmod 700 .private
```

Run the commands from a known exact checkout. Record that revision with the
backup and retain the original execution environment. The account freezes the
SHA-256 of 24 execution, strategy, storage and capture modules plus Python
major/minor and implementation. Another checkout is usable only if those
fingerprints match. Upgrading Python or changing execution code is not an
automatic account migration. The backup tools are additive and preserve those
existing v46 module bytes.

No credentials, provider requests, exchange orders, external notification or
remote publication are part of this workflow. Full replay is local simulated
execution against archived inputs. A verified prefix establishes consistency
with those inputs; it does not establish provider authenticity or profitability.

The implementation assumes supported local POSIX file permissions, advisory
locks and SQLite rollback-journal behavior. It is not a Windows, network
filesystem, storage hardware or power-loss certification.

## Stop the owners before creating a backup

1. Request cooperative stop of the continuous consumer using its foreground
   terminal or the process supervisor you operate. Wait for the process to exit
   and retain its final JSON result and exit code.
2. Stop the bound capture separately and wait for its process to exit. Preserve
   any saved error or interrupted-acquisition evidence.
3. Run `create` below against the original account directory and a new backup
   destination outside the source directories.

A saved `stopped` phase does not prove the process has exited. Backup creation
requires the actual owner locks to be available. It retains a shared account
audit lock and a shared capture lock through verification and copying, so a
cooperating writer cannot restart halfway through the operation. A competing
writer causes a refusal; the backup command does not stop or kill it.

A read-only verifier never repairs a nonempty journal, replaces a source,
removes a permanent lock, clears a terminal reason or refreshes saved timestamps.
If an earlier storage result is uncertain, preserve the original directory and
follow its existing explicit recovery procedure before deciding whether it is
eligible for verified backup. Do not delete sidecars to make the check pass.

## Create a new verified backup

```bash
python tools/paper_backup.py create .private/btc-stream-001 \
  --output .private/btc-backup-001
```

`create` reads the capture path and physical identity from the account's frozen
manifest. It does not accept a different source, strategy, account configuration
or runtime fingerprint. The output must be a new leaf directory; an existing
destination, including an empty directory or an incomplete earlier attempt, is
never overwritten. Account/source and output paths must not overlap.

The command verifies the account's bootstrap, every archived bar and fill, state
digests, final account/strategy/risk state and event hash chain. It validates the
paired capture and its relationship to the account, copies the bounded payloads
under retained locks, and confirms identities and copied bytes before reporting
`status=verified_backup`.

Source and account data remain unchanged. The backup keeps their saved phases,
errors and progress. A valid archived terminal refusal stays a terminal refusal;
successful backup is not a decision to resume that account. Likewise, backup
validation is not a claim that the stopped source is current or that its process
is alive.

### Fixed private layout

| Member | Purpose | Bound / permissions |
|---|---|---|
| Backup directory | Owns the complete bundle | `0700` |
| `.backup.lock` | Stable bundle coordination lock | Empty regular file, `0600` |
| `backup.json` | Versioned manifest, identities, digests and verification evidence | At most 64 KiB, `0600` |
| `account-database.bin` | Preserved continuous account database bytes | At most 256 MiB, `0600` |
| `capture-database.bin` | Preserved bound capture database bytes | At most 64 MiB, `0600` |

The supported payload bound is **320 MiB plus 64 KiB of metadata** and an empty
lock file. Filesystem allocation and any preserved incomplete destinations need
additional free space. Budget for both the input bundle and a new recovery copy
when performing a drill.

The format does not support arbitrary archive members, symlinks, hardlinks or
special files. Treat the complete directory as the artifact. Copying a single
payload or discarding the manifest loses the bundle's recorded relationship and
verification context.

## Check a saved bundle without changing it

```bash
python tools/paper_backup.py check .private/btc-backup-001
```

`check` verifies the fixed layout, private files, bounded manifest and payload
sizes, hashes, capture structure and full archived economic replay. It reads the
actual binary payloads directly, without extracting an executable account or
creating scratch databases. Successful check returns `status=verified_backup`,
`read_only=true` and the same archive identities.

The original account/capture need not be present to verify an already saved
bundle. The fingerprint-compatible execution code and Python environment are
still required. A copied backup can establish the same archived prefix; it
cannot establish that it is the newest surviving backup or that no later
terminal refusal was recorded elsewhere.

Checksums detect accidental corruption and inconsistencies within the supported
validation contract. They are not a signature or an external authority. A
coherent privileged rewrite, replacement by a complete older valid bundle, or
another independent account copy is outside the rollback/ownership guarantees.

## Copy and verify a recovery bundle

```bash
python tools/paper_backup.py verify-restore .private/btc-backup-001 \
  --output .private/btc-recovery-check-001
```

This command verifies the input bundle, copies its exact manifest and payloads
to a new private no-clobber destination, and fully verifies the actual copied
bytes. A successful result has `status=verified_restore`. The backup ID, stream
ID, capture ID, payload digests and economic prefix remain the same; this is
not a newly funded account or a new source capture.

The output still contains `account-database.bin` and `capture-database.bin`.
It does not contain the normal executable `stream.sqlite3` or
`capture.sqlite3` names. It is a verified recovery artifact for inspection and
drills, with **`execution_authorized=false` and `source_rebinding=false`**.

The account originally bound its capture's path and physical filesystem
identity. Copying or relocating that capture does not satisfy the original
binding. The restore verifier does not rewrite that identity, modify a source
watermark, or substitute an earlier account for a later one. An older complete
backup can omit refusals written after it was made, which is why archive
verification cannot by itself authorize rollback or resumption.

Do not rename the binary payloads into an executable account as an operational
restore procedure. A future executable restore or migration needs a separate
reviewed authority-transfer and continuity contract. This increment deliberately
provides reproducible recovery evidence while preserving the existing account's
immutable source and terminal-state rules.

## JSON results and exit codes

Each completed operation writes one JSON document to stdout. Core diagnostics,
if any, use stderr. Fixed failure messages do not contain private paths, account
contents or exception text. `--help` is ordinary human-readable CLI help.

A successful result contains:

| Field | Meaning |
|---|---|
| `schema_version` / `source` | `1` / `paper_backup_operation` |
| `operation` | `create`, `check` or `verify-restore` |
| `mode` / `live_enabled` | `paper` / `false` |
| `read_only` | `true` only for `check`; other operations write their new output bundle |
| `status` | `verified_backup` or `verified_restore` |
| `exit_code` | `0` |
| `backup_id`, `stream_id`, `capture_id` | Preserved archive, account and capture identities |
| `events_verified` | Number of post-bootstrap archived events fully replayed |
| `engine_sha256`, `event_head_sha256` | Verified final economic state and event head |
| `payloads` | Account/capture payload sizes and SHA-256 values |
| `boundaries` | Full archived replay; no execution authorization, source rebinding, rollback protection or authenticated provider evidence |

| Exit | Meaning and action |
|---|---|
| `0` | Verified backup, read-only check or copied recovery artifact completed |
| `2` | Invalid arguments, missing/invalid data, busy owner, wrong fingerprint or failed validation/replay; inspect the preserved inputs and any output |
| `4` | Storage or unexpected failure, including uncertain publication; preserve inputs and possible output before deciding another attempt |
| `130` | SIGINT/cooperative cancellation; preserve possible output |
| `143` | SIGTERM/cooperative cancellation; preserve possible output |

For runtime errors or interruption, JSON includes `output_may_exist`. It is
`true` for `create` and `verify-restore`, and `false` for read-only `check`.
This is a conservative warning about the command's output boundary, not a claim
that an artifact exists or is complete. Argument rejection happens before the
core operation and returns `paper_backup_arguments_invalid`.

Signal handlers only record numeric intent. Work checks that intent through
normal control flow and restores prior handlers on exit. A signal arriving
after a confirmed copy can still return an interrupted status; retain the copy
and inspect it. Storage uncertainty takes priority over a simultaneous signal
and remains exit `4`. No failure code authorizes deleting input, clearing an
error or overwriting an output.

## Supervision and recovery drills

These commands perform a bounded foreground operation. They do not register a
service or configure automatic restarts. Use explicit operator actions and
record the process exit separately from the account or backup's saved metadata.

The collector and consumer already contain their defined temporary waits and
retry policy. Their exit codes include terminal refusal, fingerprint/audit
mismatch and uncertain storage, so a generic restart-on-failure rule is not an
approved recovery decision. Keep automatic restart disabled for this workflow.
Preserve the exact checkout/environment instead of modifying code in place under
an existing account.

A local drill should record:

1. The original revision/Python fingerprint and the account's verified event
   count and head digests.
2. Successful creation and independent checking of a quiescent paired bundle.
3. Successful `verify-restore` to a second new destination, with the same archive
   identities, payload bytes and verified economic prefix.
4. Unchanged input bytes, refused writer contention and malformed/incomplete
   artifacts, and preserved failures when copying or synchronization is
   interrupted.
5. The explicit absence of executable account/capture filenames and execution
   authorization in both outputs.

Network-forbidden synthetic drills and injected local failure tests are useful
evidence of the implemented contract. They do not establish real-provider soak,
hardware durability, maximum-capacity recovery latency, real backup transport,
an executable disaster-recovery migration or deployment readiness.

Existing continuous operation and explicit resume remain documented in
[CONTINUOUS_PAPER.md](CONTINUOUS_PAPER.md). The wider operating boundaries and
remaining evidence are in [PRODUCTION_PAPER_OPERATIONS.md](PRODUCTION_PAPER_OPERATIONS.md)
and [PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md). `is_production_ready()`
remains false.
