"""Inert, fail-closed systemd unit rendering for PAPER monitor tick."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "paper_monitor_systemd.py"
spec = importlib.util.spec_from_file_location("paper_monitor_systemd", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

VALID = [
    "--python", "/usr/bin/python3",
    "--tool", "/opt/paper/tools/paper_monitor_tick.py",
    "--service", "/var/lib/paper/private/service",
    "--capture", "/var/lib/paper/private/capture",
    "--account", "/var/lib/paper/private/account",
    "--directory", "/var/lib/paper/private/history",
]


class UnitTests(unittest.TestCase):
    def test_inert_render(self):
        doc = module.render(module._args(VALID))
        self.assertEqual(doc["status"], "rendered_only")
        self.assertEqual(doc["exit_code"], 0)
        self.assertFalse(doc["boundaries"]["activated"])
        self.assertFalse(doc["boundaries"]["installed"])
        self.assertFalse(doc["boundaries"]["production_ready"])
        self.assertEqual(set(doc["files"]), {"paper-monitor.timer", "paper-monitor.service"})

    def test_oneshot_hardening_and_explicit_tick(self):
        service = module.render(module._args(VALID))["files"]["paper-monitor.service"]
        for value in ["Type=oneshot", "NoNewPrivileges=yes", "ProtectSystem=strict",
                      "ReadWritePaths=/var/lib/paper/private/history",
                      "RestrictAddressFamilies=AF_UNIX", "MemoryMax=256M",
                      "TimeoutStartSec=40s", "/opt/paper/tools/paper_monitor_tick.py"]:
            self.assertIn(value, service)
        self.assertNotIn("ExecStart=/bin/sh", service)
        self.assertNotIn("Restart=", service)

    def test_timer_no_catchup_and_no_activation(self):
        timer = module.render(module._args(VALID))["files"]["paper-monitor.timer"]
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_no_files_or_process_side_effect(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT), *VALID],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "rendered_only")

    def test_reject_injected_path(self):
        for bad in ("/var/lib/my folder", "/tmp/%i", "/tmp/x\nExecStart=evil",
                    "/tmp/../etc", "/tmp/x;touch", "relative", "/tmp/x\\y"):
            with self.subTest(path=bad), self.assertRaises(module.Refused):
                module._args([*VALID[:-1], bad])

    def test_reject_other_tool(self):
        wrong = list(VALID)
        wrong[3] = "/opt/paper/tools/paper_stream.py"
        with self.assertRaises(module.Refused):
            module._args(wrong)

    def test_reject_name_and_time_bounds(self):
        cases = [(["--unit-name", "foo@bar"]), (["--interval-seconds", "15"]),
                 (["--interval-seconds", "30", "--timeout-seconds", "10"]),
                 (["--interval-seconds", "3601"]),
                 (["--timeout-seconds", "31"])]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(module.Refused):
                module._args(VALID + extra)

    def test_custom_valid_name(self):
        doc = module.render(module._args(VALID + ["--unit-name", "paper-watch"]))
        self.assertEqual(set(doc["files"]), {"paper-watch.timer", "paper-watch.service"})
        self.assertIn("Unit=paper-watch.service", doc["files"]["paper-watch.timer"])

    def test_invalid_cli_has_one_json_response(self):
        out = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT)],
                             capture_output=True, text=True, timeout=8)
        self.assertEqual(out.returncode, 2)
        self.assertEqual(json.loads(out.stdout)["files"], {})
        self.assertFalse(json.loads(out.stdout)["boundaries"]["activated"])


if __name__ == "__main__":
    unittest.main()
