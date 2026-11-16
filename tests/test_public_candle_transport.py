"""Real loopback I/O for the explicit public candle transport; no provider calls."""
from contextlib import contextmanager
import http.client
import importlib
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/data'))
from trading_data import upbit_history as history


URL = ('https://api.upbit.com/v1/candles/minutes/60?market=KRW-BTC'
       '&count=1&to=2026-10-01T03%3A00%3A00Z')
TRANSPORT = Path(__file__).resolve().parents[1] / 'packages/data/trading_data/public_candle_transport.py'


def response(body=b'[]', *, status=200, headers=()):
    fields = [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
              ('Connection', 'close'), *headers]
    head = f'HTTP/1.1 {status} fixture\r\n' + ''.join(f'{key}: {value}\r\n' for key, value in fields)
    return (head + '\r\n').encode() + body


class ObservedConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        self.opened_socket = self.sock


@contextmanager
def loopback(chunks, *, read_headers=True):
    """One real TCP peer; delays are cancellable and the test owns its thread."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    stop = threading.Event()
    state = {'requests': [], 'connections': [], 'errors': [], 'address': listener.getsockname()}

    def serve():
        try:
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(2)
                request = bytearray()
                while b'\r\n\r\n' not in request:
                    block = peer.recv(4096)
                    if not block:
                        return
                    request.extend(block)
                    if not read_headers:
                        break
                    if len(request) > 8192:
                        raise AssertionError('unbounded request')
                state['requests'].append(bytes(request))
                for delay, data in chunks:
                    if stop.wait(delay):
                        return
                    peer.sendall(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # An interrupted or rejected response closes the client immediately.
        except Exception as error:
            if not stop.is_set():
                state['errors'].append(error)

    thread = threading.Thread(target=serve, name='public-candle-loopback')
    thread.start()

    def factory(host, *, timeout):
        if host != 'api.upbit.com':
            raise AssertionError('transport changed its fixed destination')
        connection = ObservedConnection('127.0.0.1', listener.getsockname()[1], timeout=timeout)
        state['connections'].append(connection)
        return connection

    state['factory'] = factory
    try:
        yield state
    finally:
        stop.set()
        listener.close()
        thread.join(3)
        if thread.is_alive():
            raise AssertionError('loopback fixture leaked a worker')
        if state['errors']:
            raise AssertionError(state['errors'])
        for connection in state['connections']:
            if connection.sock is not None or connection.opened_socket.fileno() != -1:
                raise AssertionError('transport leaked its connection')


class PublicTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TRANSPORT.is_file(), 'bounded public candle transport is missing')
        self.module = importlib.import_module('trading_data.public_candle_transport')

    def test_closed_public_get_returns_original_body_and_headers(self):
        wire = response(b'[{"unit":60}]', headers=[('Remaining-Req', 'group=candle; sec=4')])
        with loopback([(0, wire)]) as peer:
            body, headers = self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(body, b'[{"unit":60}]')
        self.assertEqual(headers['Remaining-Req'], 'group=candle; sec=4')
        self.assertEqual(len(peer['requests']), 1)
        self.assertTrue(peer['requests'][0].startswith(
            b'GET /v1/candles/minutes/60?market=KRW-BTC&count=1&to=2026-10-01T03%3A00%3A00Z HTTP/1.1\r\n'))

    def test_request_ignores_ambient_proxies_and_has_no_authorization_or_origin(self):
        proxy_env = {name: 'http://test-user:test-password@127.0.0.1:9'
                     for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')}
        with patch.dict(os.environ, proxy_env), loopback([(0, response())]) as peer:
            body, _ = self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(body, b'[]')
        request = peer['requests'][0].decode().lower()
        self.assertIn('\r\nhost: api.upbit.com\r\n', request)
        self.assertIn('\r\naccept: application/json\r\n', request)
        self.assertNotIn('authorization:', request)
        self.assertNotIn('\r\norigin:', request)
        self.assertNotIn('\r\ncookie:', request)
        self.assertNotIn('test-password', request)

    def test_invalid_endpoint_or_query_never_opens_a_connection(self):
        invalid = [
            URL.replace('https:', 'http:'), URL.replace('api.upbit.com', 'evil.example'),
            URL.replace('api.upbit.com', 'user:password@api.upbit.com'),
            URL.replace('api.upbit.com', 'api.upbit.com:443'),
            URL.replace('/candles/minutes/60', '/orders'), URL.replace('/60?', '/2?'),
            URL + '#fragment', URL + '&count=1', URL + '&secret=forbidden',
            URL.replace('count=1', 'count=201'), URL.replace('count=1', 'count=0'),
            URL.replace('KRW-BTC', 'USDT-BTC'), URL.replace('to=2026-10-01T03%3A00%3A00Z', 'to=invalid'),
        ]
        calls = []

        def forbidden(*args, **kwargs):
            calls.append((args, kwargs))
            self.fail('invalid input reached the connection boundary')

        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.module.PublicCandleTransport(connection_factory=forbidden)(url)
        self.assertEqual(calls, [])

    def test_timeout_is_finite_bounded_and_validated_before_network(self):
        for value in (None, True, False, '1', 0, -1, 0.09, 10.01, float('inf'), float('-inf'), float('nan')):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                self.module.PublicCandleTransport(timeout=value)

    def test_redirect_is_returned_as_http_error_without_following_location(self):
        wire = response(status=302, headers=[('Location', 'https://evil.example/private')])
        with loopback([(0, wire)]) as peer:
            with self.assertRaises(HTTPError) as caught:
                self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(len(peer['requests']), 1)
        caught.exception.close()

    def test_http_cooldown_headers_reach_history_client_intact(self):
        wire = response(status=429, headers=[('Retry-After', '17')])
        with loopback([(0, wire)]) as peer:
            with self.assertRaises(HTTPError) as caught:
                self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(caught.exception.headers['Retry-After'], '17')
        caught.exception.close()

    def test_non_json_response_is_rejected_and_closed(self):
        wire = b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 4\r\n\r\noops'
        with loopback([(0, wire)]) as peer:
            with self.assertRaises(history.MarketDataUnavailable):
                self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)

    def test_body_at_the_exact_cap_is_returned(self):
        body = b' ' * (1024 * 1024)
        with loopback([(0, response(body))]) as peer:
            actual, _ = self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(actual, body)

    def test_oversized_body_is_rejected_with_or_without_content_length(self):
        oversized = b' ' * (1024 * 1024 + 1)
        responses = [response(oversized), b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n' + oversized]
        for wire in responses:
            with self.subTest(content_length=b'Content-Length:' in wire), loopback([(0, wire)]) as peer:
                with self.assertRaises(ValueError):
                    self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)

    def test_chunked_body_is_decoded_by_the_http_boundary(self):
        wire = (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n'
                b'1\r\n[\r\n1\r\n]\r\n0\r\n\r\n')
        with loopback([(0, wire)]) as peer:
            body, _ = self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
        self.assertEqual(body, b'[]')

    def test_public_url_validation_is_reusable_without_opening_network(self):
        self.assertTrue(callable(getattr(history, 'validate_public_url', None)))
        parsed = history.validate_public_url(URL)
        self.assertEqual(parsed.netloc, 'api.upbit.com')
        self.assertEqual(parsed.path, '/v1/candles/minutes/60')

    def assert_deadline(self, chunks):
        with loopback(chunks) as peer:
            threads_before = {item.ident for item in threading.enumerate()}
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                self.module.PublicCandleTransport(timeout=0.2, connection_factory=peer['factory'])(URL)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.6, 'socket inactivity timeouts did not enforce a total deadline')
            self.assertEqual({item.ident for item in threading.enumerate()} - threads_before, set(),
                             'a deadline worker survived its request')

    def test_trickled_headers_cannot_extend_the_total_deadline(self):
        prefix = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: '
        chunks = [(0, prefix), *[(0.04, b'x') for _ in range(12)],
                  (0, b'\r\nContent-Length: 2\r\n\r\n[]')]
        self.assert_deadline(chunks)

    def test_trickled_body_cannot_extend_the_total_deadline(self):
        head = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n'
        self.assert_deadline([(0, head), *[(0.04, b' ') for _ in range(20)]])

    def test_body_does_not_receive_a_fresh_deadline_after_slow_headers(self):
        head = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n'
        self.assert_deadline([(0.13, head), (0.13, b'[]')])

    def test_trickled_chunk_framing_cannot_extend_the_deadline(self):
        head = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n'
        self.assert_deadline([(0, head), *[(0.04, b'1\r\n \r\n') for _ in range(12)], (0, b'0\r\n\r\n')])

    def test_default_tls_connect_uses_remaining_total_budget_after_dns(self):
        # DNS is not preemptible. Once it returns, the TLS handshake must use
        # the remaining request budget, not receive a new full socket timeout.
        with loopback([(2, b'')], read_headers=False) as peer:
            def resolve(host, port, *args, **kwargs):
                self.assertEqual((host, port), ('api.upbit.com', 443))
                time.sleep(0.2)
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', peer['address'])]

            with patch.object(socket, 'getaddrinfo', side_effect=resolve), patch.dict(
                    os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9'}):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    self.module.PublicCandleTransport(timeout=0.3)(URL)
                self.assertLess(time.monotonic() - started, 0.44,
                                'TLS reset the total deadline after name resolution')
        self.assertEqual(peer['requests'][0][:2], b'\x16\x03', 'the default connection did not use TLS')

    def test_malformed_or_truncated_http_raises_a_compatible_connection_error(self):
        responses = [
            b'not-an-http-status\r\n\r\n',
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 12\r\n\r\n[]',
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n',
        ]
        for wire in responses:
            with self.subTest(response=wire[:60]), loopback([(0, wire)]) as peer:
                try:
                    self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)
                except Exception as error:
                    self.assertIsInstance(error, (URLError, OSError),
                                          'the history client cannot classify this network failure')
                else:
                    self.fail('a malformed or truncated response was accepted')

    def test_ambiguous_content_type_is_not_accepted_as_json(self):
        wire = response(headers=[('Content-Type', 'text/html')])
        with loopback([(0, wire)]) as peer:
            with self.assertRaises(history.MarketDataUnavailable):
                self.module.PublicCandleTransport(connection_factory=peer['factory'])(URL)

    def test_raw_whitespace_is_rejected_before_urlsplit_can_discard_it(self):
        calls = []

        def forbidden(*args, **kwargs):
            calls.append((args, kwargs))
            self.fail('unvalidated URL reached the connection boundary')

        for url in (' ' + URL, '\n' + URL, URL.replace('api.upbit.com', 'api.\nupbit.com'),
                    URL.replace('%3A', ' ', 1)):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.module.PublicCandleTransport(connection_factory=forbidden)(url)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
