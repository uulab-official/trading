"""Synthetic lost-ack recovery with cash and orders in ONE local SQLite file."""
import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/core'))
from trading_core.order_manager import OrderOutcomeUnknown, OrderSide, OrderStatus, OrderType
from trading_core.order_reconciliation import reconcile_order
from trading_core.paper_session import PaperTradingSession


async def demo(path):
    intent = dict(symbol='BTC/USDT', side=OrderSide.BUY, order_type=OrderType.MARKET,
                  amount=Decimal('2'), client_order_id='synthetic-lost-ack')
    async with PaperTradingSession(path, create=True, initial_balance=Decimal('1000'),
                                   fee_pct=Decimal('0.1')) as first:
        first.exchange.set_price('BTC/USDT', Decimal('100'), timestamp=1)
        create = first.exchange.create_order
        async def lost_ack(**kwargs):
            await create(**kwargs)
            raise TimeoutError('synthetic lost acknowledgement')
        first.exchange.create_order = lost_ack
        try:
            await first.manager.place_order(**intent)
        except OrderOutcomeUnknown:
            pass
        else:
            raise RuntimeError('demo_expected_unknown')
        before = first.exchange.snapshot()
        identity = first.account_id
    # No external snapshot import or initial-balance setting on reopen.
    async with PaperTradingSession(path) as restored:
        result = await reconcile_order(restored.manager, intent['client_order_id'], allow_client_lookup=True)
        await restored.manager.place_order(**intent)  # Inspection only, never resubmission.
        after = restored.exchange.snapshot()
        if result.status != OrderStatus.FILLED or before != after or restored.account_id != identity:
            raise RuntimeError('demo_recovery_mismatch')
        return dict(success=True, mode='paper', live_enabled=False, account_id=identity,
                    cash=after['balances']['USDT'], fees=after['total_fees'],
                    trade_count=len(after['trades']), order_status=result.status.value,
                    account_recovery_exact=True, synthetic=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='New path in an existing private directory')
    args = parser.parse_args()
    try:
        result = asyncio.run(demo(args.database))
    except Exception:
        print(json.dumps({'success': False, 'error': 'paper_session_demo_failed'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
