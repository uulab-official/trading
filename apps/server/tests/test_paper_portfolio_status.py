"""Multi-portfolio offline monitoring with independent economic replay."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
             ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(path))
from tools.paper_portfolio_status import inspect_portfolios
from tools.stock_paper_forward import replay_forward
from trading_bot.paper_runner import PaperRunConfig, PaperRunner

START = 1774417800000 - (1774417800000 % 3600000)


class PaperPortfolioStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.crypto = self.root / "btc.json"
        self.checkpoint = self.root / "btc-paper.json"
        crypto_rows = [
            [START + i * 3600000, "100", "100", "100", "100", "100"]
            for i in range(6)
        ]
        self.crypto.write_text(json.dumps({
            "mode": "paper", "symbol": "BTC/USDT",
            "timeframe": "1h", "candles": crypto_rows,
        }))
        asyncio.run(PaperRunner(PaperRunConfig()).run(
            crypto_rows, checkpoint_path=self.checkpoint
        ))
        self.stocks = self.root / "stock.json"
        self.stock_db = self.root / "stock.sqlite"
        start = START - START % 60000
        prices = [10, 9, 8, 20, 21]
        stock_data = {
            "mode": "paper", "interval": "1m", "no_live_orders": True,
            "timestamp_semantics": "source_close_to_utc_open",
            "symbol": "005930", "quote_currency": "KRW",
            "candles": [
                [start + i * 60000, str(p), str(p), str(p), str(p), "100"]
                for i, p in enumerate(prices)
            ],
        }
        self.stocks.write_text(json.dumps(stock_data))
        replay_forward(stock_data, self.stock_db, initial_cash="1000",
                       fee_pct="0", fast=2, slow=3, allocation_pct="50")

    def test_both_portfolios_are_audited_and_never_summed(self):
        status = inspect_portfolios(
            crypto_fixture=self.crypto, crypto_checkpoint=self.checkpoint,
            stock_fixture=self.stocks, stock_database=self.stock_db,
            stock_cash="1000", stock_fee_pct="0",
            fast=2, slow=3, allocation_pct="50",
        )
        self.assertEqual("paper", status["mode"])
        self.assertFalse(status["live_orders_enabled"])
        self.assertIsNone(status["cross_portfolio_total"])
        self.assertEqual({"crypto", "stock"},
                         {item["type"] for item in status["portfolios"]})
        self.assertEqual("KRW", status["portfolios"][1]["quote_currency"])

    def test_independent_stock_and_crypto_mode(self):
        crypto = inspect_portfolios(
            crypto_fixture=self.crypto, crypto_checkpoint=self.checkpoint,
        )
        stock = inspect_portfolios(
            stock_fixture=self.stocks, stock_database=self.stock_db,
            stock_cash="1000", stock_fee_pct="0",
            fast=2, slow=3, allocation_pct="50"
        )
        self.assertEqual(1, len(crypto["portfolios"]))
        self.assertEqual(1, len(stock["portfolios"]))

    def test_corrupt_crypto_prevents_false_green_portfolio(self):
        raw = json.loads(self.checkpoint.read_text())
        raw["exchange"]["balances"]["USDT"] = "9999999999"
        self.checkpoint.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            inspect_portfolios(
                crypto_fixture=self.crypto, crypto_checkpoint=self.checkpoint,
                stock_fixture=self.stocks, stock_database=self.stock_db,
                stock_cash="1000", stock_fee_pct="0",
                fast=2, slow=3, allocation_pct="50",
            )

    def test_incomplete_or_absent_pair_is_rejected(self):
        with self.assertRaises(ValueError):
            inspect_portfolios(crypto_fixture=self.crypto)
        with self.assertRaises(ValueError):
            inspect_portfolios(stock_fixture=self.stocks)
        with self.assertRaises(ValueError):
            inspect_portfolios()


if __name__ == "__main__":
    unittest.main()
