"""Synthetic local-only admission demo. Requires a NEW private SQLite path."""
import argparse
import asyncio
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/core'))
from trading_core.order_manager import OrderSide, OrderType
from trading_core.paper_admission import PaperOrderRejected
from trading_core.paper_session import PaperTradingSession


async def demonstrate(path):
    async with PaperTradingSession(path, create=True, initial_balance=D('1000'),
            fee_pct=D('.1'), max_quote_age_ms=30000, max_order_notional=D('200')) as session:
        stamp = time.time_ns() // 1000000
        session.exchange.set_price('BTC/USDT', D('100'), timestamp=stamp)
        try:
            await session.manager.place_order('BTC/USDT', OrderSide.BUY,
                OrderType.MARKET, D('3'), client_order_id='example-buy')
        except PaperOrderRejected as exc:
            cap_reason = str(exc)
        else:
            raise AssertionError('notional limit did not reject')
        assert session.manager.get_all_orders() == {}
        await session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D('1'), client_order_id='example-buy')
        before = session.exchange.snapshot()
    async with PaperTradingSession(path) as restored:
        # A retry of an already-recorded order is a read, not a new fill.
        await restored.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D('1'), client_order_id='example-buy')
        assert restored.exchange.snapshot() == before
        try:
            await restored.manager.place_order('BTC/USDT', OrderSide.SELL,
                OrderType.MARKET, D('1'), client_order_id='example-exit')
        except PaperOrderRejected as exc:
            refresh_reason = str(exc)
        else:
            raise AssertionError('restored price unexpectedly authorized a new fill')
        now = time.time_ns() // 1000000
        for _ in range(100):
            if now > stamp:
                break
            await asyncio.sleep(.002)
            now = time.time_ns() // 1000000
        if now <= stamp:
            raise RuntimeError('wall clock did not advance')
        restored.exchange.set_price('BTC/USDT', D('100'), timestamp=now)
        await restored.manager.place_order('BTC/USDT', OrderSide.SELL,
            OrderType.MARKET, D('1'), client_order_id='example-exit')
        final = restored.exchange.snapshot()
        assert len(final['trades']) == 2 and D(final['balances']['USDT']) == D('999.8')
        return dict(success=True, mode='paper', live_enabled=False, data='synthetic',
            blocked_notional=cap_reason, blocked_restored_quote=refresh_reason,
            fill_count=len(final['trades']), final_cash=final['balances']['USDT'],
            total_fees=final['total_fees'], policy=restored.admission_policy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(demonstrate(args.database))
    except (ValueError, RuntimeError, OSError):
        print(json.dumps(dict(success=False, error='paper_admission_demo_failed')), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
