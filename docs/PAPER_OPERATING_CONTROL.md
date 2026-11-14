# Durable local paper operating control (v24)

## Scope

New `PaperTradingSession(..., create=True, enable_control=True)` accounts start
**halted**. A version-3 row stores the current mode, generation and reason code
alongside the existing account, order intents and optional admission policy.
This is opt-in local paper simulation, not real-broker authorization, a running
process monitor, an external emergency-kill service or a profitability claim.
No new credentials, provider access, HTTP writes or default bot activation.

Existing v1/v2 files keep their original behavior; there is no silent migration.
They report `control_status.enabled=False` and reject control-change requests.
Reopen a v3 account with no configuration overrides. Its stored mode is retained.
The original PaperExchange calculations and OrderManager outcome rules remain.

## Modes

| Mode | New orders |
| --- | --- |
| `active` | Existing quantity, funds, quote-age, notional and uncertainty checks still apply. |
| `reduce_only` | Spot SELL only, bounded by current inventory and all existing checks. No short positions. |
| `halted` | Reject every new buy and sell before intent storage. |

Halt does not liquidate positions or cancel existing orders. Quotes, balance reads,
exact duplicate-ID lookups and explicit reconciliation remain available. A
completed order replay is a read, not a new order. An UNKNOWN order remains
UNKNOWN until supported evidence resolves it; switching modes cannot clear it.

The adapter checks the current mode before proposed execution and again directly
before committing the simulated account effect, with no intervening await. If a
restrictive transition commits while a simulated calculation is suspended, a
blocked effect is not saved. Its already-durable intent remains UNKNOWN. Earlier
committed fills cannot be undone by a later stop. A stop is effective only once
its own transaction commits, not when the caller merely schedules a coroutine.

## Explicit transitions

```python
async with PaperTradingSession('.private/control.sqlite', create=True,
        initial_balance='1000', fee_pct='.1', enable_control=True,
        max_quote_age_ms=30000, max_order_notional='200') as session:
    status = session.control_status  # enabled, mode, generation, reason
    await session.set_operating_mode('active',
        expected_generation=status['generation'], reason='operator')
    # Supply a genuinely newer synthetic observation before any guarded order.
    # Later, on the same owner:
    await session.set_operating_mode('halted',
        expected_generation=session.control_status['generation'], reason='maintenance')
```

Values above are simulation examples, not recommended risk settings. The
expected generation must exactly match; a stale request fails even when asking
for the current mode. Repeating the current mode with the current generation is
an unchanged no-op. A genuine transition increments generation once. Allowed
reason codes are `operator`, `risk_review`, `maintenance`; no free-text secrets.
Only the latest decision is retained, not a historical or signed audit log.
Generation is bounded to 2**31-1, with the final value reserved for `halted`.

Relaxing restrictions requires no active I/O and no nonterminal orders. More
restrictive transitions remain available when an outcome is unresolved. Genuine
transitions discard handle-local quote receipts: with max_quote_age_ms enabled,
old or repeated observations cannot authorize a resumed order. This does not
attest provider data. If no age policy was enabled, control adds none implicitly.

## Failure and ownership

Control changes use the existing account transaction and revision/file checks.
They cannot change balances, positions, costs, account ID or tracked intents.
Failure before commit leaves the old state; a failure after commit may leave the
new state. Neither reports success. Storage errors latch manager failure; close
and reopen the same file to inspect, never reset the account or blindly retry.

Only the existing lifetime file owner may change modes. A second process is
rejected, including one trying to stop a running owner's account. Integrate the
async method inside the owning application. This release does not add IPC or a
remote stop button. Stop ongoing I/O before closing the owner. Local POSIX file
requirements, synchronous snapshot cost and 10,000-order/16-MiB limits remain.
No validation is claimed for Windows, remote filesystems, physical power loss,
malicious local writers, prolonged operation or real accounts.

## Keyless check

With existing project packages installed and a private parent directory:

```bash
python tools/paper_control_demo.py --database .private/control-demo-001.sqlite
python -m unittest discover -s tests -p 'test_order_paper_control.py' -v
```

Use a NEW database path. The demo shows initial halt, normal buying, reduce-only
selling, persistent halt across reopening and explicit resume. With synthetic
1000 USDT cash, price 100 and assumed 0.1% fees, it ends halted after three fills
with 999.6 cash and 0.4 total fees. These are test values, not investment returns.
