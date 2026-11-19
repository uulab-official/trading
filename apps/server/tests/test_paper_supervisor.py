"""Paper supervisor safety without sockets, accounts, or external brokers."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.paper_supervisor import run_supervised_once, run_loop


def success_cycle(*args, **kwargs):
    return {"mode": "paper", "live_orders_enabled": False,
            "processed_bars": 90, "appended_bars": 0,
            "daily_loss_halted": False, "drawdown_halted": False}


class PaperSupervisorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.archive = root / "candles.json"
        self.checkpoint = root / "ledger.json"
        self.heartbeat = root / "health.json"
        self.kwargs = {"archive": self.archive, "checkpoint": self.checkpoint,
                       "heartbeat": self.heartbeat, "market": "KRW-BTC",
                       "unit": 60, "count": 200, "balance": 1000000}

    def test_once_emits_local_health_and_never_calls_real_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")):
            status = run_supervised_once(**self.kwargs, cycle=success_cycle,
                                         now=lambda: 1700000000)
        self.assertEqual("healthy", status["status"])
        self.assertFalse(status["live_orders_enabled"])
        self.assertEqual(json.loads(self.heartbeat.read_text()), status)
        self.assertFalse(self.checkpoint.exists())

    def test_failure_marks_health_degraded_without_leaking_error_detail(self):
        def failing(*args, **kwargs):
            raise ValueError("SECRET_FAKE_TOKEN_DO_NOT_PERSIST")
        with self.assertRaises(ValueError):
            run_supervised_once(**self.kwargs, cycle=failing,
                                now=lambda: 1700000000)
        health = json.loads(self.heartbeat.read_text())
        self.assertEqual("degraded", health["status"])
        self.assertEqual("ValueError", health["last_error_type"])
        self.assertNotIn("SECRET_FAKE_TOKEN", self.heartbeat.read_text())

    def test_repeated_failure_halts_instead_of_endless_retries(self):
        sleeps = []
        def failing(*args, **kwargs):
            raise RuntimeError("synthetic outage")
        code = run_loop(**self.kwargs, cycle=failing, now=lambda: 1700000000,
                        interval_seconds=3600, max_failures=2,
                        sleep=lambda value: sleeps.append(value))
        self.assertEqual(2, code)
        self.assertEqual([3600], sleeps)
        self.assertEqual("halted", json.loads(self.heartbeat.read_text())["status"])

    def test_single_failure_recovers_on_next_complete_cycle(self):
        attempts = [0]
        def intermittent(*args, **kwargs):
            attempts[0] += 1
            if attempts[0] == 1:
                raise ValueError("temporary")
            return success_cycle()
        self.assertEqual(0, run_loop(**self.kwargs, cycle=intermittent,
                                      now=lambda: 1700000000,
                                      interval_seconds=3600,
                                      sleep=lambda value: None,
                                      should_stop=lambda: attempts[0] >= 2))
        self.assertEqual("healthy", json.loads(self.heartbeat.read_text())["status"])

    def test_cli_exits_nonzero_on_public_network_failure(self):
        from tools.paper_supervisor import main
        args = ["--mode", "paper", "--once", "--archive", str(self.archive),
                "--checkpoint", str(self.checkpoint),
                "--heartbeat", str(self.heartbeat)]
        with patch("tools.paper_supervisor.run_supervised_once",
                   side_effect=RuntimeError("simulated network failure")):
            self.assertEqual(2, main(args))

    def test_operator_stop_file_prevents_paper_cycle_and_halts_loop(self):
        from tools.paper_supervisor import PaperEmergencyStop
        stop = self.checkpoint.with_name(self.checkpoint.name + ".STOP")
        stop.write_text("operator requested stop")
        touched = []
        def should_never_run(*args, **kwargs):
            touched.append(True)
            return success_cycle()
        with self.assertRaises(PaperEmergencyStop):
            run_supervised_once(**self.kwargs, cycle=should_never_run,
                                kill_switch=stop, now=lambda: 1700000000)
        self.assertEqual([], touched)
        from tools.paper_supervisor import main
        self.assertEqual(3, main(["--mode", "paper", "--once",
                                   "--archive", str(self.archive),
                                   "--checkpoint", str(self.checkpoint),
                                   "--heartbeat", str(self.heartbeat),
                                   "--kill-switch", str(stop)]))
        self.assertEqual(3, run_loop(**self.kwargs, cycle=should_never_run,
                                     kill_switch=stop, now=lambda: 1700000000,
                                     interval_seconds=3600))
        self.assertEqual([], touched)
        report = json.loads(self.heartbeat.read_text())
        self.assertEqual("halted", report["status"])
        self.assertTrue(report["operator_stop"])

    def test_second_supervisor_refused_even_during_idle_period(self):
        from trading_bot.paper_checkpoint import locked_checkpoint, CheckpointInUseError
        owner = self.checkpoint.with_name(self.checkpoint.name + ".supervisor")
        with locked_checkpoint(owner):
            with self.assertRaises(CheckpointInUseError):
                run_loop(**self.kwargs, cycle=success_cycle,
                         interval_seconds=3600, should_stop=lambda: False,
                         sleep=lambda value: None)
        self.assertFalse(self.heartbeat.exists())

    def test_supervisor_lock_released_after_normal_stop(self):
        from trading_bot.paper_checkpoint import locked_checkpoint
        owner = self.checkpoint.with_name(self.checkpoint.name + ".supervisor")
        calls = []
        def one_cycle(*args, **kwargs):
            calls.append(1)
            return success_cycle()
        self.assertEqual(0, run_loop(**self.kwargs, cycle=one_cycle,
                                      now=lambda: 1700000000,
                                      interval_seconds=3600,
                                      should_stop=lambda: bool(calls)))
        with locked_checkpoint(owner):
            pass
        self.assertEqual([1], calls)

    def test_refuses_colliding_paths_and_oversampling(self):
        with self.assertRaises(ValueError):
            run_supervised_once(**dict(self.kwargs, heartbeat=self.checkpoint),
                                cycle=success_cycle, now=lambda: 1700000000)
        with self.assertRaises(ValueError):
            run_loop(**self.kwargs, cycle=success_cycle, interval_seconds=10,
                     should_stop=lambda: True)


if __name__ == "__main__":
    unittest.main()
