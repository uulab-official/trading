# Existing PaperRunner → read-only replay results

The user has requested continued implementation without step-by-step approvals.
This increment connects the existing offline `PaperRunner`/`PaperExchange` to the
operations dashboard, without creating a second execution or accounting engine.

## Approach
Keep the SQLite admission journal separate from executed replay accounting.
Publish a versioned, bounded JSON read model only after a successful existing
runner execution. Replacing the journal with a second fill ledger was rejected:
it would conflate reservations and executed balances and duplicate the engine.
A browser-side checkpoint import was rejected: raw checkpoint data must not become
an authoritative account merely because a browser can parse it.

## Contract
Python 3.10+; no new runtime dependencies; local POSIX checkpoint execution only.
The producer reads one local fixture, executes the existing engine, validates
Decimal cash/holdings/equity/fees against its fills, and atomically publishes.
The reader validates the same invariants, permits no URL-selected paths, and
returns at most 100 recent fills. It exposes no checkpoint path or raw payload.
Report publication uses a cooperating writer lock, cannot overwrite inputs or
unrelated files, and preserves an existing report if the replay/publish fails.
Existing report files belong to one fixture/configuration identity.
UI keeps replay results separate from reservations and labels input provenance,
historical bar time, local generation time, fees, and offline—not live—status.
No live orders, broker credentials, new strategies, auth or public deployment.

## Limits
A validated read model establishes arithmetic consistency, not authenticity,
strategy profitability or real market provenance. It can be regenerated from the
checkpoint and original fixture. Partial fills, live feeds, execution controls,
and admission-journal/execution coordination remain outside this increment.
