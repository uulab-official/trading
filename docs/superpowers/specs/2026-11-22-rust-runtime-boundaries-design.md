# Rust execution boundaries and order registry — 2026-11-22

## Intent and baseline

Continue the requested trading-project work from remote main
`0d73780f1090d49338971f07aae482a6d965b53e`. Compiler quality is already
verified; runtime inspection found invented broker success responses and an
order registry whose created orders cannot be read back. Correct these concrete
defects with credential-free tests before adding any broker transport.

The current Toss/KIS snapshot-to-journal comparison, readiness diagnosis, and
synthetic paper lab already exist. A fresh audit found no missing bridge to
implement. Preserve them and rerun their existing verification.

## Approaches and decision

1. Implement authenticated order transport now: requires broker semantics,
   genuine account evidence and a complete risk/accounting contract that are
   not available. This would not resolve the existing false-success boundary.
2. Rewrite the entire Rust portfolio, risk engine and market-data pipeline:
   useful follow-up, but it mixes accounting and feed contracts with the
   directly reproducible registry defects.
3. Correct unsupported-operation reporting, reject Live at startup, and make
   the in-memory order registry consistent. This is the chosen implementation
   approach under the user's continuing instruction to proceed. It preserves
   the established Python paper engine and produces independently testable
   improvements without implying production readiness.

## Execution boundary

- `Config::validate()` rejects `TradingMode::Live` before other runtime checks.
  Paper configuration needs a strategy but does not require broker secrets.
- Both `WorkerProcess::new` and `MasterProcess::new` load and validate their
  configuration before creating data/database resources, removing sockets,
  connecting transports or starting processes.
- Binance's unimplemented order placement, cancellation and balance query
  return explicit errors without transport or invented IDs/balances.
- Bithumb is an unimplemented adapter: connect, subscription, private account,
  order and disconnect methods return explicit unsupported errors and publish
  no connection/trading events.
- The manager's passive event proxy likewise refuses unsupported operations.
  Receiving an event subscription remains available. This change does not
  pretend the proxy delegates execution to the stored adapter.
- Errors identify the unsupported operation using fixed text. No supplied
  credentials, order payloads or configuration are included.
- Configuration parsing discards source-rendered TOML errors and their causes
  before returning a fixed diagnostic; malformed secret-bearing syntax must
  not expose the file contents in display, debug, chained or CLI errors.
- Existing CLI options and all five help commands retain their behavior.

## Order registry contract

- One map stores Pending and later order states. Pending, Open and
  PartiallyFilled are unresolved/open orders.
- Use the existing UUID v4 dependency for order IDs; preserve all existing
  public method signatures. Add fallible update/restore methods for callers
  that need refusal details; existing void wrappers warn on refusal.
- Creation rejects blank/control-character symbols, nonfinite/nonpositive
  quantities, and nonfinite/nonpositive supplied prices. Non-market orders
  require a price. No rejected creation changes the registry.
- Restore validates the full snapshot and accepts an identical repeated
  restore; a conflicting existing ID is refused without overwriting it.
- Updates require a known local ID, unchanged symbol/side/type/requested
  quantity/requested price/creation timestamp, and a nondecreasing update
  timestamp. An existing exchange ID may not change or disappear.
- Filled quantity must be finite, nonnegative, no larger than the requested
  quantity, and nondecreasing. Positive fills require a finite positive average
  price; zero fills require no average price. The cumulative gross must remain
  finite and increase when additional positive fills are recorded. An unchanged
  filled quantity cannot rewrite its average price.
- Pending/Open/Failed have zero fills. PartiallyFilled has strictly between
  zero and requested quantity. Filled has exactly the requested quantity.
  Canceled can retain a partial fill but not a fully filled quantity.
- Commission is either absent with no asset, or a finite nonnegative cumulative
  amount with a nonblank asset. A positive commission needs a positive fill.
  Known commission amount cannot decrease/disappear and its asset cannot change.
- Allowed transitions: Pending to any valid state; Open to Open,
  PartiallyFilled, Filled, Canceled or Failed; PartiallyFilled to itself,
  Filled or Canceled. Filled/Canceled/Failed are terminal. Only a repetition
  with unchanged order content (apart from a later observation timestamp) may
  revisit a terminal state; it is a no-op.
- Validation and mutation happen under the per-order map lock. Invalid,
  regressive, conflicting or unknown updates leave all stored fields intact.
- TradeUpdate applies all validated cumulative fields, not just status.
- Worker startup restores validated database orders into the registry and
  propagates invalid-order failures. This is in-memory restoration, not broker
  reconciliation, retry permission or a crash-safe economic ledger.
- Persisted side/type/status and timestamps must decode strictly. Unknown
  order statuses must reach validation during unresolved-order loading rather
  than silently disappearing from its query. The shared position-side decoder
  and position opening timestamp likewise refuse malformed records. A failed
  read never rewrites the stored value.
- An explicit SQL NULL filled quantity is missing evidence and must be refused.
  The existing schema DEFAULT 0 remains valid when the column is omitted.

## Validation

Use real in-memory managers and actual adapter methods with synthetic inputs.
Test pending visibility, concurrent ID uniqueness, cumulative partial/final
fills, duplicate delivery, terminal/stale/regressive/conflicting updates,
invalid numbers and metadata, and a real isolated SQLite save/load/restore.
Test mode validation and a worker subprocess in an owned temporary directory
with an absent Unix socket; assert Live refusal before data creation. Never
invoke an unguarded real master constructor or touch its global socket in a
test. After implementing its first-line guard, inspect the master sequencing.

Run locked Rust tests/release build with the existing pinned Rust 1.99.0,
maintain warnings-as-errors and unchanged Cargo.lock, run network-disabled
Docker help tests in CI, and verify the exact published commit's full CI.

## Explicit remaining limits

No authenticated broker calls or real orders. Credentials are absent. Rust
portfolio/cash/fee accounting, worker risk enforcement, public-feed lifecycle
and restart reconciliation still need dedicated implementation contracts and
tests. In-memory f64 order metadata is not an exact-money ledger. Existing
Python paper validation remains the reproducible simulation path. No change
in this increment enables live operation, image publication or deployment.
