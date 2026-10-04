# Legacy realized daily-loss limits

This note covers the existing `Portfolio` / `RiskManager` path. The offline
`PaperRunner` has a separate authoritative ledger, marked-equity loss limits,
and verified checkpoint replay. Passing these legacy checks does not establish
that the legacy application is safe for live operation.

## Repaired sign contract

For a positive daily starting value:

- `Portfolio.get_daily_pnl_pct()` is signed net realized PnL divided by the
  recorded daily starting value, times 100. Losses are negative;
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

## Nonpositive-equity entry guard

`RiskManager.check_trade_allowed()` rejects `side='buy'` when
`Portfolio.get_total_value()` returns zero or a negative value. This also covers amount-zero buy signals,
which the application uses before calculating a final entry size. The rejection
does not mutate the portfolio and reports that account equity must be positive.

This guard does not change sell requests, reporting, status probes, or direct
local position close operations. Existing guards still apply: the duplicate-symbol
guard can still reject a SELL request for a held symbol, while direct local
`Portfolio.close_position()` remains callable. This is not a general repair of
legacy exit routing. `is_trading_allowed()` is
an existing generic probe and remains unchanged, so it must not be treated as
authorization for a particular new order. Each buy still needs its entry check.

## UTC daily coordination and explicit reset

Portfolio owns a nondecreasing, in-memory UTC processing date. A valid local
`close_position()` establishes the current period before changing cash or
recording realized PnL. `RiskManager.reset_daily_stats()` delegates to that same
period owner and then aligns its counters, circuit latch, and cooldown. Its
existing check/report paths still perform that coordinated reset.

Automatic rollover occurs only when the observed UTC date is later than the
recorded date. An explicit aware observation is converted to UTC first; naive
observations retain the existing UTC meaning. Same-day repeated checks, a different host-local date, and a clock
rollback followed by restoration do not clear losses or reset the baseline. The
baseline is the existing `get_total_value()` result at the first observed rollover,
not a reconstructed price at midnight. No valuation semantics are changed.

A returned `Trade.accounting_date` tags the in-memory processing period. The
legacy application explicitly passes it to
`on_position_closed(pnl, accounting_date=trade.accounting_date)`. The callback
aligns risk counters to the portfolio's already-established period without
resetting portfolio PnL. If another check or close has advanced the period, a
callback tagged with an older date leaves the current counters, latch, and
cooldown alone. Malformed or future callback dates are rejected without advancing
the period. A supplied `exit_time` remains display/history metadata and does not
select the accounting period.

The one-argument callback remains compatible for immediate, sequential callers.
It cannot identify a delayed old close once another operation advances the
portfolio. Tagged dates address cross-period callbacks, not duplicate-event
idempotency, arbitrary same-day reordering, or concurrent portfolio writers.

`Portfolio.reset_daily()` still explicitly rebases to `get_total_value()` and
clears accumulated realized PnL even within the same day, without changing cash
or positions. It does not move the date backwards. As before, a same-day explicit
portfolio reset does not clear RiskManager's trade counters or circuit latch.
Signed report values and nonnegative loss magnitude retain their separate roles.

The date tag is not added to the existing portfolio JSON, SQLite/state-store
payload, notification payload, or risk-report keys. It is memory-only; this does
not repair restart recovery.

## Remaining high-priority limitations

The legacy path still must not be used for live trading:

- Zero baseline retains the legacy numeric result of zero, which is not an
  assertion that a funded order is safe. Negative baselines are not meaningful
  return denominators. The new entry guard rejects a nonpositive
  `get_total_value()` result without changing or normalizing balances. NaN,
  infinity, and a negative daily baseline paired with positive reported equity
  are outside this guard's contract. Broader malformed-state validation and
  recovery remain outside this repair
- Zero price marks have an existing valuation fallback: `Position.position_value`
  substitutes entry price when the current mark is zero. Consequently actual
  marked equity can be zero while `get_total_value()` returns a positive value
  and this guard cannot reject the entry. Repairing and testing that valuation
  defect is a separate P1; this change does not alter valuation or mark handling
- Legacy `Portfolio.to_dict()` / `from_dict()` does not preserve daily realized
  PnL or its UTC date. Restart recovery for this path is not validated by these
  checks and must not be confused with the separate paper runner's checkpoint
- Live adapters, credentials, services, exchanges, and deployment are not
  exercised. These are offline arithmetic and control-flow checks only

`tools/offline_checks.py --include-backtest` includes the focused daily-loss
contracts with socket connections and DNS forbidden. The tests cover zero,
profit, loss, threshold boundaries, net recovery, circuit latch, explicit reset,
the realized/unrealized distinction, UTC first-close/check ordering, tagged delayed
callbacks, repeated resets, local-date differences, clock rollback, unchanged
saved/report schemas, and the actual application close method with inert local
order/notification collaborators.
