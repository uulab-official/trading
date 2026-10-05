"""Deterministic, offline-only candle replay with an authoritative paper ledger.

Signals from completed bars execute no earlier than the following bar's open.
No live mode, credentials, remote data, Telegram, or WebSocket is accepted here.
"""

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_DOWN, localcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable, Optional

from trading_core.paper_exchange import PaperExchange, PAPER_CALCULATION_CONTEXT, _decimal, _assets
from trading_core.order_manager import OrderManager, OrderSide, OrderType
from trading_data import Candle, CandleBuffer
from trading_strategy import MACrossStrategy, RSIStrategy, StrategyConfig
from trading_bot.paper_checkpoint import CheckpointInUseError, locked_checkpoint


@dataclass(frozen=True)
class PaperRunConfig:
    mode: str = "paper"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    strategy: str = "ma_cross"
    initial_balance: Decimal = Decimal("10000")
    position_size_pct: Decimal = Decimal("20")
    fee_pct: Decimal = Decimal("0.1")
    slippage_pct: Decimal = Decimal("0.05")
    max_daily_loss_pct: Decimal = Decimal("3")
    max_drawdown_pct: Decimal = Decimal("10")
    fast_period: int = 3
    slow_period: int = 5
    rsi_period: int = 14

    def __post_init__(self):
        if self.mode != "paper":
            raise ValueError("This fixture runner accepts paper mode only")
        if _assets(self.symbol)[1] not in ("USDT", "KRW"):
            raise ValueError("The offline runner supports one USDT- or KRW-quoted spot symbol")
        if self.strategy not in ("ma_cross", "rsi"):
            raise ValueError("Offline strategy must be ma_cross or rsi")
        for name in ("initial_balance", "position_size_pct", "max_daily_loss_pct", "max_drawdown_pct"):
            value = _decimal(getattr(self, name))
            if name != "initial_balance" and value > 100:
                raise ValueError(f"{name} cannot exceed 100")
            object.__setattr__(self, name, value)
        for name in ("fee_pct", "slippage_pct"):
            value = _decimal(getattr(self, name), positive=False)
            if value >= 100:
                raise ValueError(f"{name} must be below 100")
            object.__setattr__(self, name, value)
        CandleBuffer(timeframe=self.timeframe)
        if any(type(value) is not int or value < 1 for value in (self.fast_period, self.slow_period, self.rsi_period)) or self.fast_period >= self.slow_period or self.slow_period > 500 or self.rsi_period >= 500:
            raise ValueError("Positive indicator periods and fast_period < slow_period are required")

    def identity(self):
        return {name: str(value) if isinstance(value, Decimal) else value for name, value in asdict(self).items()}


def _canonical_bar(candle):
    return [candle.timestamp, *[str(getattr(candle, field)) for field in ("open", "high", "low", "close", "volume")]]


