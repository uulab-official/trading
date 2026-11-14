# Durable order state (v20, opt-in)

`OrderManager(exchange, state_store=store)` supports synchronous write-ahead
storage. Without a store the existing in-memory behavior is unchanged. This is
NOT automatically enabled in the bot and does not authorize real orders.

## Contract

Commit SUBMITTING before create_order; commit CANCEL_PENDING and cancellation
intent before cancel_order. Validate and save acknowledgements, readbacks and
unknown outcomes before returning normally. Caller cancellation still propagates.
Any storage failure latches manager.storage_failed and blocks subsequent external
I/O until a fresh manager/store is opened. Diagnostic getters can contain newer
unsaved observations, so they are not proof of successful persistence.

Restore every nonterminal saved order as UNKNOWN and save that normalization
before admitting I/O. Identical IDs return detached snapshots, not submissions;
different IDs remain blocked while any outcome is uncertain. The existing
readback code may query known venue IDs. A missing venue ID is never guessed or
blindly resubmitted; broker-specific authoritative reconciliation is still needed.

A database commit and venue acceptance are NOT one transaction. A crash after
venue acceptance but before a durable acknowledgement intentionally leaves an
unresolved order. This is not an exactly-once broker execution guarantee.

## Ownership, files and limits

`SQLiteOrderStateStore(path, scope='paper-main', create=True)` creates a NEW store.
Omit create=True when reopening. Missing/empty/corrupt files are not initialized;
existing files are not overwritten. No other journal/ledger format is migrated.

Use a private local POSIX directory, one account/venue per file and scope alias.
The alias is operator supplied, NOT verified broker account identity. A lifetime
advisory lock allows one process per file and one manager per handle. Do not
remove the lock file. Stop polling and await outstanding calls before closing the
store. Forked handles cannot operate; closing a child copy keeps the parent lock.

At most 10,000 orders / 16 MiB of canonical snapshot JSON. The full snapshot is
rewritten synchronously: disk waits may block the event loop, and this is NOT a
high-throughput execution engine. No pruning, automatic retry, unlock or clearing
history to bypass uncertainty is provided. The revision counter is bounded.

SQLite uses rollback journaling and synchronous=EXTRA. Revision/content checks,
file/lock identity checks and private-file permissions guard cooperating writers.
Unexpected schema, extra tables/triggers, invalid rows, symlinks/hardlinks and
scope mismatches fail closed. Hashes are not trusted signatures. Malicious local
writers, power failures, remote filesystems, Windows and prolonged load have not
been validated. Never concurrently edit these files with noncooperating software.

Only normalized order fields are persisted; no keys/tokens or raw adapter replies.
Order IDs and quantities are still sensitive. Never publish the DB or lock file.
Official semantics: https://www.sqlite.org/pragma.html#pragma_synchronous and
https://docs.python.org/3.10/library/fcntl.html#fcntl.flock .

## Integration

```python
from trading_core.order_manager import OrderManager
from trading_core.order_state_store import SQLiteOrderStateStore

# This exchange must represent the SAME independently restored account.
with SQLiteOrderStateStore('.private/orders.sqlite', scope='paper-main') as store:
    manager = OrderManager(existing_exchange, state_store=store)
    # Inspect and reconcile unknown outcomes using authoritative venue records.
```

Use create=True only for intentional first creation. This DB records order state,
NOT cash/positions/fees. Do not connect restored orders to a new empty paper
account and assume account recovery. The local demo explicitly restores a
separate PaperExchange snapshot; that is not durable multi-file atomic recovery.

```bash
python tools/order_restart_demo.py --database .private/order-demo-001.sqlite
python -m unittest discover -s tests -p 'test_order_*.py' -v
```

The demo submits to PaperExchange only and requires a new path. The tests cover
process termination before/after venue effect and DB acknowledgement commit,
no duplicate resubmission, readback of cancel-pending orders, failed writes,
malformed storage, competing/forked owners and unchanged no-store behavior.
