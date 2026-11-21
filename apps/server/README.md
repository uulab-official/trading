# Trading server entrypoints

## Python paper tools

The verified offline entrypoint is `trading-paper`, implemented in
`trading_bot/paper_runner.py`. From the repository root, see
[Offline paper runner](../../docs/OFFLINE_PAPER_RUNNER.md).

`trading-bot` remains the separate legacy application. Its live feed is unavailable
in the restored offline data package, and it must not be described as validated
live operation.

## Rust build and CLI checks

The Rust binary uses the public `trading_server` library. It does not compile a
second private copy of the library's modules. Project compiler warnings are
errors through `[lints.rust]` in `Cargo.toml`; this policy does not change
third-party dependency lint settings.

The verified toolchain is Rust **1.99.0**, matching the pinned Docker builder.
The manifest's existing minimum version declaration is unchanged; this validation
does not establish compatibility with every compiler between that minimum and
the verified version.

From this directory:

```bash
cargo test --locked
cargo build --release --locked
```

The test suite includes parser-only checks for command defaults, overrides,
required values, and help for the root, `master`, `worker`, `deploy`, and `migrate`
commands. Docker CI runs the same five help entrypoints with networking disabled.
These checks do not start a server, execute migrations, deploy a binary, or submit
orders.

## Rust execution and order boundaries

`Config::validate()` refuses `mode = "live"`, including when keys or testnet
flags are present. Master and worker constructors validate before socket,
directory, database or transport initialization. Paper configuration does not
require broker credentials; it still requires at least one configured strategy.

Binance's private order, cancellation and balance methods return explicit
unsupported-operation errors. The unimplemented Bithumb adapter and the
manager's passive event proxy also refuse active connection/subscription
operations. They produce no invented broker order IDs, account balances or
connection events. The manager proxy exposes an event receiver; it does not
delegate execution to the registered adapter.

The in-memory Rust order registry keeps pending orders visible, uses UUIDs,
validates cumulative fill/fee metadata, refuses regressive or conflicting
updates, and restores validated unresolved orders from SQLite. Repeated
terminal observations do not reopen orders. Malformed stored enum/time values
are refused, and unknown order statuses cannot disappear from startup loading.
Its `f64` metadata and local
restoration are not an exact-money ledger or authenticated broker reconciliation.

The Rust worker's portfolio/cash/fee accounting, risk enforcement, market-data
reconnect/timestamp handling and deployment paths remain unvalidated. Its
historical paper simulation is not the independently audited Python paper
engine. Use the Python [paper lab](../../docs/PAPER_LAB.md) for the reproducible
offline simulation path.

See [runtime verification](../../docs/RUST_RUNTIME_VERIFICATION_2026_11_22.md),
[compiler verification](../../docs/RUST_QUALITY_VERIFICATION_2026_11_21.md), and
the [production checklist](../../PRODUCTION_CHECKLIST.md).
