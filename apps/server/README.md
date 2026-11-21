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

Rust's operational exchange, order, and deployment paths remain unvalidated.
The current exchange adapters contain simulated/stub behavior. A clean build
does not validate broker authentication, real fills, or always-on operation.
See [Rust verification](../../docs/RUST_QUALITY_VERIFICATION_2026_11_21.md) and
the [production checklist](../../PRODUCTION_CHECKLIST.md).
