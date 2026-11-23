# Public market feed and paper supervisor implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Track the checkboxes below.

**Goal:** Repair the Rust public market observation path and preserve Python paper supervisor stop/failure state across restarts.

**Architecture:** Binance owns its WebSocket inside one cancellable worker task. Validated closed candles carry their exchange opening timestamp and timeframe, are stored immutably by symbol/timeframe/time, and reach only the matching strategy. The Rust worker emits observations and signals; economic execution remains unavailable until its cash/fee/risk ledger exists. Python supervision retains durable stop and failure state under one canonical ownership lock.

**Tech Stack:** Existing Rust 1.99.0, Tokio, SQLx/SQLite, serde, Python 3.10+ standard library; no added application dependencies.

**Spec:** Remaining work and execution limits in `docs/RUST_RUNTIME_VERIFICATION_2026_11_22.md`, existing `docs/PAPER_SUPERVISOR_RUNBOOK.md`, and the reproduced failures recorded in this plan.

## Global constraints

- Preserve `Cargo.lock`, existing dependency versions, CLI compatibility, and compiler warnings as errors.
- Use fixture/loopback input and offline paper tests; do not start brokerage authentication, account transactions, messaging, or production services.
- Keep historical candle and financial records intact; do not infer a timeframe for old records.
- Persist completed changes to the existing authorized GitHub `main`, after local validation and independent review.

## Review focus

- No receiver, closed/lagged event channels, or task shutdown may become false connection success or an indefinitely blocked worker.
- Missing, non-finite, contradictory, duplicated, cross-symbol or cross-timeframe market data must not mutate strategy state incorrectly.
- Connection failure must not substitute invented market prices; signals must not create holdings without an execution ledger.
- Repeated graceful exits must not erase a halt latch or reset a failed cycle budget.
- Data, STOP and derived lock paths must not alias and replace an owned lock inode.

## Task 1: Binance public transport

**Files:** `apps/server/src/exchange/binance.rs`, `apps/server/src/exchange/binance_tests.rs`.

**Interfaces:** Existing `Exchange` methods and `run_message_loop`; `Candle.timeframe: String`, `Candle.timestamp` as exchange open time, shared `Candle::validate_closed()`.

- [x] Reproduce raw/combined parsing, missing validation, no-subscriber connect and disconnect state failures using existing APIs.
- [x] Normalize raw/combined messages; validate closed OHLCV, stream identity, interval and event/open/close times before emitting data.
- [x] Retain unique subscriptions across explicit reconnect, reject unsupported intervals, and remove subscription-free fallback.
- [x] Clear transport state on close, EOF and errors; acknowledge ping frames and make status notification independent of receiver count.
- [x] Run parser and localhost transport tests with bounded completion; inspect fixed error messages for payload leakage.

## Task 2: Rust observation worker and isolated strategy routing

**Files:** `apps/server/src/worker/mod.rs`, `apps/server/src/worker/tests.rs`, `apps/server/src/strategy/mod.rs`, `apps/server/src/core/models.rs`, `apps/server/src/core/config.rs`, relevant Rust regression tests.

**Interfaces:** `StrategyBinding::new(symbol, timeframe, Box<dyn Strategy>) -> Result<Self>` and `on_candle(&Candle) -> Result<Option<Signal>>`; `None` means a different stream or duplicate/old candle and must not update the underlying strategy. `Candle::validate_closed()` rejects invalid data before storage or dispatch.

- [x] Reproduce fabricated buy/sell holdings and closed receiver hang before changing worker behavior.
- [x] Validate enabled exchange/strategy/symbol/timeframe/parameter configuration before runtime side effects.
- [x] Give the transport task ownership of the client and obtain receivers before it starts; cancel and join the task on every exit.
- [x] Route only matching validated closed candles; skip duplicates and older timestamps independently per strategy.
- [x] Deliver market observations and strategy signals without mutating orders, holdings or realized profit. Remove automatic random-price fallback.
- [x] Fail explicitly on event-channel loss, persistence conflict and unsupported execution; preserve existing financial DB records.
- [x] Verify the real worker event path with synthetic events and temporary SQLite/Unix sockets, plus isolation tests for interleaved streams.

## Task 3: Immutable interval-qualified candle persistence

**Files:** `apps/server/src/core/database.rs`, new candle persistence Rust tests.

**Interfaces:** Existing `save_candle(&Candle) -> Result<()>`; new `closed_candles` table keyed by `(symbol, timeframe, timestamp)`; old `candles` table remains untouched.

- [x] Write failing tests for distinct intervals, immutable duplicate/conflict behavior and invalid inputs.
- [x] Validate shared candle contract and insert or compare exactly without overwriting an existing candle.
- [x] Test temporary file-backed SQLite and unchanged legacy rows.

## Task 4: Durable Python paper supervisor

**Files:** `tools/paper_supervisor.py`, `apps/server/tests/test_paper_supervisor.py`, supervisor runbook.

- [x] Reproduce initial-stop halt release, graceful-restart failure-budget reset and lock/data path collision.
- [x] Resolve and validate data/STOP/derived lock paths before creating files or acquiring ownership.
- [x] Restore the failure count consistently for loop and one-shot; reset it only after a completed successful cycle.
- [x] Preserve halted/operator-stop records even when shutdown is already requested.
- [x] Run supervisor, heartbeat, cycle and checkpoint regression suites.

## Task 5: Master observation status and dashboard

**Files:** `apps/server/src/master/mod.rs`, `apps/server/src/master/web.rs`, new master regression tests.

- [x] Reproduce fixed healthy/Live state and missing unavailable-account metadata.
- [x] Retain connection-generation-qualified state independently of browser subscribers.
- [x] Require recent heartbeat and received prices for observation health; never promote a halt on heartbeat alone.
- [x] Send the current snapshot to new WebSocket clients and poll status so silence becomes visible.
- [x] Replace invented portfolio figures and execution labels with explicit observation/unavailable state.
- [x] Verify endpoint status, reconnect generations, framing/EOF and stale status without starting production services.

## Integration and delivery

- [x] Run the complete locked Rust suite and optimized build; inspect changed-file rustfmt output.
- [x] Run network-forbidden Python regressions and the deterministic paper lab.
- [x] Have a separate reviewer inspect the integrated changes and resolve material findings with regression evidence.
- [x] Record actual verification counts, limitations and next work.

After committing this snapshot, publish main with an expected-head check and
inspect CI for that exact SHA. The remote result is recorded in GitHub Actions
and the delivery response; it cannot be embedded as a successful result in its
own not-yet-published source snapshot.
