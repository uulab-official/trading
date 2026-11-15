# Continuous paper consumption — v46 design

## Intent and scope

Continue the user's production-quality implementation request from v45 HEAD
`1b1f56b5a06438d16c55255601920ffb43b03f94`. Add a durable foreground consumer of
the existing public candle capture. One account continues across polls and
explicit restarts. Feature publication and Draft PR updates retain the existing
authorization; merge, deployment, credentials, real orders and notifications
are outside this increment.

These implementation choices are the assistant's calls under the continuation
instruction. They do not represent user-selected trading parameters or a claim
of production readiness. No real provider requests are needed for verification.

## Approaches and decision

1. Repeated historical replays would reset/recompute accounts and do not provide
   a continuous consumption contract. The existing frozen-input runner remains
   available for its historical purpose.
2. A cursor beside `PaperTradingSession` introduces multiple independent commit
   points, retains every order in memory and rewrites the entire account bundle.
   Its existing 10,000-order/16-MiB lifecycle is unsuitable for this consumer.
3. **Selected:** a new SQLite owner commits each input bar, fill, complete account,
   strategy and risk state, and cursor atomically. It uses the original execution
   primitives in a detached per-bar book. Historical code and recovery schemas
   remain unchanged. All financial events are retained to support verification.

## Engine and model

Python 3.10+, POSIX private local storage, standard library and existing packages.
No new dependency. KRW spot and the original MA crossover/RSI strategies only.
The existing 16 historical fingerprinted source files remain byte-identical.

`paper_stream_engine.py` exposes:

- `initial_state(config: PaperRunConfig, bootstrap: list) -> dict`
- `validate_state(state: dict) -> dict`
- `async advance_state(state: dict, candle: list) -> tuple[dict, dict | None]`

Bootstrap is the latest at most 500 contiguous retained bars, at least
`max(50, slow_period)` for MA or `max(50, rsi_period + 1)` for RSI. Feed the
original strategy; discard **all** bootstrap signals. Initial cash is configured,
holding and fees are zero, pending is null, and the cursor is the last bootstrap
open. The first possible fill comes from a post-activation signal and its following
bar. No historical trade is created during initialization.

Engine state exact fields: `schema_version`, `config`, `bootstrap_count`,
`next_index` (post-bootstrap consumed count), `last_open_ms`, `candles` (latest
500), `cash`, `holding`, `equity`, `total_fees`, `fill_count`, `risk_day`,
`daily_start_equity`, `peak_equity`, `daily_loss_halted`, `drawdown_halted`,
`pending_signal`, `strategy_state`. MA strategy state has `kind`,
`prev_fast_above_slow`, `consecutive_bars`; RSI has `kind`, `prev_rsi`.
Behavior-irrelevant signal history/statistics are not persisted. Numeric values
use finite bounded canonical Decimal strings and exact integers, never booleans.
Config magnitudes are at most 1e18 with exponents -18..18; account strings have
at most 100 characters, magnitudes at most 1e100 and exponents -100..100. Preserve
the original Decimal trailing-zero representation for account values and fill
quantities; source rows use the original fixed-point canonical representation.

Restore the original CandleBuffer directly and restore its strategy transition
fields, without replaying the retained tail through the 50-bar analysis gate.
For each new candle, construct a detached original PaperExchange from carried
cash/holding and an original OrderManager. Under the original Decimal context:
mark open; reset daily baseline by UTC date; use `PaperRunner._execute_pending`
with its original source index; require no unresolved order; mark close; update
original daily/lifetime risk rules; call the original strategy; retain its next
pending signal; carry balances and cumulative fee increment. At most one exact
original PaperExchange fill is emitted per bar. The input state is never mutated.

This is **delayed next-open simulation**. The following bar's opening price is
learned from that bar after it closes. Fill timestamp is model event time;
the SQLite event's `applied_at` is the later observation/commit time. Neither is
an actual broker execution or a timely quote. Risk halts prevent new buys,
permit exits, and do not liquidate automatically.

## Persistence interfaces and schema

New `paper_stream_store.py` owns `stream.sqlite3` and permanent `.stream.lock`.
Owned 0700 directories and 0600 single-link files, no symlink final components,
pinned parent/directory/database/lock identities, nonblocking flock, no-clobber
new directory creation, fixed SQLite schema/application identity, DELETE rollback
journal and FULL synchronization plus containing-directory fsync. Read-only
observers never repair nonempty journals; explicit writer resume may recover.
Check binding immediately after SQLite connect before settings or initialization.

