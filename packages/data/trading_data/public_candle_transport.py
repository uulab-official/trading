"""Fixed-host HTTPS with one deadline for public candle socket I/O.

No proxy discovery, credentials, redirects or background deadline workers are
used. DNS and hung operating-system calls cannot be forcibly interrupted; once
they return, their elapsed time has consumed the same monotonic deadline.
"""
from http.client import HTTPException, HTTPSConnection
import io
import math
import socket
import time
from urllib.error import HTTPError, URLError

from .upbit_history import MAX_PAGE_BYTES, MarketDataUnavailable, validate_public_url


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('public candle I/O deadline exceeded')
    return remaining


def _connect_tcp(host, port, deadline):
    _remaining(deadline)
    # Name resolution may block in the operating system. It is not moved to a
    # worker that could outlive this request; its elapsed time still counts.
    addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    last_error = None
    for family, kind, protocol, _, address in addresses:
        timeout = _remaining(deadline)
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(timeout)
            sock.connect(address)
            _remaining(deadline)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
        except BaseException:
            sock.close()
            raise
    if last_error is not None:
        raise last_error
    raise OSError('public endpoint has no usable address')


class _DeadlineHTTPSConnection(HTTPSConnection):
    def __init__(self, host, *, timeout, deadline):
        # The standard HTTPS context enforces CA and hostname verification.
        super().__init__(host, timeout=timeout)
        self._deadline = deadline

    def connect(self):
        self.sock = _connect_tcp(self.host, self.port, self._deadline)
        try:
            self.sock.settimeout(_remaining(self._deadline))
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
            _remaining(self._deadline)
        except BaseException:
            self.sock.close()
            self.sock = None
            raise


class _DeadlineReader(io.RawIOBase):
    """Refresh the remaining budget before every socket receive, not every body."""

    def __init__(self, sock, deadline):
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        # A real makefile reference keeps the descriptor alive when http.client
        # closes a Connection: close socket before consuming its response body.
        self._raw = sock.makefile('rb', buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self._sock.settimeout(_remaining(self._deadline))
        result = self._raw.readinto(buffer)
        _remaining(self._deadline)
        return result

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self._sock = sock
        self._deadline = deadline

    def sendall(self, data):
        self._sock.settimeout(_remaining(self._deadline))
        self._sock.sendall(data)
        _remaining(self._deadline)

    def makefile(self, mode):
        if mode != 'rb':
            raise ValueError('public transport only reads response streams')
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))

    def close(self):
        self._sock.close()


class PublicCandleTransport:
    """One public GET; the optional connection factory is only a Python seam.

    The factory accepts ``factory(host, *, timeout)`` and returns an
    http.client-compatible connection. It is a trusted test injection, never a
    CLI URL or an alternative production endpoint.
    """

    def __init__(self, timeout=10.0, *, connection_factory=None):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.1 <= timeout <= 10:
            raise ValueError('transport timeout must be finite and between 0.1 and 10 seconds')
        self.timeout = float(timeout)
        self._connection_factory = connection_factory

    def __call__(self, url):
        parsed = validate_public_url(url)
        deadline = time.monotonic() + self.timeout
        if self._connection_factory is None:
            connection = _DeadlineHTTPSConnection('api.upbit.com', timeout=self.timeout, deadline=deadline)
        else:
            connection = self._connection_factory('api.upbit.com', timeout=self.timeout)
        response = None
        try:
            connection.connect()
            _remaining(deadline)
            connection.sock = _DeadlineSocket(connection.sock, deadline)
            connection.request('GET', parsed.path + '?' + parsed.query, headers={
                'Host': 'api.upbit.com', 'Accept': 'application/json',
                'User-Agent': 'uulab-history/1', 'Connection': 'close',
            })
            response = connection.getresponse()
            if response.status != 200:
                raise HTTPError(url, response.status, 'public endpoint rejected request', response.headers, None)
            if (len(response.headers.get_all('Content-Type', [])) != 1
                    or response.headers.get_content_type() != 'application/json'):
                raise MarketDataUnavailable('unexpected public response', reason='request_rejected')
            body = response.read(MAX_PAGE_BYTES + 1)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError('market response too large')
            if response.length not in (None, 0):
                raise URLError('incomplete public HTTP response')
            _remaining(deadline)
            return body, dict(response.headers.items())
        except HTTPException:
            raise URLError('invalid public HTTP response') from None
        finally:
            if response is not None:
                response.close()
            connection.close()
