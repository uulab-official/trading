"""Offline Toss token-reuse and read-only market-data contract tests."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone, tzinfo
from email.message import Message
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.toss_readonly_snapshot import fetch_readonly_minute, main


TOKEN = "EXAMPLE-TOKEN-DO-NOT-PRINT"
CANDLES = {"result": {"candles": [{
    "timestamp": "2026-03-25T09:30:00+09:00",
    "openPrice": "100", "highPrice": "120",
    "lowPrice": "90", "closePrice": "110",
    "volume": "10", "currency": "KRW",
}], "nextBefore": None}}


class _Response:
    def __init__(self, data, *, status=200, location=None):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status = self.code = status
        self.msg = "Example response"
        self.headers = Message()
        if location is not None:
            self.headers["Location"] = location

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, length):
        return self.data[:length]

    def close(self):
        pass


class TossReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.observed = datetime(2026, 3, 25, 1, 0, tzinfo=timezone.utc)
        for target in ("socket.socket.connect", "socket.create_connection",
                       "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("Network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def fake_open(self, request, *, timeout):
        self.requests.append(request)
        self.assertEqual(10, timeout)
        if (request.get_method() == "POST"
                and request.full_url == "https://openapi.tossinvest.com/oauth2/token"):
            return _Response({"access_token": TOKEN,
                              "token_type": "Bearer", "expires_in": 3600})
        if (request.get_method() == "GET" and request.full_url.startswith(
                "https://openapi.tossinvest.com/api/v1/candles?")):
            return _Response(CANDLES)
        self.fail("Unexpected request method or endpoint")

    def cli(self, output, environment, *, flags=(), opener=None):
        args = ["--mode", "paper", "--allow-network-readonly",
                "--symbol", "005930", "--currency", "KRW",
                "--output", str(output), *flags]
        stdout, stderr = StringIO(), StringIO()
        transport = SimpleNamespace(open=opener or self.fake_open)
        with patch.dict(os.environ, environment, clear=True), patch(
                "tools.toss_readonly_snapshot.build_opener", return_value=transport
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_only_oauth_post_and_market_data_get_are_requested(self):
        result = fetch_readonly_minute(
            "005930", "KRW", client_id="client", client_secret="secret",
            count=10, adjusted=False, issue_token=True, opener=self.fake_open,
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
                                  issue_token=True, opener=self.fake_open)
        self.assertEqual([], self.requests)

    def test_credentials_alone_refuse_without_issuing_a_token(self):
        with self.assertRaises(ValueError):
            fetch_readonly_minute(
                "005930", "KRW", client_id="client", client_secret="secret",
                opener=self.fake_open, observed_at=self.observed,
            )
        self.assertEqual([], self.requests)

    def test_supplied_token_requires_only_one_market_get(self):
        result = fetch_readonly_minute(
            "005930", "KRW", access_token=TOKEN, opener=self.fake_open,
            observed_at=self.observed,
        )
        self.assertEqual(1, len(self.requests))
        request = self.requests[0]
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.data)
        self.assertEqual("Bearer " + TOKEN, request.get_header("Authorization"))
        self.assertIsNone(request.get_header("X-Tossinvest-Account"))
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual("toss_readonly_authenticated_quote", result["source"])
        self.assertEqual("2026-03-25T01:00:00+00:00", result["observed_at_utc"])
        self.assertEqual([[1774398540000, "100", "120", "90", "110", "10"]],
                         result["candles"])
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_supplied_token_takes_precedence_over_credentials_and_issue_optin(self):
        for issue_token in (False, True):
            with self.subTest(issue_token=issue_token):
                self.requests.clear()
                fetch_readonly_minute(
                    "005930", "KRW", access_token=TOKEN,
                    client_id="client", client_secret="secret",
                    issue_token=issue_token, opener=self.fake_open,
                    observed_at=self.observed,
                )
                self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_supplied_token_does_not_require_valid_unused_credentials(self):
        fetch_readonly_minute(
            "005930", "KRW", access_token=TOKEN, issue_token=True,
            client_id=None, client_secret="unused\r\ncredential",
            opener=self.fake_open, observed_at=self.observed,
        )
        self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_invalid_supplied_tokens_refuse_before_any_request_or_fallback(self):
        invalid = {
            "empty": "", "spaces": " ", "embedded space": "abc def",
            "newline": "abc\ndef", "header injection": "abc\r\nX-Evil: 1",
            "tab": "abc\tdef", "NUL": "abc\x00def", "DEL": "abc\x7f",
            "Unicode": "abcé", "colon": "abc:def", "quotes": '"abc"',
            "scheme prefix": "Bearer abc", "internal padding": "ab=cd",
            "padding only": "=", "integer": 123, "boolean": True,
            "bytes": b"abc", "list": ["abc"], "oversized": "a" * 4097,
        }
        for label, token in invalid.items():
            for issue_token in (False, True):
                with self.subTest(case=label, issue_token=issue_token):
                    with self.assertRaises(ValueError):
                        fetch_readonly_minute(
                            "005930", "KRW", access_token=token,
                            client_id="client", client_secret="secret",
                            issue_token=issue_token, opener=self.fake_open,
                            observed_at=self.observed,
                        )
                    self.assertEqual([], self.requests)

    def test_bearer_grammar_and_local_length_boundary_are_supported(self):
        for label, token in (("short", "a"), ("all symbols", "AZaz09-._~+/=="),
                             ("length boundary", "a" * 4096)):
            with self.subTest(case=label):
                self.requests.clear()
                fetch_readonly_minute(
                    "005930", "KRW", access_token=token,
                    opener=self.fake_open, observed_at=self.observed,
                )
                self.assertEqual(1, len(self.requests))
                self.assertEqual("Bearer " + token,
                                 self.requests[0].get_header("Authorization"))

    def test_invalid_issuance_credentials_refuse_before_network(self):
        invalid = (None, "", " ", 123, True, b"abc", "abc\r\nX-Evil: 1",
                   "abc\t", "abc\x00", "abc\x7f", "abcé", "a" * 4097)
        for field in ("client_id", "client_secret"):
            for index, value in enumerate(invalid):
                with self.subTest(field=field, case=index):
                    credentials = {"client_id": "client", "client_secret": "secret"}
                    credentials[field] = value
                    with self.assertRaises(ValueError):
                        fetch_readonly_minute(
                            "005930", "KRW", issue_token=True,
                            opener=self.fake_open, observed_at=self.observed,
                            **credentials,
                        )
                    self.assertEqual([], self.requests)

    def test_issued_credentials_remain_form_encoded(self):
        fetch_readonly_minute(
            "005930", "KRW", client_id="client+id&scope=paper",
            client_secret="secret /?&=:+", issue_token=True,
            opener=self.fake_open, observed_at=self.observed,
        )
        self.assertEqual({
            "grant_type": ["client_credentials"],
            "client_id": ["client+id&scope=paper"],
            "client_secret": ["secret /?&=:+"],
        }, parse_qs(self.requests[0].data.decode("utf-8")))

    def test_invalid_issue_optin_refuses_before_network(self):
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fetch_readonly_minute(
                    "005930", "KRW", access_token=TOKEN,
                    issue_token=value, opener=self.fake_open,
                )
        self.assertEqual([], self.requests)

    def test_invalid_request_inputs_refuse_before_token_issuance(self):
        for values in (
            {"symbol": "005930\r\nX-Evil: 1"}, {"symbol": ""},
            {"currency": "EUR"}, {"count": 0}, {"count": 201},
            {"count": True}, {"adjusted": "true"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                inputs = dict(symbol="005930", currency="KRW", count=10, adjusted=True)
                inputs.update(values)
                fetch_readonly_minute(
                    **inputs, client_id="client", client_secret="secret",
                    issue_token=True, opener=self.fake_open,
                    observed_at=self.observed,
                )
        self.assertEqual([], self.requests)

    def test_invalid_observation_time_refuses_before_token_issuance(self):
        class NoOffset(tzinfo):
            def utcoffset(self, unused):
                return None

        for value in ("2026-03-25T01:00:00Z", 0, False,
                      datetime(2026, 3, 25), datetime(2026, 3, 25, tzinfo=NoOffset())):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    fetch_readonly_minute(
                        "005930", "KRW", client_id="client", client_secret="secret",
                        issue_token=True, opener=self.fake_open, observed_at=value,
                    )
                self.assertEqual([], self.requests)

    def test_observation_offset_is_normalized_to_utc(self):
        observed = datetime(2026, 3, 25, 10, 0,
                            tzinfo=timezone(timedelta(hours=9)))
        result = fetch_readonly_minute(
            "005930", "KRW", access_token=TOKEN,
            opener=self.fake_open, observed_at=observed,
        )
        self.assertEqual("2026-03-25T01:00:00+00:00", result["observed_at_utc"])

    def test_failed_get_never_retries_refreshes_or_falls_back_to_credentials(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.requests.clear()

                def failed(request, *, timeout):
                    self.requests.append(request)
                    raise HTTPError(request.full_url, status, "Rejected " + TOKEN,
                                    {}, BytesIO(TOKEN.encode()))

                with self.assertRaises(HTTPError):
                    fetch_readonly_minute(
                        "005930", "KRW", access_token=TOKEN, issue_token=True,
                        client_id="client", client_secret="secret",
                        opener=failed, observed_at=self.observed,
                    )
                self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_failure_after_explicit_issuance_never_issues_a_second_token(self):
        def failed_get(request, *, timeout):
            if request.get_method() == "POST":
                return self.fake_open(request, timeout=timeout)
            self.requests.append(request)
            raise HTTPError(request.full_url, 401, "Token rejected", {}, None)

        with self.assertRaises(HTTPError):
            fetch_readonly_minute(
                "005930", "KRW", issue_token=True,
                client_id="client", client_secret="secret",
                opener=failed_get, observed_at=self.observed,
            )
        self.assertEqual(["POST", "GET"], [r.get_method() for r in self.requests])

    def test_malformed_market_responses_do_not_retry(self):
        for body in (b"{", b"[]", {"result": {"candles": []}},
                     {"error": "EXAMPLE-SECRET-DO-NOT-PRINT"}):
            with self.subTest(body=body):
                self.requests.clear()

                def malformed(request, *, timeout):
                    self.requests.append(request)
                    return _Response(body)

                with self.assertRaises(ValueError):
                    fetch_readonly_minute(
                        "005930", "KRW", access_token=TOKEN, issue_token=True,
                        client_id="client", client_secret="secret",
                        opener=malformed, observed_at=self.observed,
                    )
                self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_invalid_issued_token_cannot_reach_a_market_request(self):
        for token in (None, "", "abc\r\nX-Evil: 1", "abc\x00", "abc:def",
                      "abc=def", "=", "abcé", "a" * 4097):
            with self.subTest(case=type(token).__name__, length=len(token or "")):
                self.requests.clear()

                def invalid(request, *, timeout):
                    self.requests.append(request)
                    return _Response({"access_token": token, "token_type": "Bearer"})

                with self.assertRaises(ValueError):
                    fetch_readonly_minute(
                        "005930", "KRW", client_id="client", client_secret="secret",
                        issue_token=True, opener=invalid, observed_at=self.observed,
                    )
                self.assertEqual(["POST"], [r.get_method() for r in self.requests])

    def test_environment_proxy_is_not_used_by_real_default_transport(self):
        destinations = []

        def https_open(handler, request):
            destinations.append((request.host, request._tunnel_host))
            return self.fake_open(request, timeout=10)

        with patch.dict(os.environ, {
                "HTTPS_PROXY": "http://proxy.invalid:3128",
                "https_proxy": "http://proxy.invalid:3128", "NO_PROXY": "",
                "no_proxy": "",
        }, clear=True), patch("urllib.request.HTTPSHandler.https_open", https_open):
            fetch_readonly_minute(
                "005930", "KRW", client_id="client", client_secret="secret",
                issue_token=True, observed_at=self.observed,
            )
        self.assertEqual([("openapi.tossinvest.com", None)] * 2, destinations)

    def test_real_default_transport_refuses_all_redirects_without_second_request(self):
        for status in (301, 302, 303, 307, 308):
            for destination in ("https://openapi.tossinvest.com/other",
                                "https://different-host.invalid/steal"):
                with self.subTest(status=status, destination=destination):
                    self.requests.clear()

                    def redirected(handler, request):
                        self.requests.append(request)
                        return _Response({}, status=status, location=destination)

                    with patch("urllib.request.HTTPSHandler.https_open", redirected):
                        with self.assertRaises(ValueError):
                            fetch_readonly_minute(
                                "005930", "KRW", access_token=TOKEN,
                                observed_at=self.observed,
                            )
                    self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_http_308_is_refused_even_without_stdlib_redirect_support(self):
        # Python 3.10 has no inherited 308 handler. Exercise that same capability
        # boundary on newer runtimes too, restoring the shared class afterward.
        absent = object()
        inherited = HTTPRedirectHandler.__dict__.get("http_error_308", absent)
        if inherited is not absent:
            delattr(HTTPRedirectHandler, "http_error_308")
        try:
            for method in ("GET", "POST"):
                for destination in ("https://openapi.tossinvest.com/other",
                                    "https://different-host.invalid/steal"):
                    with self.subTest(method=method, destination=destination):
                        self.requests.clear()

                        def redirected(handler, request):
                            self.requests.append(request)
                            return _Response({}, status=308, location=destination)

                        auth = {"access_token": TOKEN} if method == "GET" else {
                            "issue_token": True, "client_id": "client", "client_secret": "secret",
                        }
                        with patch("urllib.request.HTTPSHandler.https_open", redirected):
                            with self.assertRaisesRegex(ValueError, "redirects are forbidden"):
                                fetch_readonly_minute(
                                    "005930", "KRW", observed_at=self.observed, **auth,
                                )
                        self.assertEqual([method], [r.get_method() for r in self.requests])
        finally:
            if inherited is not absent:
                HTTPRedirectHandler.http_error_308 = inherited

    def test_cli_reuses_environment_token_and_writes_only_private_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quote.json"
            result, stdout, stderr = self.cli(output, {"TOSS_ACCESS_TOKEN": TOKEN})
            self.assertEqual(0, result)
            self.assertEqual(["GET"], [r.get_method() for r in self.requests])
            self.assertEqual(str(output) + "\n", stdout)
            self.assertEqual("", stderr)
            self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
            self.assertEqual([output], list(Path(directory).iterdir()))
            snapshot = output.read_text()
            self.assertNotIn(TOKEN, snapshot + stdout + stderr)
            self.assertEqual("paper", json.loads(snapshot)["mode"])

    def test_cli_credentials_alone_refuse_without_network_or_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quote.json"
            result, stdout, stderr = self.cli(output, {
                "TOSS_CLIENT_ID": "EXAMPLE-CLIENT-DO-NOT-PRINT",
                "TOSS_CLIENT_SECRET": "EXAMPLE-SECRET-DO-NOT-PRINT",
            })
            self.assertEqual(2, result)
            self.assertEqual([], self.requests)
            self.assertFalse(output.exists())
            self.assertEqual("", stdout)
            self.assertNotIn("EXAMPLE-", stderr)

    def test_cli_explicit_issue_token_preserves_one_shot_oauth(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quote.json"
            result, stdout, stderr = self.cli(output, {
                "TOSS_CLIENT_ID": "EXAMPLE-CLIENT-DO-NOT-PRINT",
                "TOSS_CLIENT_SECRET": "EXAMPLE-SECRET-DO-NOT-PRINT",
            }, flags=("--issue-token",))
            self.assertEqual(0, result)
            self.assertEqual(["POST", "GET"], [r.get_method() for r in self.requests])
            self.assertNotIn("EXAMPLE-", output.read_text() + stdout + stderr)

    def test_cli_supplied_token_wins_even_with_explicit_issuance(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quote.json"
            result, _, _ = self.cli(output, {
                "TOSS_ACCESS_TOKEN": TOKEN,
                "TOSS_CLIENT_ID": "client", "TOSS_CLIENT_SECRET": "secret",
            }, flags=("--issue-token",))
            self.assertEqual(0, result)
            self.assertEqual(["GET"], [r.get_method() for r in self.requests])

    def test_cli_hostile_http_error_cannot_expose_credentials_or_create_output(self):
        def failed(request, *, timeout):
            self.requests.append(request)
            private = "EXAMPLE-UPSTREAM-SECRET " + TOKEN
            raise HTTPError(request.full_url + private, 401, private, {},
                            BytesIO(private.encode()))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "quote.json"
            result, stdout, stderr = self.cli(output, {
                "TOSS_ACCESS_TOKEN": TOKEN,
                "TOSS_CLIENT_ID": "EXAMPLE-CLIENT-DO-NOT-PRINT",
                "TOSS_CLIENT_SECRET": "EXAMPLE-SECRET-DO-NOT-PRINT",
            }, flags=("--issue-token",), opener=failed)
            self.assertEqual(2, result)
            self.assertEqual(["GET"], [r.get_method() for r in self.requests])
            self.assertEqual("", stdout)
            self.assertNotIn("EXAMPLE-", stderr)
            self.assertFalse(output.exists())
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_cli_requires_explicit_network_optin_and_paper_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stocks.json"
            base = ["--mode", "paper", "--symbol", "005930",
                    "--currency", "KRW", "--output", str(path)]
            with self.assertRaises(SystemExit) as exc, redirect_stderr(StringIO()):
                main(base)
            self.assertEqual(2, exc.exception.code)
            self.assertFalse(path.exists())
            live = base.copy()
            live[1] = "live"
            with self.assertRaises(SystemExit) as exc, redirect_stderr(StringIO()):
                main(live)
            self.assertEqual(2, exc.exception.code)

    def test_output_with_nondirectory_ancestor_refuses_before_token_issuance(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_file = Path(directory) / "parent-file"
            parent_file.write_text("existing evidence")
            for suffix in ("quote.json", "missing/quote.json"):
                with self.subTest(suffix=suffix):
                    output = parent_file / suffix
                    args = [
                        "--mode", "paper", "--allow-network-readonly", "--issue-token",
                        "--symbol", "005930", "--currency", "KRW", "--output", str(output),
                    ]
                    with patch("tools.toss_readonly_snapshot.fetch_readonly_minute") as fetch:
                        with self.assertRaises(SystemExit) as failure, redirect_stderr(StringIO()):
                            main(args)
                        self.assertEqual(2, failure.exception.code)
                        fetch.assert_not_called()
                    self.assertEqual("existing evidence", parent_file.read_text())

    def test_existing_snapshot_refused_before_oauth_rotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "quote.json"
            output.write_text("existing paper evidence")
            args = [
                "--mode", "paper", "--allow-network-readonly",
                "--symbol", "005930", "--currency", "KRW",
                "--output", str(output)
            ]
            with patch("tools.toss_readonly_snapshot.fetch_readonly_minute",
                       side_effect=AssertionError("OAuth must not run")):
                with self.assertRaises(SystemExit) as error, redirect_stderr(StringIO()):
                    main(args)
            self.assertEqual(2, error.exception.code)
            self.assertEqual("existing paper evidence", output.read_text())

    def test_dangling_output_symlink_refuses_before_token_issuance(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing-target.json"
            output = Path(directory) / "quote.json"
            output.symlink_to(target)
            with self.assertRaises(SystemExit) as error:
                self.cli(output, {
                    "TOSS_CLIENT_ID": "client", "TOSS_CLIENT_SECRET": "secret",
                }, flags=("--issue-token",))
            self.assertEqual(2, error.exception.code)
            self.assertEqual([], self.requests)
            self.assertTrue(output.is_symlink())
            self.assertFalse(target.exists())

    def test_invalid_token_rejected_before_market_request(self):
        def invalid(request, *, timeout):
            self.requests.append(request)
            return _Response({"access_token": "fake", "token_type": "Other"})
        with self.assertRaises(ValueError):
            fetch_readonly_minute(
                "005930", "KRW", client_id="id", client_secret="secret",
                issue_token=True, opener=invalid,
            )
        self.assertEqual(1, len(self.requests))


if __name__ == "__main__":
    unittest.main()
