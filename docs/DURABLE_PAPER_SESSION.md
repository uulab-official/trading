# Durable local paper session (v22, opt-in)

`PaperTradingSession` owns the original OrderManager and an adapter that executes
only the original PaperExchange. A single private SQLite row contains initial
cash/cost settings, an account ID, the complete paper-account snapshot and all
order intents. No external exchange object, credentials or network input is
accepted. Existing bot/replay defaults are unchanged.

## Commit and recovery boundaries

1. Manager saves SUBMITTING together with the current paper account.
2. Adapter calculates a fill in an isolated PaperExchange copy and commits its
   cash, holdings, fees, trades and deduplication records together with that intent.
3. Manager validates the acknowledgement and commits the confirmed order status.

These are distinct transactions, NOT one all-or-nothing API operation. If step 2
commits but step 3 does not, reopening restores UNKNOWN and the SAME account from
the same row. Explicit `reconcile_order(..., allow_client_lookup=True)` finds the
already-committed local fill without executing it again. A crash before step 2
can leave UNKNOWN with no fill. Missing evidence never authorizes resubmission;
that intent remains blocked. Do not delete records or recreate a funded account
to bypass uncertainty. The behavior is a paper simulation, not an exactly-once
broker execution guarantee.

## Consistency and ownership

The implementation reuses the existing SQLiteOrderStateStore private-file,
lifetime advisory-lock, revision and failure-latch infrastructure. It has a
separate schema and will not open/migrate an order-only database. Reopening
accepts no starting-balance or cost overrides. It checks fills against tracked
intents and reconstructs balances from the original initial cash and chronological
trades using PaperExchange's Decimal context. Unsupported partial-fill states,
extra schema objects, missing fills and inconsistent balances are rejected.
Checksums are unsigned consistency checks, not tamper-proof attestations.

Use a local private POSIX directory. Files must remain owned/private regular
files, not symbolic or hard links. The database and sidecar lock must not be
removed or replaced while open. One process and one asyncio event loop own the
session. Stop and await all I/O before closing. Active read tasks prevent close;
a completed polling error is propagated after releasing the store. Forked
handles, malicious local writers, network filesystems, Windows and real power
loss have not been validated for this new session.

Limits: 10,000 orders and 16 MiB canonical payload; bounded initial monetary
inputs use the existing order-number validation. Full snapshots are rewritten
synchronously and complete history is revalidated: this can block the event loop
and is NOT a high-throughput engine. No pruning, deposit/withdrawal, account import,
shorting, live orders, automatic unblocking or new broker API is implemented.
Only market orders are supported. Use the manager, not the adapter, to submit.

Persisted marks are historical observations, NOT fresh market data. Only marks,
not an OHLCV candle cache, are persisted. This component does not replace the
replay pipeline or its checkpoint/strategy state. The account ID names a local
simulation, not a verified securities account. Never publish the DB/lock files.

## Run (existing project packages installed)

```bash
mkdir -p .private
chmod 700 .private
python tools/paper_session_demo.py --database .private/paper-session-001.sqlite
python -m unittest discover -s tests -p 'test_order_paper_session.py' -v
```

The demo requires a NEW path. It simulates response loss after one local fill,
closes the session and reopens from the same DB without importing a snapshot.
After explicit reconciliation, repeating the client ID leaves one fill and the
same cash/fees. Synthetic inputs: 1,000 USDT, buy 2 units at 100, 0.1% assumed fee.
The fee is a modeling input, not a quote from a real venue.

```python
from decimal import Decimal
from trading_core.paper_session import PaperTradingSession
from trading_core.order_manager import OrderSide, OrderType

async def example():
    async with PaperTradingSession('.private/example.sqlite', create=True,
                                   initial_balance=Decimal('1000')) as session:
        session.exchange.set_price('BTC/USDT', Decimal('100'), timestamp=1)
        await session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, Decimal('1'), client_order_id='example-one')
    async with PaperTradingSession('.private/example.sqlite') as restored:
        print(restored.exchange.snapshot())
```

No GUI or HTTP write API is added. The tests include four actual child-process
terminations around account and acknowledgement commits, two cleanup regressions,
checksum-consistent balance corruption and impossible partial-state rejection.
Local reconstructed-source checks and remote CI evidence are reported separately.
