# Offline paper pair backup and restore-validation runbook

`tools/paper_backup.py` creates a **new, immutable-by-policy directory**
containing the current local paper archive and simulated portfolio checkpoint.
It does not retrieve secrets, make network requests, place orders, or restore
state automatically. It only accepts an independently replay-verified
`mode: paper` archive + checkpoint pair.

## Create a backup

Stop continuous supervision first or let the command refuse when the paper
writer owns either state lock. From the repository root:

```sh
python tools/paper_backup.py create \
  --archive data/upbit-btc-1h-archive.json \
  --checkpoint data/upbit-btc-1h-paper.json \
  --destination "data/backups/paper-$(date -u +%Y%m%dT%H%M%SZ)"
```

Three files are created in a **new** output directory:

- `archive.json`: exact bytes of the validated public-candle history
- `checkpoint.json`: exact bytes of the verified simulated trade ledger
- `manifest.json`: UTC backup timestamp, market/timeframe, bars and
  SHA-256 hashes for both components

The tool independently replays the saved strategy prefix before locking
and copying. After acquiring both local POSIX locks, it checks the bytes
still match the verified hashes. It writes staging files with owner-only
permissions, fsyncs them, then renames the staging directory to the
chosen destination. It refuses an existing destination rather than
intentionally overwriting a previous backup. It does not provide
cryptographic signatures or protection against malicious users who can
rewrite both the manifest and contents; preserve backups on secure,
access-controlled storage.

## Verify a saved backup

```sh
python tools/paper_backup.py verify data/backups/paper-YYYYMMDDTHHMMSSZ

# Optional: independently replay backed-up paper economic state
python tools/paper_backup.py verify \
  data/backups/paper-YYYYMMDDTHHMMSSZ --deep
```

The output status `backup_bytes_verified` means both backed-up files match
their manifest's hashes. The report explicitly states
`economic_replay_verified: false` because this *verify* command only
checks backup bytes, not economic strategy correctness.

With `--deep`, the auditor additionally replays the backed-up strategy
history, verifies economic/risk/pending-signal state, and reports
`backup_deep_verified` with `economic_replay_verified: true`.
Without it, verification checks only the backed-up bytes and reports
`backup_bytes_verified`. Neither verifies profitability or broker orders.

For a recovery evaluation, **do not overwrite** running production data.
Copy the saved archive + checkpoint into new isolated test paths, then
independently run the full paper checkpoint audit against that pair:

```sh
python tools/paper_checkpoint_audit.py \
  /isolated/recovery/archive.json \
  /isolated/recovery/checkpoint.json
```

Only after an operator confirms strategy assumptions, history, checkpoint
cursor, current risk guard state, market, permissions, and successful
manual replay should they consider replacing the inactive local paper
paths. Keep the original incident files and a rollback copy. No restore
command is supplied intentionally.

## Safety limits

- POSIX local file locks only; remote/shared/network filesystems are
  not covered by these tests
- Backup pair is a point-in-time copy under cooperating file locks;
  external processes that ignore those locks cannot be made safe
- Output directories need unique operator-owned names; direct concurrent
  external writes to destination paths are unsupported
- A large archive/checkpoint beyond 32 MB each is rejected
- No rotation, offsite replication, encryption at rest, restore automation,
  key management, or disaster-recovery RPO/RTO guarantee is included
- Offline paper balance history does not establish real-world profitability
  or validate a live exchange position

Run the offline network-forbidden regression suite:

```sh
python tools/offline_checks.py
```

Refer to [production safety gates](../PRODUCTION_CHECKLIST.md).
