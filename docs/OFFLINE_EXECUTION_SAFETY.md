# Offline execution safety patch

Source reviewed: `uulab-official/trading`, `main` commit
`5f375046819da3441d354faae61f3a6d869ebbb7`.

## What this patch establishes

- Paper is the default configured mode. The Python bot selects `PaperExchange`,
  an in-memory spot emulator with no credentials, HTTP client, or network fallback
- Paper ignores the secret file and disables Telegram and WebSocket startup
- Unknown execution modes fail closed
- The existing live adapter is preserved. Selecting live mode alone cannot
  construct it: valid credentials, non-sandbox/non-testnet configuration, and an
  explicit user runtime flag are required. Live behavior has only mocked tests
- Paper state uses `data/paper_bot_state.db`, separate from the existing live file
- Orders filled immediately by the paper emulator are recorded as filled by the
  existing order manager. Nullable fields in mocked live responses remain safe

## Offline verification

Run only the targeted safety tests, not the bot or setup/deployment scripts:

```sh
python -m unittest discover -s packages/core/tests -p '*execution_safety.py' -v
python -m compileall -q packages/core/trading_core apps/server/trading_bot packages/core/tests
git diff --check
```

The tests forbid live-adapter construction/order submission and socket connection
on paper paths. The bot initialization/start checks are method-level tests with
inert state/data dependencies; they use the real execution factory and portfolio.
The live selection and live order response tests use mocks only.

`PaperExchange.set_price()` and `set_ohlcv()` accept local fixtures. Missing
fixtures raise a clear error or return no candles; they never fetch external
data. The emulator supports market buys/sells only, checks available cash/assets,
and deduplicates repeated local client order IDs. Fills are immediate and
frictionless. They do not model fees, slippage, exchange tick/lot limits, partial
fills, or realistic liquidity, and cannot establish profitability.

## Still incomplete

This is a safety milestone, not a completed trading application. No bot, broker
connection, real credentials, live orders, deployment, or remote push was used.

- `packages/data` is absent in the reviewed main tree, so the original strategies,
  application imports, and backtest suite cannot be validated end to end
- CI's migrated application path is corrected to `apps/server`; the missing data
  package and Python-vs-Rust Docker checks are not silently skipped or repaired
- The Rust server has separate scaffolding and is not covered by this Python gate
- A user-facing offline fixture/replay runner, end-to-end signal handling, restart
  reconciliation, and application-level risk controls still need validation
- The bot's existing Portfolio uses signal/caller prices rather than confirmed
  exchange fill prices. Different fixture prices can diverge from the new paper
  ledger; a complete open/close/restart cycle has not been verified
- Existing backtests need separate fixes: closed-bar signals currently fill at
  that same bar's open; reported final return excludes open holdings; rejected
  buys accrue fees; overselling is not consistently prevented

Do not describe this patch or the existing README's “Production Ready” label as
evidence that live trading is ready or that a strategy will make money.
