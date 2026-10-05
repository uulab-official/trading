"""Keep optional coverage delivery separate from required CI verification."""

from pathlib import Path
import unittest

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
        self.assertLess(self.test["steps"].index(self.steps["Upload coverage"]),
                        self.test["steps"].index(warning))

    def test_test_and_coverage_generation_remain_required(self):
        self.assertFalse(self.test.get("continue-on-error", False))
        self.assertEqual(self.test["strategy"]["matrix"]["python-version"],
                         ["3.10", "3.11", "3.12"])
        for step in self.test["steps"]:
            if step.get("name") != "Upload coverage":
                self.assertFalse(step.get("continue-on-error", False))
        self.assertEqual(
            self.steps["Run tests"]["run"].strip(),
            "pytest packages/ -v --cov=trading_core --cov=trading_data "
            "--cov=trading_strategy --cov=trading_backtest --cov-report=xml",
        )

    def test_docker_validation_remains_blocking_and_network_isolated(self):
        docker = self.jobs["docker"]
        self.assertEqual(docker["needs"], ["test", "lint"])
        self.assertFalse(docker.get("continue-on-error", False))
        for step in docker["steps"]:
            self.assertFalse(step.get("continue-on-error", False))
        cli = next(step for step in docker["steps"]
                   if step.get("name") == "Test Rust Docker CLI without network access")
        commands = cli["run"].strip().splitlines()
        self.assertEqual(len(commands), 4)
        self.assertTrue(all("--network none" in command and command.endswith("--help")
                            for command in commands))

    def test_image_publication_stays_explicit_and_depends_on_verification(self):
        publish = self.jobs["build-and-push"]
        self.assertEqual(publish["needs"], ["test", "lint", "docker"])
        self.assertEqual(publish["if"],
                         "github.event_name == 'workflow_dispatch' && inputs.publish_image == true")


if __name__ == "__main__":
    unittest.main()
