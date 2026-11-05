# Causal SMC market structure (initial implementation)

Module: `packages/strategy/smc_structure.py`.

Implements strictly confirmed swing highs/lows and close-based break-of-structure (BOS) / change-of-character (CHOCH). Swings are emitted at the confirmation candle timestamp, with the historical pivot timestamp recorded separately. Equal highs/lows are not swings. The first break establishes trend (BOS); a break in the opposite direction is CHOCH. Each confirmed level breaks only once until a new pivot replaces it.

Run `python -m unittest discover -s tests -p test_smc_structure.py -v`.

This is an indicator-only offline module. It does **not** send orders, predict profitability, connect to a live feed, or validate trading readiness. Next steps: integrate with strategy interfaces, add FVG/liquidity-sweep signals, verify candle-close semantics against live adapters, and add backtest comparisons with fees and slippage.
