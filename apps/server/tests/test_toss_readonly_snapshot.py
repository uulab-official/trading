"""Mock-only Toss OAuth read-only market-data contract tests."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.toss_readonly_snapshot import fetch_readonly_minute, main


class _Response:
    status = 200

    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, length):
        return self.data[:length]


class TossReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.observed = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)

    def fake_open(self, request, *, timeout):
        self.requests.append(request)
        if len(self.requests) == 1:
            return _Response({"access_token": "EXAMPLE-TOKEN-DO-NOT-PRINT",
                              "token_type": "Bearer", "expires_in": 3600})
        return _Response({"result": {"candles": [{
            "timestamp": "2026-03-25T09:30:00+09:00",
            "openPrice": "100", "highPrice": "120",
            "lowPrice": "90", "closePrice": "110",
            "volume": "10", "currency": "KRW",
        }]}})

    def test_only_oauth_post_and_market_data_get_are_requested(self):
        result = fetch_readonly_minute(
            "005930", "KRW", client_id="client", client_secret="secret",
            count=10, adjusted=False, opener=self.fake_open,
            observed_at=self.observed,
        )
        self.assertEqual("paper", result["mode"])
        self.assertTrue(result["no_live_orders"])
        self.assertEqual(2, len(self.requests))
        token, data = self.requests
        self.assertEqual("POST", token.get_method())
        self.assertTrue(token.full_url.endswith("/oauth2/token"))
        self.assertIn(b"client_secret=secret", token.data)
        self.assertEqual("GET", data.get_method())
        self.assertTrue(data.full_url.startswith(
            "https://openapi.tossinvest.com/api/v1/candles?"
        ))
        self.assertIn("adjusted=false", data.full_url)
        self.assertEqual("Bearer EXAMPLE-TOKEN-DO-NOT-PRINT",
                         data.get_header("Authorization"))
        self.assertIsNone(data.get_header("X-Tossinvest-Account"))
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("EXAMPLE-TOKEN", json.dumps(result))

    def test_missing_credentials_fails_without_network_call(self):
        with self.assertRaises(ValueError):
            fetch_readonly_minute("AAPL", "USD",
                                  client_id=None, client_secret="",
                                  opener=self.fake_open)
        self.assertEqual([], self.requests)

    def test_cli_requires_explicit_network_optin_and_paper_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stocks.json"
            base = ["--mode", "paper", "--symbol", "005930",
                    "--currency", "KRW", "--output", str(path)]
            from contextlib import redirect_stderr
            from io import StringIO
            with self.assertRaises(SystemExit) as exc, redirect_stderr(StringIO()):
                main(base)
            self.assertEqual(2, exc.exception.code)
            self.assertFalse(path.exists())
            live = base.copy()
            live[1] = "live"
            with self.assertRaises(SystemExit) as exc, redirect_stderr(StringIO()):
                main(live)
            self.assertEqual(2, exc.exception.code)

    def test_existing_snapshot_refused_before_oauth_rotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "quote.json"
            output.write_text("existing paper evidence")
            args = [
                "--mode", "paper", "--allow-network-readonly",
                "--symbol", "005930", "--currency", "KRW",
                "--output", str(output)
            ]
            from contextlib import redirect_stderr
            from io import StringIO
            with patch("tools.toss_readonly_snapshot.fetch_readonly_minute",
                       side_effect=AssertionError("OAuth must not run")):
                with self.assertRaises(SystemExit) as error, redirect_stderr(StringIO()):
                    main(args)
            self.assertEqual(2, error.exception.code)
            self.assertEqual("existing paper evidence", output.read_text())

    def test_invalid_token_rejected_before_market_request(self):
        def invalid(request, *, timeout):
            self.requests.append(request)
            return _Response({"access_token": "fake", "token_type": "Other"})
        with self.assertRaises(ValueError):
            fetch_readonly_minute(
                "005930", "KRW", client_id="id", client_secret="secret",
                opener=invalid,
            )
        self.assertEqual(1, len(self.requests))


if __name__ == "__main__":
    unittest.main()
