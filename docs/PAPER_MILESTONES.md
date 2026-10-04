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

## Milestone 5: elapsed-time annualization

- Fix sub-day zero CAGR and whole-day truncation using elapsed milliseconds
- Use first-open capital and last-close net marked equity, including supplied gaps
- State the strategy timeframe, fixed 365.25-day year, percent unit, and valuation basis
- Preserve actual fees/slippage and classify invalid or unrepresentable values as undefined
- Show an explicit mathematical extrapolation warning alongside CLI/JSON results
- Preserve the earlier offline safety, checkpoint, ledger, and FIFO checks

See OFFLINE_ANNUALIZATION.md for the tested contract and remaining risk-statistic limits.

## Milestone 6: checkpoint writer ownership

- Prevent competing paper runs from rolling saved progress backward
- Hold one-writer ownership across load, authoritative validation, replay, and save
- Resolve aliases to one canonical checkpoint and reject unsafe lock files
- Release ownership after completion, failed writes, cancellation, and process exit
- Preserve atomic checkpoints, restart accounting, paper defaults, and live guards
- Add eight network-forbidden concurrency/recovery checks to the offline aggregate

See OFFLINE_PAPER_RUNNER.md for the POSIX/local-filesystem contract and retry steps.

## Milestone 7: reliable paper CLI completion and recovery

- Give the installed `trading-paper` command an integer-returning process wrapper
- Preserve the Python `main(argv)` result dictionary and module/console JSON parity
- Report invalid input, checkpoint ownership, local file failures, and interruption
  with documented nonzero exit codes and bounded recovery guidance
- Preserve checkpoints and their authoritative resume checks on expected errors
- Exercise real subprocess entrypoint semantics with child-level network guards
- Verify local wheel-generated launchers and interrupted replay/resume

See OFFLINE_PAPER_RUNNER.md for exit codes and manual retry steps. No accounting,
strategy, lock algorithm, live guard, or automatic deployment behavior is changed.

## Milestone 8: first-interrupt checkpoint boundary

- Yield only after a completed bar and atomic checkpoint so the first SIGINT
  cancellation is delivered before the next bar can mutate the paper ledger
- Reproduce the former run-to-end behavior through a locally installed launcher
- Check early/buy/sell/final boundaries, interruption before any checkpoint, and
  one/two SIGINTs during atomic replacement using real subprocesses
- Verify exit `130`, exact restart, stable fixture timestamps despite a changed
  Python `time.time()` value, no duplicate fills, and lock release with network/DNS forbidden

See OFFLINE_PAPER_RUNNER.md for the safe-boundary contract and latency limits.
