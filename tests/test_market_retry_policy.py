"""Outer collectors must receive the provider's retry and stop policy intact."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/data'))
from trading_data import upbit_history as history


NOW = datetime(2026, 10, 1, 3, 10, tzinfo=timezone.utc)
URL = 'https://api.upbit.com/v1/candles/minutes/60'
BODY = json.dumps([{
    'market': 'KRW-BTC', 'unit': 60,
    'candle_date_time_utc': '2026-10-01T02:00:00',
    'opening_price': '100', 'high_price': '102', 'low_price': '99',
    'trade_price': '101', 'candle_acc_trade_volume': '1.25',
}]).encode()


def http_error(code, retry=None):
    headers = {} if retry is None else {'Retry-After': retry}
    return HTTPError(URL, code, 'provider response', headers, io.BytesIO(b'private body'))


class RetryPolicyTests(unittest.TestCase):
    def client(self, outcomes):
        self.calls, self.sleeps = [], []
        pending = iter(outcomes)

        def get(url):
            self.calls.append(url)
            result = next(pending)
            if isinstance(result, Exception):
                raise result
            return result

        return history.UpbitHistoryClient(get=get, sleep=self.sleeps.append, clock=lambda: NOW)

    def unavailable(self, outcomes):
        with self.assertRaises(history.MarketDataUnavailable) as caught:
            self.client(outcomes).download('KRW-BTC', unit=60, count=1)
        return caught.exception

    def assert_policy(self, error, reason, retryable, retry_after):
        self.assertEqual(getattr(error, 'reason', None), reason)
        self.assertIs(getattr(error, 'retryable', None), retryable)
        self.assertEqual(getattr(error, 'retry_after', None), retry_after)

    def test_legacy_exception_is_terminal_unless_retry_is_explicit(self):
        error = history.MarketDataUnavailable('original diagnostic')
        self.assertEqual(str(error), 'original diagnostic')
        self.assert_policy(error, 'unavailable', False, 0)

    def test_final_429_preserves_required_cooldown_and_three_attempt_cap(self):
        errors = [http_error(429, str(delay)) for delay in (1, 2, 7)]
        error = self.unavailable(errors)
        self.assert_policy(error, 'rate_limited', True, 7)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.sleeps, [0.2, 1.0, 0.2, 2.0, 0.2])
        self.assertTrue(all(item.fp.closed for item in errors))

    def test_final_429_without_header_has_nonzero_bounded_backoff(self):
        error = self.unavailable([http_error(429), http_error(429), http_error(429)])
        self.assert_policy(error, 'rate_limited', True, 4)

    def test_zero_server_delay_does_not_spin_an_outer_collector(self):
        error = self.unavailable([http_error(429, '0') for _ in range(3)])
        self.assert_policy(error, 'rate_limited', True, 1)

    def test_bad_final_cooldown_is_terminal_even_after_retryable_errors(self):
        for value in ('600', '30.1', '-1', 'NaN', 'inf', '-inf', '1e309', '',
                      'Thu, 01 Oct 2026 04:00:00 GMT'):
            with self.subTest(value=value):
                error = self.unavailable([http_error(429, '1'), http_error(429, '2'),
                                          http_error(429, value)])
                self.assert_policy(error, 'cooldown_required', False, 0)
                self.assertEqual(len(self.calls), 3)
                self.assertEqual(self.sleeps, [0.2, 1.0, 0.2, 2.0, 0.2])

    def test_initial_bad_cooldown_makes_only_one_request(self):
        for code in (429, 500, 503):
            with self.subTest(code=code):
                error = self.unavailable([http_error(code, '600')])
                self.assert_policy(error, 'cooldown_required', False, 0)
                self.assertEqual(len(self.calls), 1)

    def test_duplicate_cooldowns_are_terminal_instead_of_choosing_a_shorter_one(self):
        from email.message import Message
        for values in (('1', '600'), ('600', '1'), ('1', '2')):
            with self.subTest(values=values):
                headers = Message()
                for value in values:
                    headers['Retry-After'] = value
                final = HTTPError(URL, 429, 'ambiguous cooldown', headers, None)
                error = self.unavailable([http_error(429, '1'), http_error(429, '2'), final])
                self.assert_policy(error, 'cooldown_required', False, 0)

    def test_418_is_terminal_without_a_retry_or_cooldown_override(self):
        for preceding in ([], [http_error(503), http_error(503)]):
            with self.subTest(attempt=len(preceding) + 1):
                error = self.unavailable([*preceding, http_error(418, '1')])
                self.assert_policy(error, 'rate_blocked', False, 0)
                self.assertEqual(len(self.calls), len(preceding) + 1)

    def test_hard_http_rejections_are_terminal(self):
        for code in (301, 302, 400, 401, 403, 404, 422):
            with self.subTest(code=code):
                error = self.unavailable([http_error(code)])
                self.assert_policy(error, 'request_rejected', False, 0)
                self.assertEqual(len(self.calls), 1)

    def test_exhausted_connection_failures_are_retryable_with_bounded_backoff(self):
        for constructor in (URLError, TimeoutError, OSError):
            with self.subTest(exception=constructor.__name__):
                error = self.unavailable([constructor('offline') for _ in range(3)])
                self.assert_policy(error, 'transport_unavailable', True, 4)
                self.assertEqual(len(self.calls), 3)

    def test_exhausted_5xx_preserves_last_cooldown(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                error = self.unavailable([http_error(code, '1'), http_error(code, '2'),
                                          http_error(code, '30')])
                self.assert_policy(error, 'transport_unavailable', True, 30)

    def test_retryable_response_can_still_produce_original_validated_fixture(self):
        fixture = self.client([http_error(429, '2'), (BODY, {})]).download(
            'KRW-BTC', unit=60, count=1)
        self.assertEqual(fixture['candles'], [[1790820000000, '100', '102', '99', '101', '1.25']])
        self.assertEqual(fixture['data_provenance']['provider'], 'upbit_public_rest')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [0.2, 2.0, 0.2])


if __name__ == '__main__':
    unittest.main()
