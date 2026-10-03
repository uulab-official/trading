# Deterministic offline paper automation

This workflow runs locally on supplied historical/synthetic candles. It does not
use brokerage accounts, credentials, remote prices, Telegram, WebSockets, or live
orders. It is an offline paper-mode program, not a profitability forecast.

## Run the verified workflow

From the repository root:

The offline runtime needs Python 3.10+ and PyYAML 6+. The minimal dependency is
listed in `requirements-offline.txt`; this excludes live broker/data clients.
The validation runtime already supplied PyYAML, so no installation was performed.

```sh
python tools/offline_checks.py
PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server \
  python -m trading_bot.paper_runner config/examples/offline_fixture.json \
  --checkpoint data/paper_replay_checkpoint.json
```

If installed with the project's Python dependencies, the same entrypoint is
`trading-paper`. No dependency installation was used for the recorded validation.
The provided 90-bar fixture is synthetic and demonstrates a simulated buy/sell
cycle only. Its prices and returns are not market evidence.

Re-running the same command validates and resumes the existing checkpoint. It
does not duplicate completed fills. To start a different experiment, supply a new
checkpoint path. Changing previously processed candles, strategy parameters,
fees, or risk settings is rejected without overwriting the saved checkpoint.
Appending valid future candles is supported.

## Inputs and options

The fixture is a JSON object:

```json
{
  "mode": "paper",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "candles": [[1700006400000, 100, 110, 90, 105, 1000]]
}
```

Each row is opening timestamp in milliseconds, open, high, low, close, and volume.
Prices must be finite and positive; volume is finite/nonnegative; OHLC bounds must
be consistent. Completed bars must be chronological and non-overlapping for the
fixed timeframe. Gaps are allowed; missing bars are not invented.

The runner supports one USDT-quoted spot symbol and one position, with no leverage
or short selling. Defaults are illustrative assumptions:

- `--strategy ma_cross` or `rsi`
- `--fast-period 3`, `--slow-period 5`, `--rsi-period 14`
- `--initial-balance 10000`, `--position-size-pct 20`
- `--fee-pct 0.1`, `--slippage-pct 0.05`
- `--max-daily-loss-pct 3`, `--max-drawdown-pct 10`

MA slow periods can be at most 500; RSI periods at most 499, matching the bounded
500-bar strategy history. Existing strategies wait for at least 50 completed
bars, and RSI additionally waits for the full period+1 Wilder seed. Quantities
are rounded down to eight decimal places for this emulator; this is not a claim
about an exchange's lot-size or minimum-order rules.

## Execution, accounting, and restart

- Completed-bar signals execute at the following available bar's opening price
- A signal on the final supplied bar remains pending until a future bar is added
- Explicit slippage adjusts buy prices upward and sell prices downward
- Fees are charged only on successful fills; unfunded buys/oversells are rejected
- One authoritative paper ledger tracks cash, holdings, trades, and fees
- Equity/return include open holdings marked at the last supplied close
- Fixed 28-digit Decimal precision and HALF_EVEN accounting make replay stable
- A checkpoint is atomically written after each completed bar
- Resume rebuilds indicators and independently replays the verified prefix,
  then compares all economic, risk, and pending-signal state before continuing
- Saved trade chronology is independent of JSON dictionary key order

Daily loss and drawdown controls block new buys based on bar-close equity and
still permit exits. Daily loss tracking resets on the fixture's UTC trading day;
the drawdown halt stays latched for the experiment. These controls do not model
intrabar stops, forced liquidation, latency, or guaranteed loss caps.

## Verification and remaining limits

`tools/offline_checks.py` runs the targeted safety, data, strategy, ledger, and
runner suites while socket connections and DNS are forbidden. It includes the
CLI entrypoint, real existing MA strategy, next-open execution, fees/slippage,
marked holdings, corrupted checkpoint rejection, and restart before/after fills.

This does not validate a live trading system. Existing live adapter code remains
guarded by exact live configuration and an explicit runtime activation flag, but
the restored data package deliberately raises `LiveMarketDataUnavailable` for
live feed construction. Rust/server deployment, real exchange compatibility,
credentials, account setup, orders, and live activation were not exercised.

The old backtest engine uses a separate broker and was not repaired by this
milestone. Its same-bar fills, open-holding return calculation, rejected-order
fees, and oversell behavior still need separate regression fixes. The old
`TradingBot` Portfolio path also remains separate from the authoritative fixture
runner and has not been declared accounting-complete. Full legacy pytest/CI and
Docker builds were not validated; pytest/ccxt are absent from the local runtime.
