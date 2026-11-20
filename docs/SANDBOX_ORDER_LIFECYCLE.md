# Offline sandbox order lifecycle fault-injection

`packages/core/trading_core/sandbox_order_emulator.py` is a deterministic,
**in-memory broker test double**, not an Upbit or other exchange client.
It has no network, API-key, account, or real order methods. Its simulated
`sandbox-*` IDs and `sim-*` hashes are NOT authentic broker evidence.

## What it tests

- Market order intent accepted once under a unique client ID
- Partial fills with precise Decimal executed quantity, quote gross and fees
- Overfill and invalid quantity rejection before any state mutation
- Cancellation after a partial fill without erasing the executed portion
- Lost acknowledgment where the broker may have accepted an order but the
  caller never receives a response
- Reopening the **separate** durable SQLite order-intent journal with a
  pending/uncertain state that blocks other trade intentions

Run the isolated regression tests with no network calls:

```sh
python tools/offline_checks.py

# Or with the core package installed:
python -m unittest packages/core/tests/test_sandbox_order_emulator.py
```

## Critical integration boundary

The emulator does **not** update the journal automatically, model real
broker order limits, or reconcile account balances. A real broker interface
must separately implement authenticated order lookup, verified execution
IDs, remaining quantity, partial-fill fee currencies, rejection reasons,
rate limits, and timeout behavior.

The journal's current `reconcile` records only final outcomes
(`filled`, `canceled`, `rejected`). That is **not sufficient for
a production partial-fill ledger**: a canceled order may have already
executed quantity and incurred fees. Do not attempt to infer cash or
holdings from these coarse final statuses. Implement a durable fill
event ledger and broker statement reconciliation first.

The lost-ACK test intentionally NEVER resubmits the request with the
same ID. A new order must remain blocked until independent, authenticated
broker data demonstrates the prior intent's outcome. Neither an in-memory
emulator response nor the synthetic evidence hash authorizes live orders.

The actual networked sandbox, independent review and live-capital gates
remain unchecked in [production checklist](../PRODUCTION_CHECKLIST.md).
