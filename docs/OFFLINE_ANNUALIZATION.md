# Elapsed-time annualization

The existing backtest engine formerly truncated its elapsed period to whole
days. A 55-minute flat-price round trip with 1% fees and 0.5% slippage loses
0.3000% of initial capital, but the old CAGR field returned zero. Fractional-day
tests also overstated the annualization exponent for longer fixtures.

This milestone changes only CAGR and its compatibility `annualized_return`
alias. Other legacy risk statistics remain unverified.

## Contract

This contract covers the existing engine's supported offline inputs: validated
candles/timestamps and ledger equity that can be represented by its finite
float equity-curve reporting. The private numerical helper is not an
arbitrary-scale Decimal arithmetic API. Selected extreme-value regressions
protect known intermediate failures; they do not extend that input domain.

- Initial capital is available at the first supplied candle's open. Final equity
  is cash plus remaining holdings marked at the last supplied candle's close
- Candle timestamps identify opens. The end timestamp adds one duration of the
  strategy's validated timeframe to the last timestamp. The elapsed interval
  includes supplied gaps and fractional days; config's default timeframe does
  not override the strategy timeframe
- A year means exactly 365.25 days, or 31,557,600,000 milliseconds
- CAGR percent equals `100 * ((final_equity / initial_equity) **
  (year_ms / elapsed_ms) - 1)`. Initial equity must be finite and positive,
  final equity finite and nonnegative, and elapsed milliseconds a positive integer
- The equity ratio includes actual successful-fill fees and configured slippage.
  Open holdings are marked without hypothetical liquidation or exit fees
- Zero final equity is the defined annualized loss -100%. Unchanged equity is
  the defined return zero. Negative equity, invalid initial equity, and zero or
  negative duration are undefined
- Calculations use the same fixed 28-digit Decimal context as the ledger. A
  result outside the finite compatibility JSON numeric range is undefined
- Undefined CAGR and its alias are Python `None`, JSON `null`, and CLI `N/A`,
  with an explicit reason. They are never fabricated zero or JSON infinity
- Result `annualization` metadata gives the start/end Unix milliseconds,
  elapsed milliseconds, year convention, percent unit, valuation basis,
  defined/undefined status, reason, and interpretation warning

An annualized return is mathematical extrapolation of historical net marked
equity. It does not estimate future returns. The CLI and offline JSON command
show this warning alongside the result, and explicitly flag intervals shorter
than one year. A short loss can approach -100% when annualized and a short gain
can exceed numeric range; neither implies a plausible yearly trading outcome.

## Run and verify

With the existing Python/PyYAML/pandas/numpy runtime, from the repository root:

```sh
python tools/offline_checks.py --include-backtest
python tools/offline_trade_report.py > report.json
```

The standalone report includes exact Decimal-string `cagr_pct` (or null) and
annualization metadata. Socket connections, network clients, and DNS are
forbidden, including during imports. The fixtures remain synthetic accounting
examples and do not establish strategy profitability. The checkpointed paper
runner, its ledger, raw fills, fees, and FIFO realized reporting are unchanged.

## Remaining limits

This does not validate volatility, Sharpe, Sortino, Calmar, VaR/CVaR, skewness,
kurtosis, drawdown duration, irregular-return sampling, or optimization methods.
Those fields retain the existing unverified labels and are omitted from the
standalone offline report. In particular, fixing CAGR does not change the
legacy Calmar formula or hourly risk scaling.

Full legacy pytest/CI, Rust/Docker, live brokerage, credentials/orders,
deployment, and the unidentified stock project remain outside this milestone.
Arbitrary-scale direct helper calls near Decimal's minimum exponent can still
lose relative changes and are outside the supported engine/reporting domain.
The old CLI/data loader may fetch missing exchange data; use the standalone
offline report or directly supplied local DataFrames for offline execution.
