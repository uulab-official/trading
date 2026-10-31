# Quant validation gates

## Implemented primitives
- Confirmed swing / BOS / CHOCH: packages/strategy/smc_structure.py
- Broker-neutral paper-only contracts: packages/core/broker_interface.py
- Basic performance metrics: packages/strategy/quant_metrics.py
- Chronological rolling/anchored walk-forward index folds with embargo: packages/strategy/walk_forward.py

These are isolated primitives, **not an integrated production strategy**.

## Required next gates
1. Map candle timestamps to walk-forward folds; purge labels whose outcome horizon overlaps validation. An embargo alone does not remove overlapping-label leakage.
2. Train and select hyperparameters only on each fold's training segment. Test segments must not influence selection.
3. Rebuild a unified event-driven backtester with next-bar fills, commissions, spread, slippage, fractional share and exchange-specific rounding.
4. Test delistings, missing data, survivorship bias, corporate actions, trading calendars, market sessions, stale feeds and timezone conversion.
5. Persist order intents, broker responses, fills, cash and holdings atomically. Reconcile broker state after restart and network failure.
6. Connect real broker sandbox adapters only after validating official specifications and authentication. Live order paths remain disabled.
7. Add real dashboard APIs and frontend connected to persisted state, not demo numbers.
8. CI: run focused unittest modules and legacy offline checks; resolve failures before merge.

## Evaluation report contract
Per fold: train/test date ranges, instrument universe, strategy version, parameter hash, number of trades, turnover, net P&L after all costs, MDD, Sharpe, exposure, missing-data rate. Aggregate out-of-sample performance separately from in-sample optimization. No guaranteed returns.
