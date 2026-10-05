"""Real shell-launcher contracts; every Python child forbids network and DNS."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class OfflineTrialLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="offline trial ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.repo = self.workspace / "repository with spaces"
        self.repo.mkdir()
        for package in ("packages/core", "packages/data", "packages/strategy", "apps/server"):
            shutil.copytree(ROOT / package, self.repo / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
        (self.repo / "config/examples").mkdir(parents=True)
        shutil.copy2(ROOT / "config/examples/offline_fixture.json", self.repo / "config/examples/offline_fixture.json")
        shutil.copy2(ROOT / "test-trading.sh", self.repo / "test-trading.sh")
        self.cwd = self.workspace / "caller with spaces"
        self.cwd.mkdir()
        self.bin = self.workspace / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.guard = self.workspace / "python guard"
        self.guard.mkdir()
        self.configure_guard()
        self.forbidden = self.workspace / "forbidden.txt"
        for name in ("docker", "docker-compose", "curl", "wget", "pip", "pip3"):
            executable = self.bin / name
            executable.write_text("#!/bin/sh\nprintf '%s\\n' '" + name + "' >> '" + str(self.forbidden) + "'\nexit 99\n")
            executable.chmod(0o755)
        self.env = {"PATH": str(self.bin) + os.pathsep + os.defpath,
                    "PYTHONPATH": str(self.guard)}

    def configure_guard(self, extra=""):
        (self.guard / "sitecustomize.py").write_text(
            "from unittest.mock import patch\n"
            "for target in ('socket.socket.connect','socket.create_connection','socket.getaddrinfo'):\n"
            " patch(target,side_effect=AssertionError('Offline launcher forbids network')).start()\n" + extra)

    def launch(self, *args):
        result = subprocess.run(["/bin/bash", str(self.repo / "test-trading.sh"), *map(str, args)],
                                cwd=self.cwd, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertFalse(self.forbidden.exists(), "The launcher cannot start Docker, downloads, or installers")
        return result

    def test_default_launch_works_from_other_directory_and_preserves_resume(self):
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual((payload["mode"], payload["status"], payload["bars_processed"]),
                         ("paper", "fixture_complete", 90))
        self.assertEqual(len(payload["trades"]), 2)
        self.assertIn("synthetic", first.stderr.lower())
        self.assertIn("offline", first.stderr.lower())
        checkpoint = self.repo / "data/owner-trial/checkpoint.json"
        before = checkpoint.read_bytes()
        resumed = self.launch()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout), payload)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.assertFalse((self.cwd / "data").exists())

    def test_caller_relative_fixture_and_checkpoint_paths_with_spaces(self):
        fixture = self.cwd / "local data.json"
        shutil.copy2(self.repo / "config/examples/offline_fixture.json", fixture)
        result = self.launch("--fixture", fixture.name, "--checkpoint", "trial output/paper state.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["bars_processed"], 90)
        self.assertTrue((self.cwd / "trial output/paper state.json").is_file())
        self.assertFalse((self.repo / "trial output").exists())

    def test_live_fixture_and_live_flags_are_refused_without_checkpoint(self):
        fixture = self.cwd / "live.json"
        fixture.write_text(json.dumps({"mode": "live", "candles": []}))
        for args in (("--fixture", str(fixture)), ("--mode", "live"), ("--enable-live-trading",)):
            with self.subTest(args=args):
                result = self.launch(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((self.repo / "data/owner-trial/checkpoint.json").exists())

    def test_equals_form_paths_use_the_caller_directory(self):
        fixture = self.cwd / "local data.json"
        shutil.copy2(self.repo / "config/examples/offline_fixture.json", fixture)
        result = self.launch("--fixture=" + fixture.name, "--checkpoint=trial output/equals.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["bars_processed"], 90)
        self.assertTrue((self.cwd / "trial output/equals.json").is_file())
        self.assertFalse((self.repo / "trial output").exists())

    def test_equals_checkpoint_cannot_silently_switch_to_repository_directory(self):
        result = self.launch("--checkpoint=caller paper.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.cwd / "caller paper.json").is_file())
        self.assertFalse((self.repo / "caller paper.json").exists())

    def test_missing_yaml_reports_small_setup_without_installing(self):
        self.configure_guard("import builtins\noriginal_import=builtins.__import__\n"
                             "def blocked_import(name,*args,**kwargs):\n"
                             " if name=='yaml': raise ModuleNotFoundError('missing test dependency')\n"
                             " return original_import(name,*args,**kwargs)\n"
                             "builtins.__import__=blocked_import\n")
        result = self.launch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("PyYAML", result.stderr)
        self.assertIn("requirements-offline.txt", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.repo / "data").exists())

    def test_old_python_reports_runtime_requirement_before_replay(self):
        self.configure_guard("import sys\nsys.version_info=(3,9,0)\n")
        result = self.launch()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Python 3.10", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.repo / "data").exists())

    def test_checkpoint_permission_error_has_cli_exit_without_saved_state(self):
        checkpoint = self.cwd / "cannot save/paper.json"
        lock = checkpoint.parent / ".paper.json.lock"
        self.configure_guard("import os\noriginal_open=os.open\n"
                             "def denied_open(path,*args,**kwargs):\n"
                             " if str(path)==" + repr(str(lock)) + ": raise PermissionError('simulated checkpoint permission denial')\n"
                             " return original_open(path,*args,**kwargs)\n"
                             "os.open=denied_open\n")
        result = self.launch("--checkpoint", checkpoint)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("permissions", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(checkpoint.exists())
        self.assertNotIn("Traceback", result.stderr)

    def test_help_and_missing_path_option_do_not_create_state(self):
        help_result = self.launch("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--fixture", help_result.stdout)
        self.assertIn("--checkpoint", help_result.stdout)
        self.assertIn("synthetic", help_result.stdout.lower())
        for option in ("--fixture", "--checkpoint"):
            with self.subTest(option=option):
                result = self.launch(option)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse((self.repo / "data").exists())

    def test_launcher_has_no_legacy_deployment_or_key_setup(self):
        source = (self.repo / "test-trading.sh").read_text()
        self.assertNotIn("docker-compose", source)
        self.assertNotIn("api_key", source)
        self.assertNotIn("api_secret", source)
        self.assertNotIn("config/secrets", source)


if __name__ == "__main__":
    unittest.main()
