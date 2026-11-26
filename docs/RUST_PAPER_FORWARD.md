# Append-only closed-bar paper continuation

## Purpose

The Rust paper-forward CLI extends one bounded, deterministic historical
paper experiment without replacing existing strategy accounts. It is an
offline reconstruction of a fixed bar sequence, NOT a real-time execution
service. A delayed synthetic fill at a historical open does not establish
that an order was submitted, or executable, at that time.

```sh
cd apps/server
mkdir -p /tmp/forward-example
cargo run --locked -- paper-forward --input config/paper_forward_example.json --output-dir /tmp/forward-example --initialize
cargo run --locked -- paper-forward --input config/paper_forward_example.json --output-dir /tmp/forward-example --audit-only
cargo run --locked -- paper-forward --input config/paper_forward_example.json --output-dir /tmp/forward-example --audit-only --html > /tmp/forward-audit.html
```

The included envelope is a synthetic fixture, not actual Binance history.

## Input and immutable account identity

A version-1 ForwardInput contains source, source_id, archive_head_sha256 and
the complete version-1 ReplayInput scenario. Source is either
binance-spot-public-utc-v1 or synthetic-fixture-v1. Digests are lowercase
64-character hex. The session digest includes the source label and identity,
first bar's UTC open timestamp, and all scenario fields except bars. The
market units, strategy list and risk/execution rules cannot change while
advancing the same accounts. Only a longer contiguous suffix is permitted.
The supplied archive head is informational provenance in the output; Rust
does not authenticate it or infer an independent trusted external checkpoint.

The forward engine reuses the exact existing MA/RSI, fee, participation, IOC,
risk and accounting model. After every closed bar, including warmup and
zero-trade bars, the journal contains a nonfinancial SealReplayBar event
committing its exact serialized decimal input. The journal head binds
these events to the account's full history. Changing a warmup volume,
past high/low, or even decimal spelling changes the seal and is refused.
Old executables cannot decode the new event variant; use the new build.
A seal is not broker evidence; only independent full replay establishes
that the seal represents the supplied input.

## Init, advance, audit and recovery

- Initialize requires an empty dedicated directory; it creates each strategy
  account with the entire verified initial journal. It never overwrites.
- Advance (no flag) requires every existing account. It fully recomputes the
  experiment and checks every stored event against the expected prefix and
  final complete-bar boundary before any writes. The unexecuted intent at
  the end of the previous sample is reconstructed, not discarded or filled
  at the original signal close. The report labels such an intent
  AwaitingNextBar, rather than an expired terminal experiment.
- Append only missing complete bar groups, in checkpoint-bound atomic
  transactions of at most 128 events. Each retry verifies the exact segment
  against its previous head. Existing balances, fills and halt state survive
  restart because the prefix is never replaced.
- Audit-only requires exact full history, uses read-only SQLite and creates
  no account. JSON/HTML output includes strategy results, journal heads and
  remaining event capacity. No current-market health is asserted.
- Use exclusive ownership of a dedicated private local output directory.
  Multiple strategy databases are independent, not cross-file atomic.
  If advance fails after one DB commits, preserve the input and rerun the
  identical scenario; complete prefixes can catch up. Missing, corrupt or
  partial-bar accounts are not automatically repaired. A failed initial
  creation requires inspection and a fresh directory or verified restore.
- The existing limits remain 240 bars and 2,048 events per account. No
  automatic sliding window, reset, compaction or unbounded long-term run.
  Full input is replayed for independent verification; batches reduce the
  number of SQLite transactions but no throughput claim is made.

## Validation

```sh
cargo test --locked --test paper_forward_test
cargo test --locked
```

Regression cases cover delayed signal recovery, staged/full history equality,
warmup revisions and identity changes, missing accounts and partial-bar
boundaries, multiple atomic batches, invalid inputs, and local CLI audit.
Network-disabled Docker smoke coverage compares init and audit output.

Actual source freshness, data capture, order-book availability, exchange
authenticity, live dashboard and continuous scheduling are separate concerns.
No actual broker requests, live orders or real capital release are enabled.
