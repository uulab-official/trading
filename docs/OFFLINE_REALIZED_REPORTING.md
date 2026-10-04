# Fee-inclusive realized trade reporting

This milestone corrects the existing backtest engine's closed-trade statistics.
The former calculation paired every sell with the latest buy price, ignored
quantities and fees, and used buy-plus-sell fill count as the win-rate denominator.
For example, a buy at 100 and sell at 101 with 1% fees on each side loses 1.01
quote units. It now counts as one completed loss.

## Runnable offline report

From the repository root, with the already used Python/PyYAML/pandas/numpy runtime:

```sh
python tools/offline_checks.py --include-backtest
python tools/offline_trade_report.py > report.json
```

The report command runs the original BacktestEngine and MA-cross strategy on the
existing 90-bar synthetic fixture. Socket connections, network clients, and DNS
are forbidden, including during package imports. There is no broker credential,
live activation, network-data fallback, installation, or deployment step.
Use `--fixture path/to/local.json` for a supplied paper-mode fixture in the same
format. A live-mode fixture fails before simulated execution.

The demonstration prices establish accounting behavior only. Its returns do not
establish strategy profitability. The existing paper runner remains the command
for checkpointed offline replay and restart validation.

## Definitions and units

- `total_trades` in BacktestResult remains the successful raw fill count for
  compatibility. `fill_count` in its detailed report means the same thing
- `closed_trade_count` counts successful sell fills. A sell matching several buy
  lots is one closed exit; a partial sell is also one closed exit
- FIFO consumes the earliest remaining buy lot for the same symbol. Open lots
  remain open and never count as realized trades, wins, or losses
- Entry notional and entry fees are allocated proportionally when part of a lot
  is sold. The final portion receives the remaining Decimal amount so fees and
  cost cannot be counted twice or silently discarded
- Gross realized PnL is exit notional minus matched entry notional
- Net realized PnL also subtracts the matched entry fees and the actual exit fee
- Monetary values use the common symbol quote currency. Mixed quote currencies
  raise an error because this report models no currency conversion
- Net return percent is net PnL divided by matched entry notional plus entry
  fees, multiplied by 100
- Win/loss/break-even counts classify strictly positive/negative/zero net PnL
- Win rate divides winning closed exits by all closed exits, including break-even
  exits. It is zero when there are no closed exits
- Average win, loss, trade, and the compatibility `expectancy` field use net
  return percentages per closed exit. Expectancy is the historical arithmetic
  mean, not a prediction; observations have equal weight
- Profit factor divides positive realized net quote-unit PnL by absolute
  negative realized net quote-unit PnL. With no negative realized PnL it is
  undefined: Python None, JSON null, and CLI N/A. It is not a fabricated zero
  or infinity. A loss-only report has the defined value zero

Raw fill dictionaries, cash, holdings, and accumulated fees retain their existing
behavior. Reports use separate immutable Decimal records captured only after
successful fills, avoiding float round-trip drift in the raw compatibility
history. Repeated report calls do not mutate the account. Broker reset clears
these records. Repeated engine runs preserve economic reporting; the existing
random raw order IDs and wall-clock fill metadata are not replay identities.

Engine timestamps are validated integer milliseconds. Direct broker callers
should supply copyable timestamp metadata. It is snapshotted on successful fills
and copied into each report, so editing a returned metadata container cannot
change raw fill metadata or a later report. Financial fields and lot matches
are frozen; an arbitrary returned timestamp container itself may remain mutable.

The broker's existing 28-digit holding arithmetic can round a credited or debited
quantity. Each successful fill records its actual before/after holdings. FIFO
reconciles only that recorded quantity residue using exact inventory arithmetic,
and the report exposes it in `inventory_rounding`. A known zero holding consumes
all remaining lot cost and fee residue. Zero-quantity cost lots stay attached
until matching or a known full close; genuine tiny positive holdings stay open.
There is no blanket epsilon and the account ledger is not changed.

The exact detailed report uses Decimal strings for amounts and percentages.
BacktestResult's existing scalar JSON fields retain numeric compatibility and
add gross/net realized PnL, closed-exit count, break-even count, and this detailed
report. CLI labels show fills separately from exits. The existing optimizer
preserves undefined profit factor without turning a valid return into an error;
it rejects a ranking metric when every candidate lacks a defined finite value.

## Remaining limits

The legacy hourly risk scaling, sparse-sample statistics, drawdown-duration
assumptions, and optimization methodology remain unverified. A separate
[elapsed-time annualization milestone](OFFLINE_ANNUALIZATION.md) corrects CAGR
and its alias with explicit period/units and warnings. The standalone report
includes that mathematical annualization and omits the other risk statistics;
the legacy CLI labels the other risk statistics as unverified.
FIFO here describes simulated spot reporting and does not validate other
accounting methods or exchange execution behavior.

The broker still has documented OHLCV limit/slippage approximations and unused
partial-fill/delay settings. The older backtest CLI/data loader can fetch data
when a CSV is missing and is not the offline report command. Live market data,
Rust/Docker readiness, exchange compatibility, the old TradingBot Portfolio
path, and the unidentified stock project remain outside this milestone.
