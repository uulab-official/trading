# Paper order admission

`packages/core/paper_execution.py` adds a minimal **single-process, in-memory** paper admission layer on top of the pure risk gate and paper order stub. Accepted buy intents reserve notional to avoid repeated spending of the same buying power. Repeated client IDs return the same accepted order, while conflicting payloads are rejected. Confirmed cancellation releases the reservation.

No fills, fees, persisted journal, live orders, sells, multi-process concurrency, or broker reconciliation are implemented. This layer must not be used as a production risk gate. Next implementation: durable transactional reservations, order lifecycle, accounting, restart reconciliation, and integration tests.
