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

## Binance public archive integration

The existing Python public collector can export a current, verified fixed-start
window as a ForwardInput envelope. It checks the archive, freshness, operator
STOP, contiguous bar count, market/template identity and optional exact
expected archive head in a single read transaction. The source ID is derived
from the pinned archive configuration; the head changes as it grows.

From the repository root (prepare a real static market-rules scenario template,
not the synthetic DEMO fixture):

```sh
python tools/binance_public_archive.py forward-export data/binance-history/btc.sqlite \
  --start-open-ms SESSION_START_UTC_MS --template data/btc-paper-template.json \
  > data/btc-forward-input.json
apps/server/target/release/trading-server paper-forward \
  --input data/btc-forward-input.json --output-dir data/btc-forward --initialize
```

After later completed-bar capture, repeat the export with the SAME start and
template and invoke paper-forward without --initialize. Use --audit-only for
a read-only report. Do not change start to get around capacity. Use the
collector's separate sync --loop for capture; no automatic real order or
live execution is involved. The input file must be fully written before the
Rust process reads it; use a private directory and atomic file publication
if scheduling these commands. Independent external archive checkpoint
retention and real API verification are still required.

## Local supervised paper continuation

`tools/binance_paper_forward_supervisor.py` connects the separately running
public archive collector to the local Rust executable. It does not make
network requests itself. Prepare a private existing empty session directory
and a fixed real-market template. Initialize explicitly once, then run the
same command without --initialize and with --loop if desired:

```sh
python tools/binance_paper_forward_supervisor.py \
  --archive data/binance-history/btc.sqlite --template data/btc-paper-template.json \
  --binary apps/server/target/release/trading-server --session-dir data/btc-forward \
  --start-open-ms SESSION_START_UTC_MS --initialize
# Remove --initialize and add --loop --poll-seconds 60 for a supervised loop.
```

The supervisor pins the archive, directory, template digest, executable path,
start and failure budget in a sibling .forward-status.json file. It acquires
an exclusive local POSIX owner lock across cycles/sleep; do not remove the lock
or run other account writers concurrently. Only current verified archive
snapshots may invoke the fixed paper-forward subcommand (empty inherited
environment, no shell, bounded invocation time). The report's provenance,
account identity and checkpoint fields are checked before atomic status
publication. The latest successful report is retained even on failure and is
historical simulation, not an assertion of current positions at a broker.

Stale/catching-up data consumes the durable failure budget and uses bounded
backoff; invalid archive, replay, or report state halts. Create a sibling
.forward.STOP marker to latch halt at a cycle boundary. An in-progress
subprocess may finish its simulated transactions before the stop is observed;
no further cycle starts. On interruption, retain the stopped/previously
halted status. After diagnosing a halt, stop the process and preserve the
status file under a distinct review path before a deliberate restart;
never delete the SQLite account or reset a missing/corrupt history.
For an interrupted initial creation, inspect and restore/rebuild in a new
directory; ordinary advance never creates missing accounts. No automatic
service installation or start is performed. A crash can leave running status;
operator health monitoring must check status and age, not last_report alone.
Use a trusted executable and directory; this is not an OS sandbox or
signature verification system.

Seven new orchestration regressions are registered in the network-forbidden
Python safety suite. The supervisor remains bounded by the Rust 240-bar,
2,048-event limits; do not rotate/reset accounts automatically to bypass them.
Continuous real-data validation, independently retained external checkpoints,
incremental long-history replay and a live operator UI remain separate gates.
