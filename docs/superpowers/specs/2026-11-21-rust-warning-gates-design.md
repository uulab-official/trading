# Rust warning cleanup and CLI compatibility

Date: 2026-11-21 (Asia/Seoul)

## Goal and baseline

The user asked to continue all remaining work after Python/CI cleanup. The
published baseline is `dd8cd5dd78ccb1ac974a61cdba8fa1d8dcf9ab27`, CI #467.
Its Rust build passed, while reporting 27 library and 61 binary warnings,
repeated across test and release targets. Broker readiness still reports all
eight allowlisted environment variables absent; authenticated validation cannot
be inferred from this maintenance work.

## Decisions

- The binary will consume the existing public `trading_server` library instead
  of declaring the same six source modules again as private binary modules.
  Preserve the CLI enum, argument names/defaults, and dispatch destinations.
- Preserve all public library APIs, exchange/dashboard event payloads, state and
  risk interfaces. Binary-only reachability diagnostics do not justify deletion.
- Remove compiler-confirmed unused imports and genuinely unused private storage
  or unreachable private helpers. Do not activate any order, signing, exchange,
  deployment, or socket path merely to make a symbol used.
- Keep the effectful migration database initialization and its pool lifetime;
  use an explicitly retained unused binding. Remove the unused parameter from
  the private sell handler and its sole call site.
- Keep the Bithumb constructor signature but stop retaining configuration that
  its current stub adapter never consumes. Existing stub behavior remains
  unvalidated and must not be described as authenticated trading.
- Add package-scoped Cargo `warnings = "deny"`, supported since Cargo 1.74,
  without imposing project lint policy on third-party dependencies. No broad
  lint suppression, new public getters, or dependency pruning is needed.
- Add parser-only regression tests for all CLI modes, defaults, overrides,
  missing required values, and help. Extend the network-disabled Docker help
  smoke check to `migrate --help`; never execute migrations as a help check.
- Validate using the same Rust 1.99.0 toolchain pinned in the existing Docker
  build. Keep Cargo.lock and the documented package MSRV unchanged; actual
  execution evidence applies to Rust 1.99.0, not an untested minimum version.

## Verification

Run the actual Rust test suite and optimized build with the committed lockfile,
verify the lint policy rejects a deliberate warning in an isolated offline probe,
and inspect compiler diagnostics. Run parser compatibility tests without server
constructors, database writes, exchange clients, or deployment execution. Verify
Python CI-contract changes and required Python/lint gates. Obtain independent
review before publishing, verify uploaded file hashes, and wait for the exact
published commit's complete CI including Docker and coverage artifacts.

## Primary references

- https://doc.rust-lang.org/cargo/reference/manifest.html#the-lints-section
- https://doc.rust-lang.org/stable/book/ch07-01-packages-and-crates.html
- https://rust-lang.github.io/rustup/installation/index.html
