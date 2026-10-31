"""Local-only read-only dashboard API backed by the paper SQLite journal.

No trading endpoints, no broker credentials, and no network listener beyond loopback.
"""
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class Handler(BaseHTTPRequestHandler):
    journal_path = None

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/health":
            return self.respond(200, {"mode": "paper", "live_enabled": False, "journal_connected": self.journal_path is not None and Path(self.journal_path).is_file()})
        if route == "/api/orders":
            if self.journal_path is None or not Path(self.journal_path).is_file():
                return self.respond(503, {"error": "journal_unavailable"})
            try:
                with sqlite3.connect(f"file:{Path(self.journal_path).resolve()}?mode=ro", uri=True) as db:
                    db.execute("PRAGMA query_only=ON")
                    rows = db.execute("SELECT client_id,status,reserved,updated_at FROM orders ORDER BY updated_at DESC,client_id LIMIT 100").fetchall()
            except (sqlite3.Error, OSError):
                return self.respond(503, {"error": "journal_unavailable"})
            return self.respond(200, {"mode": "paper", "orders": [{"client_id": row[0], "status": row[1], "reserved": row[2], "updated_at": row[3]} for row in rows]})
        if route == "/" or route == "/index.html":
            body = (Path(__file__).parent / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        return self.respond(404, {"error": "not_found"})

    def respond(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def serve(journal_path=None, port=8765):
    Handler.journal_path = journal_path
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Read-only paper dashboard: http://127.0.0.1:{server.server_port}")
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", default=None)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.journal, args.port)
