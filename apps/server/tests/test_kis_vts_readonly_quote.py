"""KIS VTS read-only quote safety contract without real network access."""

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.kis_vts_readonly_quote import fetch_quote, main


class MockResponse:
    status = 200

    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount):
        return self.data[:amount]


class KisVtsQuoteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.inputs = dict(
            token="FAKE-VTS-TOKEN-NEVER-PRINT",
            appkey="FAKE-APPKEY",
            appsecret="FAKE-APPSECRET",
        )

    def open_quote(self, request, *, timeout):
        self.calls.append(request)
        return MockResponse({"rt_cd": "0", "output": {"stck_prpr": "80100"}})

    def test_only_kis_virtual_host_and_quote_get_called(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network")):
            result = fetch_quote(
                "005930",
                opener=self.open_quote,
                now=lambda: datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc),
                **self.inputs,
            )
        self.assertEqual(1, len(self.calls))
        request = self.calls[0]
        self.assertEqual("GET", request.get_method())
        self.assertTrue(
            request.full_url.startswith(
                "https://openapivts.koreainvestment.com:29443/"
                "uapi/domestic-stock/v1/quotations/inquire-price?"
            )
        )
        self.assertIn("FID_INPUT_ISCD=005930", request.full_url)
        self.assertEqual("FHKST01010100", request.get_header("Tr_id"))
        self.assertEqual("Bearer FAKE-VTS-TOKEN-NEVER-PRINT", request.get_header("Authorization"))
        self.assertEqual("80100", result["price"])
        self.assertFalse(result["validated_completed_bars"])
        self.assertFalse(result["live_orders_enabled"])
        serialized = json.dumps(result)
        self.assertNotIn("FAKE-VTS", serialized)
        self.assertNotIn("FAKE-APPSECRET", serialized)

    def test_market_data_cannot_be_faked_by_invalid_price_or_rt_cd(self):
        for response in (
            {"rt_cd": "1", "output": {"stck_prpr": "80000"}},
            {"rt_cd": "0", "output": {"stck_prpr": "NaN"}},
            {"rt_cd": "0", "output": {"stck_prpr": "0"}},
            {"rt_cd": "0", "output": None},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                fetch_quote(
                    "005930", opener=lambda request, timeout: MockResponse(response), **self.inputs
                )

    def test_invalid_symbol_or_missing_token_cannot_request_network(self):
        with self.assertRaises(ValueError):
            fetch_quote("not-a-stock", opener=self.open_quote, **self.inputs)
        with self.assertRaises(ValueError):
            fetch_quote(
                "005930", opener=self.open_quote, token=None, appkey="key", appsecret="secret"
            )
        self.assertEqual([], self.calls)

    def test_cli_rejects_live_and_missing_optin_without_creating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quote.json"
            args = ["--mode", "paper", "--symbol", "005930", "--output", str(path)]
            with self.assertRaises(SystemExit) as error, redirect_stderr(StringIO()):
                main(args)
            self.assertEqual(2, error.exception.code)
            invalid = args.copy()
            invalid[1] = "live"
            with self.assertRaises(SystemExit) as error, redirect_stderr(StringIO()):
                main(invalid)
            self.assertEqual(2, error.exception.code)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
