"""Offline orchestration tests; no exchange request or real executable."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools import binance_paper_forward_supervisor as supervisor
from tools import binance_public_archive as archive


class ForwardSupervisorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        directory = root / "session"
        directory.mkdir()
        archive_path = root / "archive.sqlite"
        archive_path.touch()
        binary = root / "paper-binary"
        binary.touch()
        binary.chmod(0o700)
        template = root / "template.json"
        template.write_text(
            json.dumps({"version": 1, "timeframe": "1m", "strategies": [{"id": "ma"}], "bars": []}),
            encoding="utf-8",
        )
        self.session = supervisor.make_session(archive_path, directory, binary, template, 60_000, 2)
        self.export_calls = 0
        self.run_calls = 0
        self.envelope = {
            "version": 1,
            "source": archive.SOURCE,
            "source_id": "a" * 64,
            "archive_head_sha256": "b" * 64,
            "scenario": {"strategies": [{"id": "ma"}], "bars": [{}, {}]},
        }

    def clock(self):
        return 120_000

    def export(self, *args, **kwargs):
        self.export_calls += 1
        self.expected_prefix = kwargs.get("expected_prefix")
        return self.envelope

    def run_binary(self, session, envelope, initialize):
        self.run_calls += 1
        self.assertEqual(session, self.session)
        self.trusted_checkpoints = envelope.get("trusted_checkpoints")
        return {
            "mode": "offline_paper_forward",
            "live_orders_enabled": False,
            "source": envelope["source"],
            "source_id": envelope["source_id"],
            "archive_head_sha256": envelope["archive_head_sha256"],
            "input_bar_count": len(envelope["scenario"]["bars"]),
            "session_sha256": "c" * 64,
            "replay": {"results": [{"id": "ma"}]},
            "accounts": [
                {
                    "strategy_id": "ma",
                    "checkpoint": {
                        "ledger_id": f"forward-{'c' * 64}-ma",
                        "head": {"sequence": 5, "hash": "d" * 64},
                    },
                    "remaining_event_capacity": 2043,
                }
            ],
        }

    def cycle(self, *, exporter=None, runner=None, initialize=False):
        with supervisor.owner(self.session):
            return supervisor.cycle(
                self.session,
                exporter=exporter or self.export,
                runner=runner or self.run_binary,
                clock=self.clock,
                initialize=initialize,
            )

    def test_success_and_restart_preserve_verified_report(self):
        first = self.cycle(initialize=True)
        self.assertEqual(first["status"], "paper_current")
        self.assertFalse(first["live_orders_enabled"])
        self.assertEqual(supervisor.read_state(self.session), first)
        self.envelope["archive_head_sha256"] = "e" * 64
        second = self.cycle()
        self.assertEqual(second["status"], "paper_current")
        self.assertEqual(second["last_report"]["archive_head_sha256"], "e" * 64)
        self.assertEqual((self.export_calls, self.run_calls), (2, 2))
        self.assertEqual(self.expected_prefix, (120_000, "b" * 64))
        self.assertEqual(
            self.trusted_checkpoints, [first["last_report"]["accounts"][0]["checkpoint"]]
        )

    def test_stale_archive_failure_budget_survives_restart_without_running_binary(self):
        def stale(*args, **kwargs):
            raise archive.ArchiveError("capture_not_current")

        self.cycle()
        first = self.cycle(exporter=stale)
        self.assertEqual(first["status"], "degraded")
        self.assertEqual(first["consecutive_failures"], 1)
        second = self.cycle(exporter=stale)
        self.assertEqual(second["status"], "halted")
        self.assertEqual(second["consecutive_failures"], 2)
        self.assertEqual(second["last_report"], first["last_report"])
        self.assertEqual(self.cycle(), second)
        self.assertEqual((self.export_calls, self.run_calls), (1, 1))

    def test_stop_before_capture_and_during_execution_is_sticky(self):
        self.session.stop_path.touch()
        stopped = self.cycle()
        self.assertEqual(stopped["status"], "halted")
        self.assertEqual(stopped["reason"], "operator_stop")
        self.assertEqual((self.export_calls, self.run_calls), (0, 0))
        self.session.stop_path.unlink()
        self.assertEqual(self.cycle(), stopped)

    def test_stop_during_invocation_preserves_final_report_but_halts(self):
        def stop(session, envelope, initialize):
            report = self.run_binary(session, envelope, initialize)
            session.stop_path.touch()
            return report

        result = self.cycle(runner=stop)
        self.assertEqual(result["status"], "halted")
        self.assertIsNotNone(result["last_report"])
        self.session.stop_path.unlink()
        self.assertEqual(self.cycle(), result)
        self.assertEqual(self.run_calls, 1)

    def test_wrong_or_live_report_is_fatal_and_never_replaces_last_good_report(self):
        good = self.cycle()

        def wrong(session, envelope, initialize):
            return {**self.run_binary(session, envelope, initialize), "live_orders_enabled": True}

        result = self.cycle(runner=wrong)
        self.assertEqual(result["status"], "halted")
        self.assertEqual(result["last_report"], good["last_report"])
        self.assertEqual(result["reason"], "invalid_paper_report_provenance")

    def test_owner_lock_and_malformed_state_refuse_before_export(self):
        with supervisor.owner(self.session):
            with self.assertRaises(supervisor.SupervisorError):
                with supervisor.owner(self.session):
                    self.fail("duplicate session owner")
        self.session.state_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(supervisor.SupervisorError):
            self.cycle()
        self.assertEqual((self.export_calls, self.run_calls), (0, 0))
        self.assertEqual(self.session.state_path.read_text(encoding="utf-8"), "{}")

    def test_subprocess_uses_fixed_paper_command_no_shell_or_inherited_environment(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command[:2], [str(self.session.binary), "paper-forward"])
            self.assertEqual(command[-1], "--initialize")
            self.assertEqual(kwargs["env"], {})
            self.assertEqual(kwargs["timeout"], 180)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(
                json.loads(Path(command[3]).read_text(encoding="ascii")), self.envelope
            )
            result = self.run_binary(self.session, self.envelope, True)
            kwargs["stdout"].write(json.dumps(result).encode("ascii"))
            return type("Completed", (), {"returncode": 0})()

        with patch.object(supervisor.subprocess, "run", side_effect=fake_run):
            report = supervisor.invoke_paper_binary(self.session, self.envelope, True)
        self.assertFalse(report["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
