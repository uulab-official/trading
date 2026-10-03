# Paper execution milestones

Upstream baseline: `uulab-official/trading` main
`5f375046819da3441d354faae61f3a6d869ebbb7`.

## Milestone 1: execution isolation

- Credential-free paper exchange and fail-closed paper/live selection
- Explicit user activation guard before live adapter construction
- No paper secret-file, Telegram, or WebSocket startup
- Separate paper state path, immediate-fill response handling, and verified CI app path
- 17 offline safety regressions

## Milestone 2: deterministic offline replay

- Restore pure offline candle/buffer/indicator interfaces and their tests
- Consume completed OHLCV bars without duplicate strategy signals
- Add a usable local fixture CLI and synthetic demonstration fixture
- Execute signals causally at the next bar's open
- Track fees/slippage, cash, marked holdings, stable orders, and checkpoint state
- Validate resume against authoritative prefix replay and reject corrupt state
- Add bar-close entry risk limits and reproducibility assumptions to reports
- Preserve milestone 1's safety regressions

Both milestones are local changes awaiting publication approval. No remote branch,
push, deployment, live account connection, real credentials, or live orders were
used. See OFFLINE_PAPER_RUNNER.md for the verified path and remaining limitations.
