"""Local read-only paper operations server. Never expose this service publicly.

Host/Origin checks mitigate browser-origin attacks; they are not authentication.
No credential handling, broker connection, order submission or fill simulation.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .store import JournalUnavailable, STATUSES, read_snapshot
    from .runtime import BoundedHTTPServer, HeaderBudgetReader
else:  # Keep direct script execution and the repository's file-loader tests working.
    _spec = importlib.util.spec_from_file_location("uulab_dashboard_store", Path(__file__).with_name("store.py"))
    _store = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_store)
    JournalUnavailable, STATUSES, read_snapshot = _store.JournalUnavailable, _store.STATUSES, _store.read_snapshot
    _runtime_spec = importlib.util.spec_from_file_location("uulab_dashboard_runtime", Path(__file__).with_name("runtime.py"))
    _runtime = importlib.util.module_from_spec(_runtime_spec)
    _runtime_spec.loader.exec_module(_runtime)
    BoundedHTTPServer, HeaderBudgetReader = _runtime.BoundedHTTPServer, _runtime.HeaderBudgetReader

def _report_reader(filename):
    # Optional sources are independent; unused readers need not be importable.
    path = Path(__file__).resolve().parents[1] / 'server/trading_bot' / filename
    spec = importlib.util.spec_from_file_location('uulab_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _market_capture_status(directory):
    if directory is None:
        raise ValueError('capture not configured')
    # This optional observer imports only stdlib/pure market-data modules.
    import sys
    root = Path(__file__).resolve().parents[2]
    for component in ('packages/data', 'apps/server'):
        path = str(root / component)
        if path not in sys.path:
            sys.path.insert(0, path)
    from trading_bot.market_capture import capture_status
    return capture_status(directory)


def _order_page_query(query):
    if set(query) - {'offset', 'limit', 'status', 'revision', 'digest'}:
        raise ValueError('unsupported page parameter')
    numbers = {}
    for key, default in (('offset', '0'), ('limit', '50')):
        value = query.get(key, [default])[0]
        if not re.fullmatch(r'0|[1-9][0-9]{0,4}', value):
            raise ValueError('invalid page number')
        numbers[key] = int(value)
    return dict(**numbers, status=query.get('status', ['all'])[0],
                expected_revision=query.get('revision', [None])[0],
                expected_digest=query.get('digest', [None])[0])


ASSETS = {"/paper-order-contract.mjs": ("paper-order-contract.mjs", "text/javascript; charset=utf-8"),
          "/paper-order-detail.mjs": ("paper-order-detail.mjs", "text/javascript; charset=utf-8"),
          "/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"),
          "/replay.mjs": ("replay.mjs", "text/javascript; charset=utf-8"),
          "/replay.css": ("replay.css", "text/css; charset=utf-8"),
          "/research.html": ("research.html", "text/html; charset=utf-8"),
          "/research.mjs": ("research.mjs", "text/javascript; charset=utf-8"),
          "/research.css": ("research.css", "text/css; charset=utf-8"),
          "/paper-orders.html": ("paper-orders.html", "text/html; charset=utf-8"),
          "/paper-orders.css": ("paper-orders.css", "text/css; charset=utf-8"),
          "/paper-orders.mjs": ("paper-orders.mjs", "text/javascript; charset=utf-8"),
          "/paper-account.html": ("paper-account.html", "text/html; charset=utf-8"),
          "/paper-account.css": ("paper-account.css", "text/css; charset=utf-8"),
          "/paper-account.mjs": ("paper-account.mjs", "text/javascript; charset=utf-8"),
          "/market-data.html": ("market-data.html", "text/html; charset=utf-8"),
          "/market-data.css": ("market-data.css", "text/css; charset=utf-8"),
          "/market-data.mjs": ("market-data.mjs", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    journal_path = None
    replay_path = None
    research_path = None
    paper_session_observer = None
    market_capture_path = None
    server_version = "UULABLocal"
    sys_version = ""

    def parse_request(self):
        if len(self.raw_requestline) > 4096:
            self.command, self.request_version, self.requestline = None, "HTTP/1.0", ""
            self.send_error(414)
            return False
        stream = self.rfile
        self.rfile = HeaderBudgetReader(stream)
        try:
            return super().parse_request()
        finally:
            self.rfile = stream

    def send_error(self, code, message=None, explain=None):
        # Standard parser errors otherwise echo untrusted method/version text.
        self.close_connection = True
        if code == 501:
            return self._deny_write()
        self.respond(code, {"error": "invalid_request"})

    def _allowed_request(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed |= {"127.0.0.1", "localhost"}
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != "http://" + hosts[0].lower()):
            return False
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        return len(sites) <= 1 and (not sites or sites[0] in {"same-origin", "none"})

    def _send(self, status, body, content_type, *, allow=None):
        self.send_response(status)
        for key, value in {"Content-Type": content_type, "Content-Length": str(len(body)),
                           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                           "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                           "Content-Security-Policy": CSP,
                           "Cross-Origin-Resource-Policy": "same-origin"}.items():
            self.send_header(key, value)
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Client left; do not turn this into a server-wide failure.

    def respond(self, status, data, *, allow=None):
        self._send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8", allow=allow)

    def _probe(self, *, readiness):
        report = dict(schema_version=1, source="local_paper_dashboard", mode="paper",
                      read_only=True, live_enabled=False, status="alive",
                      scope="observer_process", observed_at=datetime.now(timezone.utc).isoformat())
        if not readiness:
            return self.respond(200, report)
        sources = {}
        readers = (
            ("journal", self.journal_path is not None, lambda: read_snapshot(self.journal_path)),
            ("replay", self.replay_path is not None,
             lambda: _report_reader("replay_report.py").read_report(self.replay_path)),
            ("research", self.research_path is not None,
             lambda: _report_reader("research_report.py").read_report(self.research_path)),
            ("paper_session", self.paper_session_observer is not None,
             lambda: self.paper_session_observer.read()),
        )
        for name, configured, read in readers:
            if not configured:
                sources[name] = "not_configured"
                continue
            try:
                read()
                sources[name] = "available"
            except Exception:
                # One failed source cannot suppress another source's observation.
                sources[name] = "unavailable"
        configured = [value for value in sources.values() if value != "not_configured"]
        status = ("not_configured" if not configured else
                  "degraded" if "unavailable" in configured else "ready")
        report.update(scope="configured_source_readability", sources=sources, status=status)
        return self.respond(200 if status == "ready" else 503, report)

    def do_GET(self):
        if not self._allowed_request():
            return self.respond(403, {"error": "local_same_origin_required"})
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or len(self.path) > 2048:
                raise ValueError("invalid target")
            # Python 3.10 treats strict parsing of an empty query as an error.
            # Absence of parameters is valid; malformed nonempty queries stay strict.
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else {}
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("duplicate query")
            route = parsed.path
            if route in {"/api/liveness", "/api/readiness"}:
                if query:
                    raise ValueError("probe parameters are not supported")
                return self._probe(readiness=route == "/api/readiness")
            if route == "/api/market-capture":
                if query:
                    raise ValueError('capture parameters are not supported')
                try:
                    report = _market_capture_status(self.market_capture_path)
                except (ValueError, OSError, ImportError):
                    return self.respond(503, {"error": "market_capture_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route in ASSETS:
                if query:
                    raise ValueError("asset parameters not supported")
                filename, content_type = ASSETS[route]
                try:
                    body = Path(__file__).with_name(filename).read_bytes()
                except OSError:
                    return self.respond(503, {"error": "asset_unavailable"})
                return self._send(200, body, content_type)
            if route == "/api/paper-session/order":
                if set(query) != {"client_id", "revision", "digest"}:
                    raise ValueError("detail requires exact id and both preconditions")
                if self.paper_session_observer is None:
                    return self.respond(503, {"error": "paper_session_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                from trading_core.paper_session_observer import (
                    PaperSessionChanged, PaperSessionUnavailable, PaperOrderNotFound)
                try:
                    report = self.paper_session_observer.read_order(query["client_id"][0],
                        expected_revision=query["revision"][0], expected_digest=query["digest"][0])
                except PaperSessionChanged:
                    return self.respond(409, {"error": "paper_session_changed", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                except PaperOrderNotFound:
                    return self.respond(404, {"error": "paper_order_not_found", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                except PaperSessionUnavailable:
                    return self.respond(503, {"error": "paper_session_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route == "/api/paper-session/orders":
                options = _order_page_query(query)
                if self.paper_session_observer is None:
                    return self.respond(503, {"error": "paper_session_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                from trading_core.paper_session_observer import PaperSessionChanged, PaperSessionUnavailable
                try:
                    report = self.paper_session_observer.read_orders(**options)
                except PaperSessionChanged:
                    return self.respond(409, {"error": "paper_session_changed", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                except PaperSessionUnavailable:
                    return self.respond(503, {"error": "paper_session_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route == "/api/paper-session":
                if query:
                    raise ValueError("session parameters are not supported")
                try:
                    if self.paper_session_observer is None:
                        raise ValueError("paper session not configured")
                    report = self.paper_session_observer.read()
                except ValueError:
                    return self.respond(503, {"error": "paper_session_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route == "/api/research":
                if query:
                    raise ValueError("research parameters are not supported")
                try:
                    report = _report_reader('research_report.py').read_report(self.research_path)
                except (ValueError, OSError, ImportError):
                    return self.respond(503, {"error": "research_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route == "/api/replay":
                if query:
                    raise ValueError("replay parameters are not supported")
                try:
                    report = _report_reader('replay_report.py').read_report(self.replay_path)
                except (ValueError, OSError, ImportError):
                    return self.respond(503, {"error": "replay_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                # Verify totals against all fills, but bound the public response.
                report["fills"] = report["fills"][-100:]
                return self.respond(200, report)
            if route not in {"/api/health", "/api/orders", "/api/snapshot", "/api/events"}:
                return self.respond(404, {"error": "not_found"})
            allowed_query = {"client_id"} if route == "/api/events" else ({"limit", "offset", "status"} if route != "/api/health" else set())
            if set(query) - allowed_query:
                raise ValueError("unsupported parameter")
            limit = int(query.get("limit", ["100"])[0])
            offset = int(query.get("offset", ["0"])[0])
            status = query.get("status", [None])[0]
            if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000 or (status is not None and status not in STATUSES):
                raise ValueError("invalid parameters")
            client_id = query.get("client_id", [None])[0]
            if client_id is not None and (not client_id.strip() or len(client_id) > 128):
                raise ValueError("invalid identifier")
            snapshot = read_snapshot(self.journal_path, limit=limit, offset=offset, status=status, event_client_id=client_id)
            if route == "/api/health":
                return self.respond(200, {k: snapshot[k] for k in ("mode", "read_only", "live_enabled", "journal_connected", "source", "schema_version", "as_of")})
            if route == "/api/events":
                return self.respond(200, {k: snapshot[k] for k in ("mode", "source", "as_of", "audit_available", "events")})
            return self.respond(200, snapshot)
        except JournalUnavailable:
            return self.respond(503, {"error": "journal_unavailable", "journal_connected": False,
                                      "mode": "paper", "live_enabled": False, "read_only": True})
        except ValueError:
            return self.respond(400, {"error": "invalid_request"})
        except Exception:
            return self.respond(500, {"error": "internal_error"})

    def do_HEAD(self):
        self.do_GET()

    def _deny_write(self):
        if not self._allowed_request():
            return self.respond(403, {"error": "local_same_origin_required"})
        self.close_connection = True
        self.respond(405, {"error": "read_only"}, allow="GET, HEAD")

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = _deny_write

    def log_message(self, *_):
        # Do not place request/query content (possibly identifiers) in shared logs.
        pass


def create_server(journal_path=None, port=8765, *, replay_path=None, research_path=None,
                  paper_session_path=None, market_capture_path=None, max_connections=8, socket_timeout=2.0,
                  connection_timeout=10.0):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    class ConfiguredHandler(Handler):
        pass
    ConfiguredHandler.journal_path = journal_path
    ConfiguredHandler.replay_path = replay_path
    ConfiguredHandler.research_path = research_path
    ConfiguredHandler.market_capture_path = market_capture_path
    if paper_session_path is not None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/core'))
        from trading_core.paper_session_observer import PaperSessionObserver
        ConfiguredHandler.paper_session_observer = PaperSessionObserver(paper_session_path)
    return BoundedHTTPServer(("127.0.0.1", port), ConfiguredHandler,
                             max_connections=max_connections, socket_timeout=socket_timeout,
                             connection_timeout=connection_timeout)


def serve(journal_path=None, port=8765, *, replay_path=None, research_path=None, paper_session_path=None,
          market_capture_path=None):
    with create_server(journal_path, port, replay_path=replay_path, research_path=research_path,
                       paper_session_path=paper_session_path, market_capture_path=market_capture_path) as server:
        stopped = threading.Event()
        received, failed, previous = [], [], {}

        def on_signal(signum, frame):
            if not received:
                received.append(signum)
            # Event.set takes a non-reentrant lock that this signal may have
            # interrupted inside Event.wait. Only record the request here.

        def run():
            try:
                server.serve_forever()
            except Exception:
                failed.append(True)
            finally:
                stopped.set()

        worker = threading.Thread(target=run, name="uulab-dashboard-accept", daemon=True)
        started = False
        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous[signum] = signal.signal(signum, on_signal)
            worker.start()
            started = True
            print(f"Read-only paper dashboard: http://127.0.0.1:{server.server_port}", flush=True)
            while not received and not stopped.wait(.1):
                pass
        except KeyboardInterrupt:
            received.append(signal.SIGINT)
        finally:
            if started:
                server.shutdown()
                server.server_close()
                worker.join(2)
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        if received:
            return 128 + received[0]
        if failed:
            raise OSError("server_failed")
        return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def cli(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--journal", default=None, help="Existing local paper SQLite journal")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--replay-report", default=None, help="Existing offline replay result, not a checkpoint")
    parser.add_argument("--research-report", default=None, help="Existing offline walk-forward research report")
    parser.add_argument("--paper-session", default=None, help="Existing durable local paper account; read only")
    parser.add_argument("--market-capture", default=None, help="Existing public candle capture; read only")
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print(json.dumps({"error": "invalid_arguments"}))
        return 2
    try:
        return serve(args.journal, args.port, replay_path=args.replay_report,
                     research_path=args.research_report, paper_session_path=args.paper_session,
                     market_capture_path=args.market_capture)
    except Exception:
        print(json.dumps({"error": "startup_failed"}))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
