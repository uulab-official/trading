"""Keep optional coverage delivery separate from required CI verification."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"


class CoverageWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.jobs = self.workflow["jobs"]
        self.test = self.jobs["test"]
        self.steps = {step.get("name"): step for step in self.test["steps"]}

    def test_upload_process_failure_is_non_blocking(self):
        upload = self.steps["Upload coverage"]
        self.assertIs(upload.get("continue-on-error"), True)
        self.assertEqual(upload["id"], "coverage_upload")
        self.assertEqual(upload["uses"], "codecov/codecov-action@v3")
        self.assertIs(upload["with"]["fail_ci_if_error"], False)
        self.assertEqual(upload["with"]["file"], "./coverage.xml")

    def test_upload_failure_remains_visible_as_warning(self):
        warning = self.steps.get("Warn when coverage upload fails")
        self.assertIsNotNone(warning)
        self.assertEqual(warning["if"], "steps.coverage_upload.outcome == 'failure'")
        self.assertIn("::warning::", warning["run"])
        self.assertIn("Codecov", warning["run"])
        self.assertLess(
            self.test["steps"].index(self.steps["Upload coverage"]),
            self.test["steps"].index(warning),
        )

    def test_test_and_coverage_generation_remain_required(self):
        self.assertFalse(self.test.get("continue-on-error", False))
        self.assertEqual(
            self.test["strategy"]["matrix"]["python-version"], ["3.10", "3.11", "3.12"]
        )
        for step in self.test["steps"]:
            if step.get("name") != "Upload coverage":
                self.assertFalse(step.get("continue-on-error", False))
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        with tempfile.TemporaryDirectory() as directory:
            recorded = Path(directory) / "pytest-args.json"
            fake_pytest = Path(directory) / "pytest"
            fake_pytest.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['CI_TEST_ARGS_PATH'], 'w', encoding='utf-8') as output:\n"
                "    json.dump(sys.argv[1:], output)\n"
                "sys.exit(23)\n",
                encoding="utf-8",
            )
            fake_pytest.chmod(0o700)
            result = subprocess.run(
                [bash, "--noprofile", "--norc", "-e", "-c", self.steps["Run tests"]["run"]],
                # Only the fake pytest is on PATH, so this contract test
                # cannot accidentally launch the real suite recursively.
                env={**os.environ, "PATH": directory, "CI_TEST_ARGS_PATH": str(recorded)},
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(23, result.returncode, result.stdout + result.stderr)
            arguments = json.loads(recorded.read_text(encoding="utf-8"))

        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("paths", nargs="*")
        parser.add_argument("-v", "--verbose", action="count")
        parser.add_argument("--cov", action="append", default=[])
        parser.add_argument("--cov-report", action="append", default=[])
        configured = parser.parse_intermixed_args(arguments)
        self.assertTrue(
            {"packages", "apps/server/tests"}.issubset(
                {Path(path).as_posix() for path in configured.paths}
            )
        )
        self.assertTrue(
            {"trading_core", "trading_data", "trading_strategy", "trading_backtest"}.issubset(
                configured.cov
            )
        )
        self.assertIn("xml", configured.cov_report)

    def test_docker_validation_remains_blocking_and_network_isolated(self):
        docker = self.jobs["docker"]
        self.assertEqual(docker["needs"], ["test", "lint"])
        self.assertFalse(docker.get("continue-on-error", False))
        for step in docker["steps"]:
            self.assertFalse(step.get("continue-on-error", False))
        cli = next(
            step
            for step in docker["steps"]
            if step.get("name") == "Test Rust Docker CLI without network access"
        )
        commands = cli["run"].strip().splitlines()
        expected_commands = [
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "trading-bot:latest",
                "./trading-server",
                *arguments,
                "--help",
            ]
            for arguments in ([], ["master"], ["worker"], ["deploy"], ["migrate"])
        ]
        self.assertEqual([shlex.split(command) for command in commands], expected_commands)

    def test_image_publication_stays_explicit_and_depends_on_verification(self):
        publish = self.jobs["build-and-push"]
        self.assertEqual(publish["needs"], ["test", "lint", "docker"])
        self.assertEqual(
            publish["if"],
            "github.event_name == 'workflow_dispatch' && inputs.publish_image == true",
        )

    def test_lint_failures_propagate_and_check_all_source_roots(self):
        lint = self.jobs["lint"]
        self.assertFalse(lint.get("continue-on-error", False))
        steps = {step.get("name"): step for step in lint["steps"]}
        checks = (
            ("Run flake8", "flake8", "--select=E9,F63,F7,F82"),
            ("Run black check", "black", "--check"),
            ("Run isort check", "isort", "--check-only"),
        )
        bash = shutil.which("bash")
        self.assertIsNotNone(bash)
        for name, command, required_option in checks:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                step = steps[name]
                self.assertFalse(step.get("continue-on-error", False))
                recorded = Path(directory) / "lint-args.json"
                stub = Path(directory) / command
                stub.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, sys\n"
                    "with open(os.environ['CI_LINT_ARGS_PATH'], 'w', encoding='utf-8') as output:\n"
                    "    json.dump(sys.argv[1:], output)\n"
                    "sys.exit(23)\n",
                    encoding="utf-8",
                )
                stub.chmod(0o700)
                result = subprocess.run(
                    [bash, "--noprofile", "--norc", "-e", "-c", step["run"]],
                    env={**os.environ, "PATH": directory, "CI_LINT_ARGS_PATH": str(recorded)},
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(23, result.returncode, result.stdout + result.stderr)
                arguments = json.loads(recorded.read_text(encoding="utf-8"))
                self.assertIn(required_option, arguments)
                self.assertTrue(
                    {"packages", "apps", "tools"}.issubset(
                        {Path(argument).as_posix() for argument in arguments}
                    ),
                    shlex.join(arguments),
                )


if __name__ == "__main__":
    unittest.main()
