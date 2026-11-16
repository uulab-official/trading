"""Local PaperExchange restart demonstration; no credentials or network."""
import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packages/core'))
from trading_core.order_manager import OrderManager, OrderSide, OrderType
from trading_core.order_state_store import SQLiteOrderStateStore
from trading_core.paper_exchange import PaperExchange


async def demonstrate(database):
    exchange = PaperExchange(initial_balance=Decimal('1000'), fee_pct=Decimal('0.1'))
    await exchange.connect()
    try:
        exchange.set_price('BTC/USDT', Decimal('100'), timestamp=1)
        with SQLiteOrderStateStore(database, scope='paper-demo', create=True) as store:
            manager = OrderManager(exchange, state_store=store)
            first = await manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET,
                                              Decimal('2'), client_order_id='demo-one')
            broker_state = exchange.snapshot()
    finally:
        await exchange.disconnect()
    # Broker cash/holdings require their own consistent restoration.
    restored = PaperExchange.from_snapshot(broker_state)
    await restored.connect()
    try:
        with SQLiteOrderStateStore(database, scope='paper-demo') as store:
            manager = OrderManager(restored, state_store=store)
            duplicate = await manager.place_order('BTC/USDT', OrderSide.BUY, OrderType.MARKET,
                                                  Decimal('2'), client_order_id='demo-one')
            if duplicate.to_dict() != first.to_dict() or restored.snapshot() != broker_state:
                raise ValueError('restart_mismatch')
            fills = await restored.fetch_my_trades()
            if len(fills) != 1:
                raise ValueError('duplicate_fill')
            return dict(mode='paper', success=True, fills=len(fills),
                        duplicate_resubmitted=False, order_status=duplicate.status.value,
                        live_enabled=False, account_snapshot_restored_separately=True)
    finally:
        await restored.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(demonstrate(args.database)), allow_nan=False))
        return 0
    except Exception:
        print('Local demo failed; preserve files and use a new private path.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
