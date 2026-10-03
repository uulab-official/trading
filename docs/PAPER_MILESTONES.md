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

Milestones 1 and 2 were published to main in
`07442cfbd3ab36b958c1e1ce7bef47d36567bcfe`. Image publication is manual only;
ordinary main pushes do not activate trading or publish an image.

## Milestone 3: existing backtest accounting

- Queue completed-bar market signals for the next available open
- Use only then-known fill inputs and validate the full candle chronology first
- Make spot rejection, cash/assets, successful fees, and pending order cleanup atomic
- Report marked final equity separately from cash and calculate return from equity
- Preserve the existing broker/engine and five original backtest assertions
- Reset the MA-cross and RSI private crossing state for repeated experiments
- Keep the base paper checks and offer extended offline backtest checks

See OFFLINE_BACKTEST_ACCOUNTING.md for the tested accounting contracts and legacy
statistics that remain unverified. No live account connection, credentials,
orders, deployment, or trading activation were used for these milestones.

## Milestone 4: net realized reporting

- Replace latest-buy price pairing with per-symbol FIFO matching of exact fills
- Allocate entry fees and cost across partial exits without double counting
- Separate raw fill count, completed exit count, and unmatched open holdings
- Distinguish gross quote-unit PnL from fee-inclusive net PnL and return percentages
- Treat a no-loss profit factor as undefined throughout reports and consumers
- Add a runnable network-forbidden local fixture report command
- Preserve the existing cash/holdings ledger and all earlier offline checks

See OFFLINE_REALIZED_REPORTING.md for count/unit definitions and remaining limits.
