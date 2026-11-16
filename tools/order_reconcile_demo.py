"""Offline lost-acknowledgement demonstration; no broker credentials or network.

Uses a new order-state database and an independently restored in-memory paper
venue snapshot. This is not atomic recovery of a live broker or cash ledger.
"""
import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import OrderManager, OrderSide, OrderType, OrderOutcomeUnknown
from trading_core.order_reconciliation import reconcile_order
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange


class LostAcknowledgementPaper(PaperExchange):
    async def create_order(self, *args, **kwargs):
        await super().create_order(*args, **kwargs)
        raise TimeoutError('synthetic lost acknowledgement after local paper fill')


async def demonstrate(database):
    venue = LostAcknowledgementPaper(initial_balance=Decimal('1000'), fee_pct=Decimal('.1'))
    await venue.connect()
    venue.set_price('BTC/USDT', Decimal('100'), timestamp=1)
    with SQLiteOrderStateStore(database, scope='paper-reconcile-demo', create=True) as store:
        manager = OrderManager(venue, state_store=store)
        try:
            await manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET,
                                      Decimal('1'), client_order_id='demo-one')
        except OrderOutcomeUnknown:
            pass
        else:
            raise AssertionError('the synthetic acknowledgement should be lost')
        unresolved = await manager.get_order('demo-one')
        if unresolved.status.value != 'unknown' or unresolved.exchange_order_id is not None:
            raise AssertionError('missing-ack state not preserved')
    # Preserve the independently authoritative paper venue state, not a new book.
    venue_snapshot = venue.snapshot()
    await venue.disconnect()
    restored = PaperExchange.from_snapshot(venue_snapshot)
    await restored.connect()
    with SQLiteOrderStateStore(database, scope='paper-reconcile-demo') as store:
        manager = OrderManager(restored, state_store=store)
        result = await reconcile_order(manager, 'demo-one', allow_client_lookup=True)
        if result.status.value != 'filled' or restored.snapshot() != venue_snapshot:
            raise AssertionError('reconciliation must not mutate cash or execute a fill')
    with SQLiteOrderStateStore(database, scope='paper-reconcile-demo') as store:
        manager = OrderManager(restored, state_store=store)
        if (await manager.get_order('demo-one')).status.value != 'filled':
            raise AssertionError('resolved status did not survive restart')
    await restored.disconnect()
    return dict(mode='paper', synthetic=True, live_enabled=False, recovered_status='filled',
                fill_count=len(venue_snapshot['trades']),
                cash=venue_snapshot['balances']['USDT'],
                fees=venue_snapshot['total_fees'],
                venue_state_unchanged=True, durable_restart_verified=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(asyncio.run(demonstrate(args.database)), allow_nan=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('Offline reconciliation demo failed; preserve files and use a new database path.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
