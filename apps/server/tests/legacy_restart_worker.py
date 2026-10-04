"""Run real legacy APIs in a separate process against a caller-created temp fixture.

No live exchange, credentials, service, or network is used. Only the clock and
the legacy import-time file logger are patched; orders use actual PaperExchange.
"""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import Decimal
import importlib
import json
import logging
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for package in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
    sys.path.insert(0, str(ROOT / package))


class Clock(datetime):
    current = datetime(2026, 10, 4, 12, 0)

    @classmethod
    def utcnow(cls):
        return cls.current


def capture(bot):
    if bot.portfolio is None:
        return {"portfolio": None, "daily_date": None, "daily_pnl_pct": None,
                "risk": bot.risk_manager.get_risk_report(), "cooldown_until": None,
                "buy_allowed": False, "buy_reason": bot.recovery_reason,
                "order_count": 0, "exchange_orders": {}}
    allowed, reason = bot.risk_manager.check_trade_allowed(
        "ETH/USDT", "buy", Decimal("0"), Decimal("100")
    )
    return {
        "portfolio": bot.portfolio.to_dict(),
        "daily_date": bot.portfolio.daily_date.isoformat(),
        "daily_pnl_pct": str(bot.portfolio.get_daily_pnl_pct()),
        "risk": bot.risk_manager.get_risk_report(),
        "cooldown_until": str(getattr(bot.risk_manager, "_cooldown_until", None)),
        "buy_allowed": allowed,
        "buy_reason": reason,
        "order_count": len(bot.order_manager.get_all_orders()),
        "exchange_orders": bot.exchange.snapshot()["orders"],
    }


async def run(args, Bot, Signal, SignalType):
    if args.phase == "resume" and args.scenario == "rollback":
        Clock.current = datetime(2026, 10, 3, 12, 31)
    elif args.scenario in ("daily_loss", "rollback") and args.phase == "resume":
        Clock.current += timedelta(minutes=31)
    bot = Bot("config")
    await bot.initialize()
    assert bot.exchange is None or type(bot.exchange).__name__ == "PaperExchange"
    assert bot.websocket is None
    assert type(bot.notifier).__name__ == "NullNotifier"
    assert bot.config.mode == "paper"
    if args.phase == "establish":
        for _ in range(10 if args.scenario == "trade_count" else 1):
            bot.exchange.set_price("BTC/USDT", Decimal("100"))
            await bot._process_signal(Signal(SignalType.BUY, "BTC/USDT", Decimal("100"), Clock.utcnow()))
            assert bot.portfolio.has_position("BTC/USDT")
            price = {"circuit": "40", "daily_loss": "65", "cooldown": "99", "trade_count": "100",
                     "rollback": "40", "missing_baseline": "40", "corrupt_state": "40"}[args.scenario]
            bot.exchange.set_price("BTC/USDT", Decimal(price))
            await bot._close_position("BTC/USDT", Decimal(price), "offline restart fixture")
            assert not bot.portfolio.has_position("BTC/USDT")
        if args.scenario in ("daily_loss", "rollback"):
            Clock.current += timedelta(minutes=31)
        result = {"phase": args.phase, "before_stop": capture(bot)}
        await bot.stop()
        # Read the actual SQLite rows after graceful shutdown, before exit.
        with sqlite3.connect("data/paper_bot_state.db") as conn:
            result["persisted_rows"] = [
                {"key": key, "data": json.loads(data), "updated_at": updated}
                for key, data, updated in conn.execute("SELECT key, data, updated_at FROM state ORDER BY key")
            ]
    else:
        result = {"phase": args.phase, "after_initialize": capture(bot)}
        if bot.exchange is not None:
            bot.exchange.set_price("ETH/USDT", Decimal("100"))
        await bot._process_signal(Signal(SignalType.BUY, "ETH/USDT", Decimal("100"), Clock.utcnow()))
        result["new_buy_filled"] = bot.portfolio is not None and bot.portfolio.has_position("ETH/USDT")
        result["after_buy"] = capture(bot)
        await bot.stop()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("establish", "resume"))
    parser.add_argument("scenario")
    args = parser.parse_args()
    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
            stack.enter_context(patch(target, side_effect=AssertionError("Restart audit forbids network/DNS")))
        stack.enter_context(patch("logging.FileHandler", return_value=logging.NullHandler()))
        stack.enter_context(patch("logging.basicConfig"))
        module = importlib.import_module("trading_bot.main")
        from trading_strategy import Signal, SignalType
        for target in ("trading_core.portfolio.datetime", "trading_core.risk_manager.datetime",
                       "trading_core.order_manager.datetime", "trading_core.state_store.datetime",
                       "trading_bot.main.datetime", "trading_core.legacy_checkpoint.datetime"):
            stack.enter_context(patch(target, Clock))
        asyncio.run(run(args, module.TradingBot, Signal, SignalType))
