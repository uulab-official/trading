"""Run the existing backtest engine on a local fixture and print net FIFO accounting."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "config/examples/offline_fixture.json"


def build_fixture_report(path):
    """No broker/data network client is permitted, including during imports."""
    with patch("socket.socket.connect", side_effect=AssertionError("Offline report forbids connections")), \
         patch("socket.create_connection", side_effect=AssertionError("Offline report forbids network clients")), \
         patch("socket.getaddrinfo", side_effect=AssertionError("Offline report forbids DNS")):
        for package in ("core", "data", "strategy", "backtest"):
            source = str(ROOT / "packages" / package)
            if source not in sys.path:
                sys.path.insert(0, source)
        import pandas as pd
        from decimal import Decimal
        from trading_backtest import BacktestConfig, BacktestEngine
        from trading_strategy import MACrossStrategy, StrategyConfig

        raw = Path(path).read_bytes()
        fixture = json.loads(raw)
        if not isinstance(fixture, dict) or fixture.get("mode", "paper") != "paper":
            raise ValueError("The offline report accepts paper-mode local fixtures only")
        config = BacktestConfig(initial_balance=Decimal("10000"), commission_pct=Decimal("0.1"),
                                slippage_pct=Decimal("0.05"), position_size_pct=Decimal("20"))
        strategy = MACrossStrategy(StrategyConfig("offline_report", fixture["symbol"], fixture["timeframe"]),
                                   fast_period=3, slow_period=5)
        data = pd.DataFrame(fixture["candles"], columns=["timestamp", "open", "high", "low", "close", "volume"])
        result = BacktestEngine(config).run(strategy, data)
        return {
            "mode": "paper", "source": "caller-supplied local fixture",
            "fixture_sha256": hashlib.sha256(raw).hexdigest(),
            "symbol": result.symbol, "timeframe": result.timeframe, "bars": len(data),
            "initial_balance": str(result.initial_balance), "cash": str(result.final_balance),
            "marked_equity": str(result.final_equity), "total_return_pct": str(result.total_return_pct),
            "trade_report": result.trade_report.to_dict(),
            "assumptions": {"strategy": "MA cross 3/5", "fee_pct": "0.1", "slippage_pct": "0.05",
                "position_size_pct": "20", "fill_timing": "next available open",
                "liquidity_gate": "previous completed bar volume", "lot_matching": "FIFO"},
            "risk_statistics": "unverified legacy risk statistics are omitted",
            "profitability": "Synthetic examples demonstrate accounting; they do not establish profitability",
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args(argv)
    print(json.dumps(build_fixture_report(args.fixture), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
