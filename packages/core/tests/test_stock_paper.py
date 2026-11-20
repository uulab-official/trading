"""No-network stock paper broker regressions for KRW and USD markets."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages/core"))
from trading_core.stock_paper import StockPaperBook


class StockPaperTests(unittest.TestCase):
    def test_krw_buy_sell_fees_and_marked_equity(self):
        book = StockPaperBook("KRW", "100000", fee_pct="0.1")
        buy = book.execute("buy-001", "005930", "buy", 2, price="10000")
        self.assertEqual("simulated_fill", buy["status"])
        self.assertEqual(Decimal("79980"), book.cash)
        self.assertEqual(Decimal("99980"), book.marked_equity({"005930": "10000"}))
        again = book.execute("buy-001", "005930", "buy", 2, price="10000")
        self.assertEqual(buy, again)
        self.assertEqual(1, len(book.fills))
        sell = book.execute("sell-001", "005930", "sell", 2, price="11000")
        self.assertEqual(Decimal("101958"), book.cash)
        self.assertEqual({}, book.holdings)
        self.assertEqual("22", sell["fee"])

    def test_usd_marked_positions_do_not_convert_from_krw(self):
        usd = StockPaperBook("USD", "1000", fee_pct="0")
        usd.execute("apple-001", "AAPL", "buy", 3, price="100")
        self.assertEqual(Decimal("700"), usd.cash)
        self.assertEqual(Decimal("1030"), usd.marked_equity({"AAPL": "110"}))
        self.assertEqual("USD", usd.fills[0]["quote_currency"])

    def test_rejects_oversell_and_insufficient_cash_without_mutation(self):
        book = StockPaperBook("KRW", "10000")
        before = book.cash
        with self.assertRaises(ValueError):
            book.execute("x", "005930", "buy", 2, price="10000")
        with self.assertRaises(ValueError):
            book.execute("y", "005930", "sell", 1, price="10000")
        self.assertEqual(before, book.cash)
        self.assertEqual([], book.fills)

    def test_same_client_id_conflicting_terms_is_rejected(self):
        book = StockPaperBook("KRW", "100000")
        book.execute("x", "005930", "buy", 1, price="10000")
        with self.assertRaises(ValueError):
            book.execute("x", "005930", "buy", 2, price="10000")
        self.assertEqual(1, len(book.fills))

    def test_rejects_fractional_shares_invalid_marks_and_unknown_currency(self):
        book = StockPaperBook("USD", "1000")
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                book.execute("x", "AAPL", "buy", value, price=100)
        book.execute("ok", "AAPL", "buy", 1, price=100)
        for marks in ({}, {"AAPL": "NaN"}, {"AAPL": 0}):
            with self.subTest(marks=marks), self.assertRaises(ValueError):
                book.marked_equity(marks)
        with self.assertRaises(ValueError):
            StockPaperBook("USDT", 1000)
        self.assertEqual(1, len(book.fills))


if __name__ == "__main__":
    unittest.main()
