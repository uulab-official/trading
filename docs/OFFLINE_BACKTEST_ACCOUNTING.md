# Offline backtest accounting

This milestone repairs the existing `BacktestEngine` and `VirtualBroker` on top
of main `07442cfbd3ab36b958c1e1ce7bef47d36567bcfe`. It uses supplied local candles
and the real existing strategy classes. It does not connect to an exchange.

## Verify without network access

The base paper checks still need only Python 3.10+ and PyYAML. The optional legacy
backtest checks additionally need pandas and numpy, already present in the
validation runtime. No dependency installation was used.

```sh
python tools/offline_checks.py
python tools/offline_checks.py --include-backtest
```

Both commands forbid socket connections, network clients, and DNS. The extended
command includes the five original backtest tests, the broker accounting
regressions, and engine regressions. The original backtest assertions are
unchanged; their classes also support unittest so pytest is optional for these
checks. Full legacy pytest, CI, and Docker validation remain separate.

## Timing and financial assumptions

- A completed bar creates a signal after its close. The resulting market order
  first becomes eligible at the next available bar's open
- A final-bar signal stays pending and cannot fill without a later bar
- Quantity is sized from cash and the signal bar's known close. A price gap may
  make the order unaffordable; the broker then rejects the complete order
- Market slippage is the configured fixed percentage. The fill does not use the
  next bar's eventual range, closing price, or total volume
- The existing minimum-volume gate uses the previous completed bar's supplied
  volume as an explicit model assumption. It is not actual next-open liquidity
- Fees accrue only for successful fills. Rejected buys and oversells preserve
  balances, holdings, prior trades, accumulated fees, and unfilled metadata
- Terminal orders leave the pending queue. Pending low-volume or unreached
  limit orders stay queued, and an already filled order cannot fill twice
- `final_balance` remains cash. `final_equity` is cash plus open holdings at the
  last supplied close, and `total_return_pct` uses that equity
- Valuation does not assume liquidation or charge hypothetical exit fees
- A direct broker summary can receive explicit current prices. Otherwise it
  uses the last prices recorded by fills or successful valuation calls; the
  caller must refresh stale marks. Missing holding marks raise an error
- Accounting uses fixed 28-digit Decimal precision and HALF_EVEN rounding
- The complete fixture is validated before simulated execution. Duplicate,
  overlapping, reversed, malformed, or nonfinite candles fail before fills
- Each engine run resets its results and invokes the strategy's reset hook.
  Real MA-cross and RSI regressions verify their private crossing state resets
- SELL and CLOSE signals exit existing spot holdings. No shorts are created

This is an emulator without exchange tick/lot limits, partial fills, latency,
order-book depth, or guaranteed loss protection. Limit orders retain the legacy
completed-OHLCV approximation, including optional volatility-based slippage;
their intrabar sequence and limit-price guarantees are not established. Stop
orders are rejected as unsupported. Legacy partial-fill/delay settings remain
unused and do not simulate those features.

## Remaining limits

The separate [realized reporting milestone](OFFLINE_REALIZED_REPORTING.md) now
uses fee-inclusive FIFO matching, closed-exit counts, and explicit gross/net
units. Unmatched open lots remain excluded from realized results. The risk
metrics retain their older hourly scaling, sparse-sample behavior, and
drawdown-duration assumptions. The separate [elapsed-time annualization](OFFLINE_ANNUALIZATION.md)
milestone corrects CAGR and its alias, with explicit duration/units and a
mathematical extrapolation warning. Use the verified cash/holdings/fees/equity ledger
for this milestone; do not treat those legacy statistics as profitability
evidence.

The old backtest CLI and `HistoricalDataLoader.prepare_data` may fetch exchange
data when a local file is missing. They were not executed by this validation;
the checks pass DataFrames directly to the engine. The deterministic fixture
paper runner remains the verified runnable offline entrypoint.

Live market data, live brokerage integration, real credentials/orders, Rust,
deployment, and the old TradingBot Portfolio path remain outside this milestone.
Custom strategies must reset their own private state through their reset hook.
