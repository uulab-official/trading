"""Synthetic, keyless operating-control demonstration; requires a NEW DB path."""
import argparse
import asyncio
from decimal import Decimal as D
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.paper_admission import PaperOrderRejected
from trading_core.order_manager import OrderSide, OrderType
from trading_core.paper_session import PaperTradingSession


async def demonstrate(path):
    blocked = []

    async def order(session, key, side, amount='1'):
        return await session.manager.place_order('BTC/USDT', side, OrderType.MARKET,
                                                 D(amount), client_order_id=key)

    async def rejected(label, request):
        try:
            await request
        except PaperOrderRejected:
            blocked.append(label)
        else:
            raise AssertionError('expected_local_rejection')

    async def change(session, mode):
        return await session.set_operating_mode(mode,
            expected_generation=session.control_status['generation'], reason='operator')

    async with PaperTradingSession(path, create=True, initial_balance='1000',
            fee_pct='.1', max_order_notional='200', enable_control=True) as session:
        identity = session.account_id
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=1)
        await rejected('initial_halt', order(session, 'initial', OrderSide.BUY))
        await change(session, 'active')
        await order(session, 'buy', OrderSide.BUY, '2')
        await change(session, 'reduce_only')
        await rejected('reduce_only_buy', order(session, 'blocked-buy', OrderSide.BUY))
        await order(session, 'reduce', OrderSide.SELL)
        await change(session, 'halted')
        await rejected('halted_sell', order(session, 'blocked-sell', OrderSide.SELL))
        before = session.exchange.snapshot()

    async with PaperTradingSession(path) as restored:
        assert restored.account_id == identity and restored.exchange.snapshot() == before
        restored_mode = restored.control_status['mode']
        await change(restored, 'active')
        restored.exchange.set_price('BTC/USDT', D('100'), timestamp=2)
        await order(restored, 'exit', OrderSide.SELL)
        await change(restored, 'halted')
        book = restored.exchange.snapshot()
        return dict(synthetic=True, blocked=blocked, restored_mode=restored_mode,
                    final_control=restored.control_status, fills=len(book['trades']),
                    cash=book['balances']['USDT'], fees=book['total_fees'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(demonstrate(args.database))
    except Exception:
        print(json.dumps(dict(error='paper_control_demo_failed')), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
