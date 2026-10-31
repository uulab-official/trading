# Order lifecycle primitive

Inspired by event-driven execution architecture in LEAN and NautilusTrader, without copying source code.

States: created, submitting, unknown, accepted, partially_filled, filled, cancel_pending, cancelled, rejected. Validates monotonic cumulative fill and disallows transitions out of terminal states. A network timeout is **unknown**, never automatically a rejected order.

This module is pure and not wired to a broker. Required follow-up: persist transitions and raw venue events atomically, reconcile unknown orders by venue order ID/client ID, handle out-of-order exchange events, partial fills, trade IDs, commissions, settlement and cash ledger, plus restart/retry tests. Do not use for live trading before these gates.
