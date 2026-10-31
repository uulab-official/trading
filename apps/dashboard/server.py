"""Local read-only paper operations server. Never expose this service publicly.

Host/Origin checks mitigate browser-origin attacks; they are not authentication.
No credential handling, broker connection, order submission or fill simulation.
"""
import argparse
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .store import JournalUnavailable, STATUSES, read_snapshot
else:  # Keep direct script execution and the repository's file-loader tests working.
    _spec = importlib.util.spec_from_file_location("uulab_dashboard_store", Path(__file__).with_name("store.py"))
    _store = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_store)
    JournalUnavailable, STATUSES, read_snapshot = _store.JournalUnavailable, _store.STATUSES, _store.read_snapshot

# Load the dependency-free report reader, not the trading engine or credentials.
_replay_spec = importlib.util.spec_from_file_location(
    "uulab_replay_report", Path(__file__).resolve().parents[1] / "server/trading_bot/replay_report.py")
_replay = importlib.util.module_from_spec(_replay_spec)
_replay_spec.loader.exec_module(_replay)

_research_spec = importlib.util.spec_from_file_location(
    "uulab_research_report", Path(__file__).resolve().parents[1] / "server/trading_bot/research_report.py")
_research = importlib.util.module_from_spec(_research_spec)
_research_spec.loader.exec_module(_research)

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"),
          "/replay.mjs": ("replay.mjs", "text/javascript; charset=utf-8"),
          "/replay.css": ("replay.css", "text/css; charset=utf-8"),
          "/research.html": ("research.html", "text/html; charset=utf-8"),
          "/research.mjs": ("research.mjs", "text/javascript; charset=utf-8"),
          "/research.css": ("research.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")


class Handler(BaseHTTPRequestHandler):
    journal_path = None
    replay_path = None
    research_path = None
    server_version = "UULABLocal"
    sys_version = ""

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
            if route in ASSETS:
                if query:
                    raise ValueError("asset parameters not supported")
                filename, content_type = ASSETS[route]
                try:
                    body = Path(__file__).with_name(filename).read_bytes()
                except OSError:
                    return self.respond(503, {"error": "asset_unavailable"})
                return self._send(200, body, content_type)
            if route == "/api/research":
                if query:
                    raise ValueError("research parameters are not supported")
                try:
                    report = _research.read_report(self.research_path)
                except (ValueError, OSError):
                    return self.respond(503, {"error": "research_unavailable", "mode": "paper",
                                              "live_enabled": False, "read_only": True})
                return self.respond(200, report)
            if route == "/api/replay":
                if query:
                    raise ValueError("replay parameters are not supported")
                try:
                    report = _replay.read_report(self.replay_path)
                except (ValueError, OSError):
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


def create_server(journal_path=None, port=8765, *, replay_path=None, research_path=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid port")
    class ConfiguredHandler(Handler):
        pass
    ConfiguredHandler.journal_path = journal_path
    ConfiguredHandler.replay_path = replay_path
    ConfiguredHandler.research_path = research_path
    return ThreadingHTTPServer(("127.0.0.1", port), ConfiguredHandler)


def serve(journal_path=None, port=8765, *, replay_path=None, research_path=None):
    with create_server(journal_path, port, replay_path=replay_path, research_path=research_path) as server:
        print(f"Read-only paper dashboard: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default=None, help="Existing local paper SQLite journal")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--replay-report", default=None, help="Existing offline replay result, not a checkpoint")
    parser.add_argument("--research-report", default=None, help="Existing offline walk-forward research report")
    args = parser.parse_args()
    serve(args.journal, args.port, replay_path=args.replay_report, research_path=args.research_report)
