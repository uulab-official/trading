# Durable order intent journal — SANDBOX DESIGN FOUNDATION ONLY

`packages/core/trading_core/order_intent_journal.py` is a SQLite state
machine for recording *intention to submit* before any future broker network
request. It does **not** contain a broker API adapter or send orders. Merely
adding it to the repository does not activate live trading.

## Safety contract

- Caller assigns a unique, deterministic `client_id` for a proposed spot
  order; `reserve(id, symbol, side, quantity)` is a durable SQLite
  `BEGIN IMMEDIATE` transaction before any future submission.
- WAL mode and `synchronous=FULL` provide local SQLite durability
  assumptions, but not guarantees on unsupported remote filesystems.
- A repeated **identical** client ID returns the existing record,
  regardless of whether its state is `reserved`, `uncertain` or
  `reconciled`. It never creates a second order.
- Reusing an ID with different trade terms fails immediately.
- While **any** intent is `reserved` or `uncertain`, creation of a
  *different* intent is rejected. This limits the journal to one
  unresolved broker action at a time.
- Any ambiguous submit/timeout/acknowledgment must be marked
  `uncertain`; the system must **not** retry that request automatically.
- The only exit from uncertain state is explicit
  `reconcile(client_id, outcome, evidence_ref, broker_order_id)`
  using externally obtained, verified broker evidence. Outcome is
  `filled`, `canceled` or `rejected`. A filled order requires a
  broker order ID. Final evidence is immutable.
- The journal stores no credentials, passwords, API secrets, or raw
  broker response bodies. Evidence references are bounded identifiers,
  not proof that evidence has been independently authenticated.

## Lifecycle

```text
proposed
   |
   v
reserved -- (future broker adapter sends ONCE)
   |
   +---- unambiguous broker evidence ------> reconciled
   |
   +---- network timeout / unknown ACK ----> uncertain
                                             |
                                             +-- independently query broker --
                                                           |
                                                           v
                                                       reconciled

Any reserved / uncertain record blocks a different new intent.
```

The code currently **only** implements the persistence state machine.
A future sandbox adapter must separately prove broker client-order-ID
semantics, identify unknown submission outcomes, handle partial fills
and cancellations, reconcile balances/trades/fees, and constrain all
order sizes against authenticated exchange market filters. It must
also enforce portfolio risk and a separate operator-approved
activation gate. None of these prerequisites is satisfied simply
because this journal exists.

## Offline regression verification

```sh
python tools/offline_checks.py
# Or with the core package installed:
python -m unittest packages/core/tests/test_order_intent_journal.py
```

The tests cover durable reopen, exact duplicate replay, conflicting
ID reuse, ambiguous outcome persistence, final evidence immutability,
numeric validation, and competing writers. They require no account,
broker, or network connection.

**Do not connect this journal to real-money order APIs until the full
sandbox and independent security/test gates in
[PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md) are passed.**
