# Rust paper ledger: logical backup and external checkpoint

## Purpose

This is a simulated-account storage extension, not a trading or deployment command.
The existing worker, dashboard, private exchange API and Python paper account are
unchanged. No broker communication occurs.

## API

- `PaperStore::export_backup()` returns versioned canonical JSON bytes and a separate
  `JournalCheckpoint` containing the ledger ID, sequence and SHA-256 head. Both are
  generated from a single verified SQLite read transaction, including committed events
  that still reside in WAL. This is a logical export, not a copy of SQLite files.
- `checkpoint()` returns the current verified journal head.
- `open_with_checkpoint(path, id, config, checkpoint)` accepts the exact checkpoint
  or a valid later extension containing that prefix. It rejects a shorter journal or
  a divergent fork even if that database's internal hashes are consistent. The floor
  is rechecked on every audit/export/append on that handle.
- `restore_backup(path, id, config, bytes, expected)` requires the exact externally
  supplied expected checkpoint, verifies account, original configuration, canonical
  encoding, version, event capacity and replay before creating a destination.
  All metadata and events are initialized in one SQLite transaction and compared
  against the verified replay before commit. It never replaces an existing file.
  Initialization failure leaves any newly reserved file for inspection; it is not
  a successful restore. The restored handle retains the supplied checkpoint floor.

## Trust and operations

1. Export the journal from the simulated account. Persist backup bytes using a separate
   trusted backup service or directory; retain the matching checkpoint independently
   (different failure/administrative domain).
2. Preserve the original ledger identity and configuration, including integer units.
   Never derive the trusted checkpoint only from the backup being verified.
3. To restore, stop any consumer of that account and create a new DB path. Supply the
   exact known checkpoint for the chosen backup. Do not point two active consumers
   at different copies of the same account.
4. Verify the restored audit result and head before changing the runtime DB reference.
   Keep the old DB and WAL intact. On restart use the latest independently trusted
   checkpoint with `open_with_checkpoint`.

An external checkpoint is a trusted *minimum history*, not proof that no events
after it have been lost. It detects rollback or forks at/before that checkpoint,
not loss of later history. An attacker able to modify both backup and checkpoint
can still forge history. The API neither signs checkpoints nor ships an independent
checkpoint service. It does not automatically persist/advance the trusted floor.
The existing unpinned `open` remains a compatibility API without external rollback
protection. Backup file persistence, fsync/atomic publication, retention, scheduling,
cross-process account ownership, path/symlink race prevention, crash/power-loss tests,
and a command-line operational interface remain separate work. Use a trusted local
directory; do not alias SQLite files or WAL paths.

The version-1 limit remains 2,048 events / 4,096 bytes per event and the complete
backup is limited to 9 MiB. Full replay is intentionally bounded. This is not a
long-term high-throughput storage design.

## Verification

```sh
cd apps/server
cargo test --locked --test paper_backup_test
cargo test --locked
```

The added tests cover partial-fill and halt restoration, duplicate retries,
checkpoint prefix acceptance, valid forks, rollback after opening, tampered or
oversized backups, exact-checkpoint requirements, refusal to overwrite, empty
backup, concurrent export/append, and malformed checkpoint identity. The commands
above describe validation to run, not test results.
