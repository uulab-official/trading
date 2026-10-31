# SQLite paper order journal

`packages/core/order_journal.py` stores idempotent order intents and reserved buying power in SQLite. Atomic BEGIN IMMEDIATE transactions serialize reservations across cooperating writers; journal mode is WAL. Unknown submissions survive restarts and remain reserved until reconciliation.

This is not a complete production execution system. `mark_submitting` is called **before** attempting external submission, and a timeout should be treated as unknown. On restart, `submitting`, `unknown`, and `cancel_pending` require broker-side reconciliation before any retry. `confirm_cancelled` is only for confirmed cancellations, not timeouts.

Limitations: caller must independently enforce cash, fees, exposure and risk limits; the journal does not authenticate to a broker or simulate fills, does not persist fill IDs/commissions, and does not implement live execution. Never expose this SQLite file via a public web endpoint.
