# Paper pre-trade risk gate

Pure module: `packages/core/risk_gate.py`.

Rejects invalid/nonfinite numbers, stale or missing market data, halted state, daily-loss threshold, per-order notional limit, insufficient available cash and gross-exposure limit. This is a **building block**, not a broker-enforced risk system. The caller must reserve cash/exposure atomically for outstanding orders, reconcile fills, evaluate sell orders separately, and re-check risk before sending an order. Risk snapshots must be authoritative and include fees and outstanding reservations.

The module does not accept broker credentials, contact exchanges, or enable live trading.
