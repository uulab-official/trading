# Legacy realized daily-loss limits

This note covers the existing `Portfolio` / `RiskManager` path. The offline
`PaperRunner` has a separate authoritative ledger, marked-equity loss limits,
and verified checkpoint replay. Passing these legacy checks does not establish
that the legacy application is safe for live operation.

## Repaired sign contract

For a positive daily starting value:

- `Portfolio.get_daily_pnl_pct()` is signed net realized PnL divided by the
  explicitly recorded daily starting value, times 100. Losses are negative;
  gains are positive
- `Portfolio.get_daily_loss_pct()` is the nonnegative loss magnitude:
  `max(0, -daily_pnl_pct)`. Profits and break-even therefore produce zero loss
- The existing daily-loss and circuit-breaker comparisons use loss magnitude
  and retain their inclusive `>=` thresholds
- `RiskManager.get_risk_report()['daily_pnl_pct']` remains signed, preserving
  the existing negative-loss warning in the legacy application
- Portfolio statistics named `daily_loss_pct` report loss magnitude. This is
  intentionally distinct from a signed return or PnL percentage

The calculation uses net realized PnL, so a loss of 50 followed by a gain of 30
against a starting value of 1,000 leaves a loss magnitude of 2%. Unrealized mark
changes do not become realized losses. An already triggered circuit breaker
stays latched during the same day even if subsequent realized gains recover
the loss. No threshold, position-size guard, or available-cash guard is relaxed.

The original successful-purchase test used 1,000 cash for a 5,000 purchase. Its
fixture now supplies enough cash and asserts the deduction. The daily-loss test
uses a 5% order notional so the separate 10% size guard cannot mask its outcome.

## Explicit reset and remaining high-priority limitations

`Portfolio.reset_daily()` retains its existing explicit contract: set the new
baseline to current marked portfolio value and clear accumulated realized daily
PnL without changing cash or positions. Tests explicitly coordinate it with a
date change in `RiskManager.reset_daily_stats()`; no automatic connection was
added between the two reset methods.

The legacy path still must not be used for live trading:

- Automatic UTC-day coordination is unresolved. `RiskManager.reset_daily_stats()`
  resets its own counters and latch but does not reset the portfolio. Connecting
  those calls naively can erase a loss when the first action after midnight is
  a position close. This needs its own API-level regression and lifecycle fix
- Zero baseline retains the legacy numeric result of zero, which is not an
  assertion that a funded order is safe. Negative baselines are not meaningful
  return denominators. Existing legacy risk checks can allow positive orders
  for nonpositive account equity; fail-closed validation is a separate pending
  safety fix. This change does not silently normalize invalid balances
- Legacy `Portfolio.to_dict()` / `from_dict()` does not preserve daily realized
  PnL or its UTC date. Restart recovery for this path is not validated by these
  checks and must not be confused with the separate paper runner's checkpoint
- Live adapters, credentials, services, exchanges, and deployment are not
  exercised. These are offline arithmetic and control-flow checks only

`tools/offline_checks.py --include-backtest` includes the focused daily-loss
contracts with socket connections and DNS forbidden. The tests cover zero,
profit, loss, threshold boundaries, net recovery, circuit latch, explicit reset,
and the realized/unrealized distinction.