Interfaces:

- `stream_writer(directory, *, manifest=None, engine=None, at=None, source=None)`
  creates a new account only with all creation inputs, otherwise opens existing.
- `writer.read() -> snapshot`
- `writer.commit_bar(engine, bar, fill, *, at, source) -> snapshot`
- `writer.control(phase, error, *, at, source=None) -> snapshot`
- `writer.latch(error) -> snapshot` persists a terminal fault when no new clock
  stamp is trustworthy, retaining the exact last trusted timestamp and all
  account/event/source state. It never refreshes success or data freshness.
- `writer.events()` yields bounded canonical event dictionaries in sequence.
- `writer.history(first_open_ms, last_open_ms)` reads at most 5,000 archived
  bootstrap/consumed candles by indexed sequence for complete source overlap checks.
- `stream_reader(directory, *, audit=False)` is a read-only context. `audit=True`
  holds a nonblocking shared flock and permits `events()`; a running writer
  causes refusal. Passive head reads take no flock and do not enumerate events.
- `writer.verify_head(engine_sha256, event_head_sha256)` unlocks economic commits
  on an existing owner only after the runtime has replay-verified that same head.
- `read_stream(directory) -> snapshot` is a bounded passive head observation.
- `StreamUnavailable(ValueError)`, `StreamStorageError(OSError)`,
  `StreamCapacity(StreamUnavailable)` distinguish refusal, uncertain storage,
  and planned archival capacity.

Snapshot exact fields: `schema_version`, `manifest`, `control`, `engine`,
`engine_sha256`, `event_head_sha256`. Manifest exact fields: `format`
(`uulab.continuous-paper`), `version` (1), `mode` (`paper`), `live_enabled` (false),
`execution_model` (`delayed_next_open`), `stream_id` (32 lowercase hex),
`created_at` (canonical aware UTC), `config` (original identity), `activation_source`
(initial source progress below), `bootstrap`
(canonical rows), `capture` (below), `engine_sha256` (64 hex fingerprint),
`poll_seconds` (5–300), `max_bars` (100000).

Manifest capture exact fields: `path` (canonical absolute source directory),
`binding` (parent/directory/database/lock physical identity mapping), `capture_id`,
`config` (original frozen capture config). Binding each identity is a JSON list
`[device, inode, mode, uid, gid]`; `parent` also carries resolved path via a separate
`parent_path` field. The directory path itself is in capture.path.

Control exact fields: `revision`, `phase`, `last_error`, `updated_at`, `source`.
Phases: `waiting`, `running`, `stopped`, `blocked`. Errors: null,
`source_unavailable`, `source_not_current`, `source_identity_changed`,
`source_history_conflict`, `source_regression`, `retention_lost`,
`source_blocked`, `clock_regression`, `capacity`, `engine_rejected`.
Blocked is terminal and cannot be cleared by stop or resume. Initial phase is
waiting and error null. Control changes never alter engine or events.

Source progress exact fields: `revision`, `accepted_bars_total`,
`evicted_bars_total`, `last_verified_at`, `latest_open_ms`, `snapshot_sha256`.
Its digest covers the entire validated capture snapshot. Require monotone source
progress and equality of content at the same revision. Source config/ID are in
the manifest. Source progress may advance without consuming a bar.

Event exact fields: `sequence` (1-based), `bar`, `fill`, `applied_at`, `source`,
`previous_sha256`, `engine_sha256`, `sha256`. The hash is canonical JSON of all
event fields except `sha256`. Initial event head is 64 zeroes. Sequence, engine
next_index and cursor increase together; event plus head plus control is one
transaction. At most 100,000 consumed bars and 256 MiB of database are supported.
No financial event eviction. Capacity causes a preserved terminal result, not a
new account. Other per-row/metadata/VM bounds are explicit in the implementation.

Before commit, perform detached calculations only. Any unconfirmed commit or
directory synchronization failure ends the owner immediately; do not retry the
candidate. Explicit reopen determines the actual durable prefix. DB restoration
or coherent modification by a privileged owner is not cryptographically detected
without an external trust anchor; hashes are consistency guards.

## Source admission and lifecycle

