# Rust Runtime Boundaries Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` or
> `superpowers:subagent-driven-development` for task execution. Existing
> authorization covers implementation, verification and main publication.

**Goal:** Refuse unsupported Rust execution and preserve a validated, visible
order lifecycle without contacting brokers.

**Architecture:** Keep the existing Python paper system and Rust public API.
The registry owns cumulative local order snapshots. Runtime constructors and
unimplemented adapters enforce an explicit no-live/no-fabricated-success
boundary. Independent implementation work has disjoint file ownership; the
root agent integrates worker restoration and performs the final review gate.

**Tech Stack:** Rust 1.99.0, Tokio, DashMap, UUID, SQLx/SQLite; existing Python
3.10–3.12 CI. No dependency or lockfile changes.

**Spec:** `docs/superpowers/specs/2026-11-22-rust-runtime-boundaries-design.md`

## Global Constraints

- No broker network requests, real orders, deployment, or secret-value output.
- Preserve existing public method signatures and the CLI argument interface.
- Maintain package warnings-as-errors and unchanged Cargo.lock.
- Validate before mutation; failed updates cannot partially change stored data.
- Tests do not start a master, remove its socket, or call exchange connect.
- Publication must use the known remote main parent and an expected-SHA lease.
- Existing local history is a tracking snapshot; do not ordinary-push it.

## Review Focus

- Invalid numeric combinations or fee metadata must leave an order unchanged:
  Task 1 table-driven snapshot tests.
- Concurrent updates must not lose a larger cumulative fill: Task 1 concurrency
  test of competing valid/regressive snapshots.
- A terminal duplicate with a later observation time must not reopen or rewrite
  a completed order: Task 1 terminal-duplicate tests.
- Secret-looking values must not appear in errors or fabricated events:
  Task 2 real adapter failure and receiver tests.
- Live refusal must precede directory/database/socket side effects: Task 2
  pure config tests, isolated worker subprocess and constructor code review.

## Task 1: Consistent order registry

**Files:** `apps/server/src/core/order_manager.rs`, optional `Order` equality
derive in `apps/server/src/core/models.rs`,
`apps/server/tests/order_registry_test.rs`.

**Interfaces:** Preserve `new`, `create_order`, `get_order`, `get_all_orders`,
`get_open_orders`, `update_order_status` and `handle_exchange_event`.
Add `restore_order(&self, order: Order) -> Result<()>`,
`apply_order_update(&self, order: &Order) -> Result<()>`, and
`try_update_order_status(&self, order_id: &str, status: OrderStatus) -> Result<()>`.

- [x] Write regressions for a newly created order's immediate visibility,
  256 concurrent creates with 256 unique preserved IDs, invalid creation,
  Pending-to-Open status updates and full cumulative TradeUpdate metadata.
- [x] Run the tests against the old manager and record behavioral failures.
- [x] Implement the single-map/UUID registry and fallible snapshot validation
  exactly as specified. Keep void wrappers as warning-on-refusal compatibility
  entrypoints.
- [x] Test 10-unit order snapshots with 3 units at average 100 and total fee 1,
  then 10 units at average 102 and total fee 2; all fields must survive. Cover
  overfill 11, decreased fill 2, changed identity, stale update, changed broker
  ID, invalid commission, terminal regressions and no-op duplicates.
- [x] Save and load Pending/PartiallyFilled snapshots in a private SQLite
  database, restore into a new manager and refuse conflicting restoration.
- [x] Run `cargo test --locked --test order_registry_test` then all Rust tests.
  Expected: pass, zero compiler warnings. Record results and limitations.

## Task 2: Truthful execution boundaries and worker integration

**Files:** `core/config.rs`, `core/database.rs`, `worker/mod.rs`, `master/mod.rs`,
`exchange/{mod,binance,bithumb}.rs`,
`apps/server/tests/execution_boundary_test.rs`,
`apps/server/tests/database_restore_boundary_test.rs`, `infra/docker/Dockerfile`.

**Interfaces:** `Config::validate() -> anyhow::Result<()>`; existing `Exchange`
trait methods; Task 1 `OrderManager::restore_order`.

- [x] Write real adapter regressions requiring errors for order/cancel/balance
  operations, no credential echo and no fabricated events. Add Bithumb/proxy
  unsupported connection/subscription tests.
- [x] Write pure config regressions: valid configured Paper without keys passes;
  Live with syntactically present fake keys still fails. Run and record RED.
- [x] Make `Config::validate` reject Live and permit credential-free Paper.
  Invoke it first in both runtime constructors after reading configuration.
- [x] Replace fabricated adapter successes with fixed explicit errors; retain
  Binance public feed implementation and event receiver methods.
- [x] Copy the shipped Rust configuration into the Docker builder so the
  integration tests can compile their real configuration fixture.
- [x] Integrate `restore_order(order.clone())?` in the existing startup restore
  loop before logging success.
- [x] Add real SQLite RED/GREEN tests for malformed side/type/status/time,
  unknown-status visibility, and all 48 valid enum combinations. Strictly
  decode stored order and shared position fields, preserving corrupt bytes
  rather than replacing them with defaults or the current time.
- [x] Fix independently reproduced review findings with RED/GREEN tests:
  discard secret-bearing TOML error causes; reject explicit SQL NULL cumulative
  fill quantity while retaining the default for omitted values.
- [x] Run an isolated worker subprocess with a Live config, confirm refusal
  and no data directory/database creation. Review master guard before its
  socket removal; never run an unguarded master during RED.
- [x] Run focused and full locked Rust tests and locked release build. Run all
  five CLI help paths from an empty temporary directory.

## Task 3: Evidence, review and publication

**Files:** `apps/server/README.md`, `PRODUCTION_CHECKLIST.md`, new
`docs/RUST_RUNTIME_VERIFICATION_2026_11_22.md`, this plan/spec.

- [x] Run existing broker/readiness/review/paper-lab tests and the temporary
  paper lab, reporting actual runner and fresh counts.
- [x] Independently review the complete diff, with the spec's failure cases.
  Fix material findings with demonstrated regression tests.
- [x] Document completed work and the specific remaining Rust accounting,
  risk/feed and authenticated-broker limitations.
- [ ] Verify published tree bytes/modes, update main with expected parent,
  and wait for all required jobs for that exact commit.
- [ ] Report code/CI links, meaningful test counts and current broker readiness.
