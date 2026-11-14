"""Bounded loopback HTTP resources for the read-only operator dashboard.

This is a local utility, not an authenticated public application server.
"""
from http.client import LineTooLong
from http.server import ThreadingHTTPServer
import math
import socket
import threading
import time

MAX_CONNECTIONS = 8
MAX_HEADER_BYTES = 16 * 1024


class HeaderBudgetReader:
    """Limit aggregate headers before the standard parser materializes them."""

    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_HEADER_BYTES

    def readline(self, limit=-1):
        size = self.remaining + 1
        if limit >= 0:
            size = min(size, limit)
        line = self.stream.readline(size)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise LineTooLong("headers")
        return line


def _duration(value, maximum):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not 0 < value <= maximum):
        raise ValueError("invalid server limits")
    return float(value)


class BoundedHTTPServer(ThreadingHTTPServer):
    """Cap workers and expire complete connection lifetimes, including trickles."""

    request_queue_size = MAX_CONNECTIONS
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, *, max_connections=MAX_CONNECTIONS,
                 socket_timeout=2.0, connection_timeout=10.0):
        if type(max_connections) is not int or not 1 <= max_connections <= MAX_CONNECTIONS:
            raise ValueError("invalid server limits")
        self.socket_timeout = _duration(socket_timeout, 2.0)
        self.connection_timeout = _duration(connection_timeout, 10.0)
        if self.socket_timeout > self.connection_timeout:
            raise ValueError("invalid server limits")
        self._slots = threading.BoundedSemaphore(max_connections)
        self._gate = threading.Lock()
        self._connections = {}
        self._workers = set()
        self._closing = threading.Event()
        self._closed = False
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.socket_timeout)
        return request, address

    @staticmethod
    def _close_socket(request):
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            request.close()
        except OSError:
            pass

    def process_request(self, request, client_address):
        if self._closing.is_set() or not self._slots.acquire(blocking=False):
            self._close_socket(request)
            return
        worker = threading.Thread(target=self.process_request_thread,
                                  args=(request, client_address),
                                  name="uulab-dashboard-request", daemon=True)
        with self._gate:
            if self._closing.is_set():
                self._slots.release()
                self._close_socket(request)
                return
            self._connections[request] = time.monotonic() + self.connection_timeout
            self._workers.add(worker)
        try:
            worker.start()
        except Exception:
            with self._gate:
                self._connections.pop(request, None)
                self._workers.discard(worker)
            self._slots.release()
            self._close_socket(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._gate:
                self._connections.pop(request, None)
                self._workers.discard(threading.current_thread())
            self._slots.release()

    def handle_error(self, request, client_address):
        # Request identifiers, local paths and underlying exception strings are private.
        # Handler maps application errors to a fixed HTTP response when possible.
        pass

    def service_actions(self):
        now = time.monotonic()
        with self._gate:
            expired = [request for request, deadline in self._connections.items()
                       if deadline <= now]
        for request in expired:
            self._close_socket(request)

    def serve_forever(self, poll_interval=.1):
        # Expiry checks run independently of whether any new clients arrive.
        super().serve_forever(poll_interval=min(poll_interval, .1))

    def _stop_connections(self):
        self._closing.set()
        with self._gate:
            requests = list(self._connections)
        for request in requests:
            self._close_socket(request)

    def shutdown(self):
        self._stop_connections()
        super().shutdown()

    def server_close(self):
        with self._gate:
            if self._closed:
                return
            self._closed = True
        self._stop_connections()
        super().server_close()
        deadline = time.monotonic() + 2
        with self._gate:
            workers = list(self._workers)
        for worker in workers:
            if worker.ident is not None and worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