`paper_stream.py` exposes `async run_stream(directory, *, capture=None,
config_options=None, poll_seconds=5, max_polls=None, stop=None, clock=None,
sleep=None)`, `stream_status(directory, *, now=None)`, and
`async audit_stream(directory, *, stop=None)`.
`capture` supplied means new account; absent means explicit resume. Resume
accepts no configuration/source overrides. Defaults remain paper-only, KRW cash
1,000,000, original friction/risk values, MA periods 3/5. CLI only supplies explicit
overrides for the existing PaperRunConfig options.

Hold source descriptors and persist its physical identity; verify them before
and after every read and before any account commit. The new account directory
must be outside the capture directory. Use one validated capture
snapshot both for `_observation(snapshot, now)` and its rows. Admit new work only
when its status is current and phase running. Stale, fetching, retrying, stopped
or temporarily unreadable source means waiting with no economic mutation.
Clock errors, source terminal errors, replacement, progress regression, revised
overlap or lost required cursor overlap block the account. Compare all available
overlap against the consumer's archived bootstrap/consumed history. Require exact next-open
timestamp and source sequence. A finite batch may be caught up chronologically;
never jump a gap or reuse cached input after a failed new observation.

Recheck current wall-clock freshness and pinned binding before each commit.
Detect wall-clock regression without clamping. While still holding the writer,
latch clock faults and any already observed terminal refusal using the last
trusted control timestamp. The result reports the actual regressed time, or a
null observation if no valid time exists. This cannot authorize further bars.
Use monotonic sleep with stop
checks at most 0.1 seconds apart. Stop handlers only record intent; stop before
commit discards detached work, stop after confirmed commit preserves it. Stop
must never clear a saved blocking condition. Preserve previous handlers.

At most 10,000 polls may be requested for a bounded invocation; default is a
foreground loop. Bounded completion with current source returns 0 and records
stopped; source waiting returns 3 and preserves its reason. Signals return
130/143. Input/ownership/invalid stream refusal is 2; storage uncertainty is 4.
`check`: 0 only current consumer, 1 valid noncurrent, 2 unavailable.
`audit`: 0 verified prefix, 2 invalid/mismatch, signal codes on cancellation.
One bounded JSON stdout result per completed CLI command; fixed errors exclude
paths and exception text. A saved phase is not process liveness.

## Recovery and audit

Freeze SHA-256 fingerprints of the actual imported execution, strategy, data,
source/storage/consumer modules and Python major/minor/implementation. Resume
requires the exact fingerprint before any new bar. No automatic migration.

Explicit resume must stream-replay the archived bootstrap and every consumed
event through the detached engine, compare every fill and state digest, validate
the hash chain and compare the final complete engine state. It never resubmits
effects to the persisted account. Reuse the verifier for read-only `audit`, which
requires the writer to be stopped and refuses a busy owner without delaying its
commits. Check cancellation between events. Memory remains bounded, work grows with the
capped journal. Re-read the source after verification to detect retention loss
during startup. Passive status is head structural integrity, explicitly not a
full economic audit. Resume from terminal blocked state or a saved terminal
reason remains refused, even if another saved phase accompanies that reason.

## Observation and delivery

Provide passive CLI status with account/cursor/pending/risk, last source progress,
consumer/source ages and clear model boundaries. Add a GET/HEAD dashboard route
only if the existing bounded HTTP contract can be preserved; readiness schema1
retains its exact four keys. No browser bypass or visual QA claim.

Tests use real original engine, SQLite files and synthetic closed captures with
network forbidden. Cover >500-bar MA/RSI continuation, original economic parity
with equivalent activation, no bootstrap trades, pending causality, UTC risk
rollover, duplicate polling, stale data, mutation/rotation/retention loss, signals,
clock regression, crash/commit uncertainty, authoritative tamper detection and
archival capacity. Run existing unittest, package pytest and Node suites, plus
actual supported-Python CI on the final published feature head. Preserve main,
Draft state and unrelated changes. Record remaining soak/deployment/broker gaps
and keep `is_production_ready()` false.

## Technical references

- SQLite [atomic commit](https://www.sqlite.org/atomiccommit.html) and
  [synchronous](https://sqlite.org/pragma.html#pragma_synchronous): rollback
  transactions and explicit directory confirmation, with filesystem assumptions.
- Upbit [minute candles](https://docs.upbit.com/kr/reference/list-candles-minutes):
  public historical source, no synthetic bars when trading is absent.