def _prefix_hash(candles, count):
    content = json.dumps([_canonical_bar(candle) for candle in candles[:count]], separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def _atomic_checkpoint(path: Path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".paper-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class PaperRunner:
    def __init__(self, config: Optional[PaperRunConfig] = None, *, strategy_factory: Optional[Callable] = None):
        self.config = config or PaperRunConfig()
        self.strategy_factory = strategy_factory

    def _strategy(self):
        if self.strategy_factory is not None:
            return self.strategy_factory()
        settings = StrategyConfig(name="offline-paper", symbol=self.config.symbol, timeframe=self.config.timeframe)
        if self.config.strategy == "rsi":
            return RSIStrategy(settings, rsi_period=self.config.rsi_period)
        return MACrossStrategy(settings, fast_period=self.config.fast_period, slow_period=self.config.slow_period)

    async def run(self, fixture, *, checkpoint_path: Optional[Path] = None, stop_after: Optional[int] = None):
        with localcontext(PAPER_CALCULATION_CONTEXT):
            with locked_checkpoint(checkpoint_path) as canonical_path:
                return await self._run(fixture, checkpoint_path=canonical_path, stop_after=stop_after)

    async def _run(self, fixture, *, checkpoint_path=None, stop_after=None, return_state=False):
        candles = []
        duration = CandleBuffer(timeframe=self.config.timeframe).duration_ms
        for row in fixture:
            candle = row if isinstance(row, Candle) else Candle(*row)
            if candles and candle.timestamp < candles[-1].timestamp + duration:
                raise ValueError("Fixture bars must be chronological and non-overlapping for the configured timeframe")
            candles.append(candle)
        if not candles:
            raise ValueError("At least one local fixture bar is required")
        if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
            raise ValueError("stop_after must be a positive bar count")
        config = self.config
        quote_currency = _assets(config.symbol)[1]
        strategy = self._strategy()
        path = Path(checkpoint_path) if checkpoint_path is not None else None
        state = {
            "version": 1, "mode": "paper", "config": config.identity(), "next_index": 0,
            "pending_signal": None, "prefix_hash": _prefix_hash(candles, 0),
            "risk_day": None, "daily_start_equity": str(config.initial_balance),
            "peak_equity": str(config.initial_balance), "daily_loss_halted": False,
            "drawdown_halted": False,
        }
        if path is not None and path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("Checkpoint must be a JSON object")
            if state.get("version") != 1 or state.get("mode") != "paper" or state.get("config") != config.identity():
                raise ValueError("Checkpoint mode/version/configuration does not match this paper replay")
            cursor = state.get("next_index")
            if type(cursor) is not int or cursor < 1 or cursor > len(candles) or state.get("prefix_hash") != _prefix_hash(candles, cursor):
                raise ValueError("Previously processed fixture history changed")
            # Recompute authoritative economic/risk state from verified fixtures.
            # A JSON checkpoint cannot invent cash, fills, or pending signals.
            verifier = PaperRunner(config, strategy_factory=self.strategy_factory)
            expected = await verifier._run(candles[:cursor], return_state=True)
            if state != expected:
                raise ValueError("Checkpoint economic, risk, or pending-signal state differs from authoritative replay")
            state = expected
            exchange = PaperExchange.from_snapshot(expected["exchange"])
        else:
            exchange = PaperExchange(initial_balance=config.initial_balance, fee_pct=config.fee_pct, slippage_pct=config.slippage_pct, quote_currency=quote_currency)
        cursor = state["next_index"]
        # Rebuild indicator/cross state from the verified historical prefix.
        # Replayed historical signals are not submitted again.
        for candle in candles[:cursor]:
            strategy.on_candle(candle)
        await exchange.connect()
        manager = OrderManager(exchange)
        consumed = 0
        try:
            for index in range(cursor, len(candles)):
                if stop_after is not None and consumed >= stop_after:
                    break
                candle = candles[index]
                exchange.set_price(config.symbol, candle.open, timestamp=candle.timestamp)
                try:
                    day = datetime.fromtimestamp(candle.timestamp / 1000, timezone.utc).date().isoformat()
                except (ValueError, OSError, OverflowError) as error:
                    raise ValueError("Fixture timestamp must be a representable UTC date") from error
                if day != state["risk_day"]:
                    state["risk_day"] = day
                    state["daily_start_equity"] = str(exchange.get_total_value())
                    state["daily_loss_halted"] = False
                pending = state["pending_signal"]
                if pending is not None:
                    await self._execute_pending(exchange, manager, pending, state)
                exchange.set_price(config.symbol, candle.close, timestamp=candle.timestamp)
                equity = exchange.get_total_value()
                daily_start = _decimal(state["daily_start_equity"], positive=False)
                peak = max(_decimal(state["peak_equity"], positive=False), equity)
                state["peak_equity"] = str(peak)
                if daily_start > 0 and (daily_start - equity) / daily_start * 100 >= config.max_daily_loss_pct:
                    state["daily_loss_halted"] = True
                if peak > 0 and (peak - equity) / peak * 100 >= config.max_drawdown_pct:
                    state["drawdown_halted"] = True
                signal = strategy.on_candle(candle)
                action = signal.type.value if signal is not None else None
                if action not in (None, "hold", "buy", "sell", "close"):
                    raise ValueError("Unknown offline strategy signal")
                state["pending_signal"] = {"action": "sell" if action == "close" else action, "source_index": index} if action in ("buy", "sell", "close") else None
                state["next_index"] = index + 1
                state["prefix_hash"] = _prefix_hash(candles, index + 1)
                state["exchange"] = exchange.snapshot()
                if path is not None:
                    _atomic_checkpoint(path, state)
                consumed += 1
                # Paper exchange awaits complete synchronously. Deliver pending
                # cancellation only after this bar and its atomic save finish,
                # before another bar can mutate the ledger.
                await asyncio.sleep(0)
            balances = await exchange.fetch_balance()
            equity = exchange.get_total_value()
            if return_state:
                return state
            result = {
                "mode": "paper", "status": "fixture_complete" if state["next_index"] == len(candles) else "paused",
                "assumptions": config.identity(),
                "bars_processed": state["next_index"], "cash": str(balances[quote_currency].free),
                "holdings": {asset: str(balance.free) for asset, balance in balances.items() if asset != quote_currency and balance.free},
                "equity": str(equity), "total_fees": str(exchange.total_fees),
                "total_return_pct": str((equity - config.initial_balance) / config.initial_balance * 100),
                "pending_signal": state["pending_signal"], "trades": exchange.snapshot()["trades"],
                "daily_loss_halted": state["daily_loss_halted"], "drawdown_halted": state["drawdown_halted"],
            }
            if quote_currency != "USDT":
                result["quote_currency"] = quote_currency
            return result
        finally:
            await exchange.disconnect()

    async def _execute_pending(self, exchange, manager, pending, state):
        if type(pending.get("source_index")) is not int or pending["source_index"] != state["next_index"] - 1:
            raise ValueError("Checkpoint pending signal is not from the preceding completed bar")
        action = pending.get("action")
        if action not in ("buy", "sell"):
            raise ValueError("Invalid pending paper action")
        config = self.config
        base, quote = _assets(config.symbol)
        balances = await exchange.fetch_balance()
        held = balances[base].free if base in balances else Decimal("0")
        if action == "buy":
            if held or state["daily_loss_halted"] or state["drawdown_halted"]:
                return
            cash = balances[quote].free
            reference = (await exchange.fetch_ticker(config.symbol)).last
            cost_per_unit = reference * (1 + config.slippage_pct / 100) * (1 + config.fee_pct / 100)
            amount = (cash * config.position_size_pct / 100 / cost_per_unit).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        else:
            amount = held
        if amount <= 0:
            return
        await manager.place_order(config.symbol, OrderSide.BUY if action == "buy" else OrderSide.SELL,
                                  OrderType.MARKET, amount, client_order_id=f"fixture-{pending['source_index']}-{action}")


def _cli_decimal(value):
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal number") from error


def main(argv=None):
    """Run the local CLI inputs and return the result dict to Python callers."""
    parser = argparse.ArgumentParser(description="Offline paper replay; no live mode or network supported")
    parser.add_argument("fixture", type=Path, help="Local JSON object with symbol, timeframe, and six-column candles")
    parser.add_argument("--checkpoint", type=Path, required=True, help="Local paper-only checkpoint; existing history is validated")
    parser.add_argument("--strategy", choices=("ma_cross", "rsi"), default="ma_cross")
    parser.add_argument("--initial-balance", type=_cli_decimal, default=Decimal("10000"))
    parser.add_argument("--position-size-pct", type=_cli_decimal, default=Decimal("20"))
    parser.add_argument("--fee-pct", type=_cli_decimal, default=Decimal("0.1"))
    parser.add_argument("--slippage-pct", type=_cli_decimal, default=Decimal("0.05"))
    parser.add_argument("--max-daily-loss-pct", type=_cli_decimal, default=Decimal("3"))
    parser.add_argument("--max-drawdown-pct", type=_cli_decimal, default=Decimal("10"))
    parser.add_argument("--fast-period", type=int, default=3)
    parser.add_argument("--slow-period", type=int, default=5)
    parser.add_argument("--rsi-period", type=int, default=14)
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("mode", "paper") != "paper":
        raise ValueError("Fixture must be an offline paper JSON object")
    if not isinstance(data.get("symbol", "BTC/USDT"), str):
        raise ValueError("Fixture symbol must be a string")
    candles = data.get("candles")
    if not isinstance(candles, list) or any(not isinstance(row, list) or len(row) != 6 for row in candles):
        raise ValueError("Fixture candles must be an array of six-column OHLCV rows")
    config = PaperRunConfig(symbol=data.get("symbol", "BTC/USDT"), timeframe=data.get("timeframe", "1h"),
                            strategy=args.strategy, initial_balance=args.initial_balance,
                            position_size_pct=args.position_size_pct, fee_pct=args.fee_pct, slippage_pct=args.slippage_pct,
                            max_daily_loss_pct=args.max_daily_loss_pct, max_drawdown_pct=args.max_drawdown_pct,
                            fast_period=args.fast_period, slow_period=args.slow_period, rsi_period=args.rsi_period)
    result = asyncio.run(PaperRunner(config).run(candles, checkpoint_path=args.checkpoint))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return result


def cli(argv=None):
    """Process entry point: emit one result on success and return an exit code.

    Keep exception details/configuration out of routine recovery messages.
    Unexpected programming errors remain visible rather than looking like bad
    user input or an automatically recoverable checkpoint failure.
    """
    try:
        main(argv)
    except CheckpointInUseError:
        print("Checkpoint is in use. Wait for the active run to finish, then retry the same command. "
              "Do not delete or replace the lock file.", file=sys.stderr)
        return 3
    except (ValueError, DecimalException):
        print("Invalid paper input or checkpoint. Check the fixture format and original run options. "
              "Preserve the saved checkpoint; use a new checkpoint path for a different experiment.", file=sys.stderr)
        return 2
    except OSError:
        print("Local file access failed. Check the fixture/checkpoint paths and permissions, "
              "then retry the same command; preserve the checkpoint and lock file.", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("Paper replay interrupted. To validate and resume the last saved checkpoint, "
              "rerun the same command; if none was saved, it starts from the beginning.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
