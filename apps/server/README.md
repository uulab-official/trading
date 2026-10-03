# Trading server Python entrypoints

The verified offline entrypoint is `trading-paper`, implemented in
`trading_bot/paper_runner.py`. From the repository root, see
[Offline paper runner](../../docs/OFFLINE_PAPER_RUNNER.md).

`trading-bot` remains the separate legacy application. Its live feed is unavailable
in the restored offline data package, and it must not be described as validated
live operation. The Rust source/deployment workflow is a separate unvalidated path.
