"""Probe the real configured sources without executing or mutating them."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_dashboard_runtime import ROOT, load_server, request, running_server
for part in ("apps/server", "packages/core"):
    sys.path.insert(0, str(ROOT / part))
from trading_bot.replay_report import build_report, write_report as write_replay
from trading_bot.research_report import write_report as write_research
from trading_bot.walk_forward_research import run_research
from trading_core.paper_session import PaperTradingSession
from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics
from test_replay_report import result_fixture


class DashboardReadinessTests(unittest.TestCase):
    def setUp(self):
        self.module = load_server()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = self.root / "journal.sqlite"
        with closing(sqlite3.connect(self.journal)) as db, db:
            db.execute("CREATE TABLE orders(client_id TEXT,status TEXT,reserved TEXT,updated_at TEXT)")
            db.execute("INSERT INTO orders VALUES('one','unknown','12','2026-01-01')")
        self.replay = self.root / "replay.json"
        write_replay(self.replay, build_report(result_fixture(), fixture_sha256="a"*64,
            first_bar_ms=0, last_bar_ms=7200000, mark_price="110"))
        self.research = self.root / "research.json"
        write_research(self.research, run_research(dataset(), "a"*64,
            candidates=CANDIDATES, settings=SETTINGS, evaluator=lambda *args: metrics("-2")))
        self.account = self.root / "account.sqlite"
        async def create():
            async with PaperTradingSession(self.account, create=True,
                                           initial_balance="1000", enable_control=True):
                pass
        asyncio.run(create())
        self.options = {"journal": ("journal_path", self.journal),
                        "replay": ("replay_path", self.replay),
                        "research": ("research_path", self.research),
                        "paper_session": ("paper_session_path", self.account)}

    def check_contract(self, data, *, ready=False):
        self.assertEqual(set(data), {"schema_version", "source", "mode", "read_only",
            "live_enabled", "scope", "status", "observed_at"} | ({"sources"} if ready else set()))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["source"], "local_paper_dashboard")
        self.assertEqual(data["mode"], "paper")
        self.assertIs(data["read_only"], True)
        self.assertIs(data["live_enabled"], False)
        self.assertEqual(datetime.fromisoformat(data["observed_at"]).utcoffset(),
                         timezone.utc.utcoffset(None))
        self.assertNotIn(str(self.root), json.dumps(data))

    def test_no_configured_sources_are_not_ready_but_process_is_alive(self):
        with running_server(self.module) as server:
            status, _, raw = request(server, "/api/readiness")
            self.assertEqual(status, 503)
            data = json.loads(raw)
            self.check_contract(data, ready=True)
            self.assertEqual(data["scope"], "configured_source_readability")
            self.assertEqual(data["status"], "not_configured")
            self.assertEqual(data["sources"], {key: "not_configured" for key in self.options})
            status, _, raw = request(server, "/api/liveness")
            self.assertEqual(status, 200)
            self.check_contract(json.loads(raw))
            self.assertEqual(json.loads(raw)["status"], "alive")

    def test_liveness_never_opens_configured_sources(self):
        all_options = dict(self.options.values())
        with running_server(self.module, **all_options) as server:
            class NoRead:
                def read(self):
                    raise AssertionError("liveness attempted account observation")
            server.RequestHandlerClass.paper_session_observer = NoRead()
            with patch.object(self.module, "read_snapshot", side_effect=AssertionError("journal I/O")), \
                 patch.object(self.module, "_report_reader", side_effect=AssertionError("report I/O")):
                status, _, raw = request(server, "/api/liveness")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["scope"], "observer_process")

    def test_every_nonempty_source_combination_uses_its_actual_validators(self):
        before = {path: path.read_bytes() for _, path in self.options.values()}
        names = list(self.options)
        for count in range(1, 5):
            for selected in itertools.combinations(names, count):
                with self.subTest(selected=selected):
                    with running_server(self.module, **dict(self.options[key] for key in selected)) as server:
                        status, _, raw = request(server, "/api/readiness")
                        self.assertEqual(status, 200)
                        data = json.loads(raw)
                        self.check_contract(data, ready=True)
                        self.assertEqual(data["status"], "ready")
                        self.assertEqual(data["sources"],
                            {key: "available" if key in selected else "not_configured" for key in names})
                        if "journal" not in selected:
                            self.assertEqual(request(server, "/api/health")[0], 503)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_one_unavailable_source_keeps_other_sources_visible_then_recovers(self):
        with running_server(self.module, **dict(self.options.values())) as server:
            original = self.research.read_bytes()
            self.assertEqual(request(server, "/api/readiness")[0], 200)
            self.research.write_bytes(b"broken")
            status, _, raw = request(server, "/api/readiness")
            self.assertEqual(status, 503)
            data = json.loads(raw)
            self.assertEqual(data["status"], "degraded")
            self.assertEqual(data["sources"], {"journal": "available", "replay": "available",
                "research": "unavailable", "paper_session": "available"})
            self.assertEqual(request(server, "/api/replay")[0], 200)
            self.research.write_bytes(original)
            self.assertEqual(request(server, "/api/readiness")[0], 200)

    def test_missing_sources_are_not_created_and_error_details_are_private(self):
        for key, (option, _) in self.options.items():
            missing = self.root / (key + "-SECRET.sqlite")
            with self.subTest(key=key), running_server(self.module, **{option: missing}) as server:
                status, _, raw = request(server, "/api/readiness")
                self.assertEqual(status, 503)
                data = json.loads(raw)
                self.assertEqual(data["sources"][key], "unavailable")
                self.assertEqual(data["status"], "degraded")
                self.assertNotIn(b"SECRET", raw)
                self.assertFalse(missing.exists())

    def test_ready_account_can_be_halted_and_does_not_authorize_trading(self):
        with running_server(self.module, paper_session_path=self.account) as server:
            self.assertEqual(request(server, "/api/readiness")[0], 200)
            status, _, raw = request(server, "/api/paper-session")
            self.assertEqual(status, 200)
            observed = json.loads(raw)
            self.assertEqual(observed["control"]["mode"], "halted")
            self.assertEqual(observed["observation"]["process_state"], "unobserved")
            self.assertEqual(observed["observation"]["order_authorization"], "not_assessed")

    def test_ready_checks_observer_replacement_binding(self):
        with running_server(self.module, paper_session_path=self.account) as server:
            self.assertEqual(request(server, "/api/readiness")[0], 200)
            replacement = self.root / "replacement.sqlite"
            replacement.write_bytes(self.account.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(self.account)
            status, _, raw = request(server, "/api/readiness")
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(raw)["sources"]["paper_session"], "unavailable")

    def test_head_queries_and_origin_checks_apply_to_both_probes(self):
        with running_server(self.module, journal_path=self.journal) as server:
            for route in ("/api/liveness", "/api/readiness"):
                with self.subTest(route=route):
                    status, headers, raw = request(server, route, method="HEAD")
                    self.assertEqual(status, 200)
                    self.assertEqual(raw, b"")
                    self.assertGreater(int(headers["Content-Length"]), 0)
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                    for query in ("?path=/private", "?x=1&x=2", "?x"):
                        self.assertEqual(request(server, route + query)[0], 400)
                    for denied in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                                   {"Sec-Fetch-Site": "cross-site"}):
                        self.assertEqual(request(server, route, headers=denied)[0], 403)
                    self.assertEqual(request(server, route, method="POST")[0], 405)

    def test_unexpected_source_error_is_unavailable_without_fake_success(self):
        with running_server(self.module, journal_path=self.journal,
                            replay_path=self.replay) as server:
            with patch.object(self.module, "read_snapshot", side_effect=RuntimeError("SECRET")):
                status, _, raw = request(server, "/api/readiness")
            self.assertEqual(status, 503)
            data = json.loads(raw)
            self.assertEqual(data["sources"]["journal"], "unavailable")
            self.assertEqual(data["sources"]["replay"], "available")
            self.assertNotIn(b"SECRET", raw)


if __name__ == "__main__":
    unittest.main()
