"""Offline broker configuration checks never authenticate or expose values."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/broker_readiness.py"
sys.path.insert(0, str(ROOT))

REQUIREMENTS = {
    "toss_market_data": ["TOSS_ACCESS_TOKEN"],
    "toss_token_issuance": ["TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"],
    "kis_vts_quote": [
        "KIS_VTS_ACCESS_TOKEN", "KIS_VTS_APPKEY", "KIS_VTS_APPSECRET",
    ],
    "kis_vts_order_inquiry": [
        "KIS_VTS_ACCESS_TOKEN", "KIS_VTS_APPKEY", "KIS_VTS_APPSECRET",
        "KIS_VTS_CANO", "KIS_VTS_ACNT_PRDT_CD",
    ],
}
VARIABLES = (
    "TOSS_ACCESS_TOKEN", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
    "KIS_VTS_ACCESS_TOKEN", "KIS_VTS_APPKEY", "KIS_VTS_APPSECRET",
    "KIS_VTS_CANO", "KIS_VTS_ACNT_PRDT_CD",
)
SECRET = "SYNTHETIC-SECRET-MUST-NEVER-APPEAR"
UNRELATED = "UNRELATED_PRIVATE_ENVIRONMENT_NAME"


class StrictEnvironment:
    """A mapping whose truthiness and enumeration must never be consulted."""

    def __init__(self, values):
        self.values = values
        self.lookups = []

    def get(self, name):
        if name not in VARIABLES:
            raise AssertionError(UNRELATED)
        self.lookups.append(name)
        return self.values.get(name)

    def __bool__(self):
        raise AssertionError("Do not substitute the process environment")

    def __iter__(self):
        raise AssertionError("Do not enumerate environment names")


class OpaqueValue:
    def __str__(self):
        raise AssertionError(SECRET)

    __repr__ = __str__
    __bool__ = __str__


class BrokerReadinessTests(unittest.TestCase):
    def setUp(self):
        try:
            self.readiness = importlib.import_module("tools.broker_readiness")
        except ModuleNotFoundError as error:
            if error.name != "tools.broker_readiness":
                raise
            self.fail("The offline broker configuration diagnostic is not implemented")

    def assert_safe_report(self, report):
        self.assertEqual(set(report), {
            "schema_version", "mode", "live_orders_enabled", "safe_to_submit",
            "actual_broker_requests", "capabilities",
        })
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["mode"], "paper")
        self.assertIs(report["live_orders_enabled"], False)
        self.assertIs(report["safe_to_submit"], False)
        self.assertEqual(report["actual_broker_requests"], 0)
        self.assertEqual([item["id"] for item in report["capabilities"]],
                         list(REQUIREMENTS))
        for item in report["capabilities"]:
            expected_fields = {
                "id", "required_variables", "missing_variables", "variable_presence",
                "configuration_present", "authenticated",
            }
            if item["id"] == "toss_token_issuance":
                expected_fields.add("requires_explicit_opt_in")
                self.assertIs(item["requires_explicit_opt_in"], True)
            self.assertEqual(set(item), expected_fields)
            self.assertEqual(item["required_variables"], REQUIREMENTS[item["id"]])
            self.assertEqual(set(item["variable_presence"]),
                             set(REQUIREMENTS[item["id"]]))
            self.assertIs(item["authenticated"], False)
            self.assertIs(type(item["configuration_present"]), bool)
            for present in item["variable_presence"].values():
                self.assertIs(type(present), bool)
        self.assertEqual(json.loads(json.dumps(report, allow_nan=False)), report)

    def test_empty_environment_lists_exact_missing_configuration(self):
        report = self.readiness.inspect_environment({})
        self.assert_safe_report(report)
        for item in report["capabilities"]:
            self.assertEqual(item["missing_variables"], REQUIREMENTS[item["id"]])
            self.assertFalse(item["configuration_present"])
            self.assertFalse(any(item["variable_presence"].values()))

    def test_existing_toss_token_does_not_require_client_credentials(self):
        report = self.readiness.inspect_environment({"TOSS_ACCESS_TOKEN": SECRET})
        market, issuance, quote, inquiry = report["capabilities"]
        self.assertTrue(market["configuration_present"])
        self.assertEqual(market["missing_variables"], [])
        self.assertFalse(issuance["configuration_present"])
        self.assertTrue(issuance["requires_explicit_opt_in"])
        self.assertFalse(quote["configuration_present"])
        self.assertFalse(inquiry["configuration_present"])

    def test_client_credentials_only_prepare_explicit_token_issuance(self):
        report = self.readiness.inspect_environment({
            "TOSS_CLIENT_ID": SECRET, "TOSS_CLIENT_SECRET": SECRET,
        })
        market, issuance = report["capabilities"][:2]
        self.assertFalse(market["configuration_present"])
        self.assertEqual(market["missing_variables"], ["TOSS_ACCESS_TOKEN"])
        self.assertTrue(issuance["configuration_present"])
        self.assertTrue(issuance["requires_explicit_opt_in"])
        self.assert_safe_report(report)

    def test_quote_configuration_does_not_establish_account_inquiry(self):
        report = self.readiness.inspect_environment({
            "KIS_VTS_ACCESS_TOKEN": SECRET,
            "KIS_VTS_APPKEY": SECRET,
            "KIS_VTS_APPSECRET": SECRET,
        })
        quote, inquiry = report["capabilities"][2:]
        self.assertTrue(quote["configuration_present"])
        self.assertFalse(inquiry["configuration_present"])
        self.assertEqual(inquiry["missing_variables"],
                         ["KIS_VTS_CANO", "KIS_VTS_ACNT_PRDT_CD"])

    def test_present_values_never_mean_authenticated_or_safe_to_submit(self):
        values = {name: SECRET + "-" + str(index)
                  for index, name in enumerate(VARIABLES)}
        values["KIS_VTS_CANO"] = "98765432"
        values["KIS_VTS_ACNT_PRDT_CD"] = "PRIVATE-ACCOUNT-PRODUCT"
        values[UNRELATED] = "UNRELATED-PRIVATE-VALUE"
        report = self.readiness.inspect_environment(values)
        self.assert_safe_report(report)
        self.assertTrue(all(item["configuration_present"]
                            for item in report["capabilities"]))
        self.assertTrue(all(item["missing_variables"] == []
                            for item in report["capabilities"]))
        for output in (json.dumps(report), self.readiness.render_text(report)):
            for forbidden in (SECRET, "98765432", "PRIVATE-ACCOUNT-PRODUCT",
                              UNRELATED, "UNRELATED-PRIVATE-VALUE"):
                self.assertNotIn(forbidden, output)

    def test_only_nonblank_strings_count_without_authenticity_validation(self):
        for value, expected in (
            (None, False), (False, False), (0, False), (b"token", False),
            ([], False), ({}, False), (OpaqueValue(), False),
            ("", False), (" \t\r\n\v\f\u2003\u00a0", False),
            ("\x1c\x1d\x1e\x1f", False),
            (" token ", True), ("not-a-valid-token", True),
            ("\x00", True), ("\r\n" + SECRET + "\x00\x1b", True),
        ):
            report = self.readiness.inspect_environment({"TOSS_ACCESS_TOKEN": value})
            item = report["capabilities"][0]
            self.assertIs(item["configuration_present"], expected)
            self.assertIs(item["variable_presence"]["TOSS_ACCESS_TOKEN"], expected)
            self.assertNotIn(SECRET, json.dumps(report))

    def test_string_subclass_cannot_execute_custom_strip_or_truthiness(self):
        class UntrustedString(str):
            def strip(self):
                raise AssertionError(SECRET)

            def __bool__(self):
                raise AssertionError(SECRET)

        report = self.readiness.inspect_environment({
            "TOSS_ACCESS_TOKEN": UntrustedString(" token "),
        })
        self.assertTrue(report["capabilities"][0]["configuration_present"])

    def test_inspects_only_allowlisted_names_once_without_enumeration(self):
        environ = StrictEnvironment({name: SECRET for name in VARIABLES})
        report = self.readiness.inspect_environment(environ)
        self.assert_safe_report(report)
        self.assertEqual(environ.lookups, list(VARIABLES))

    def test_none_uses_process_environment_but_empty_mapping_does_not(self):
        environ = StrictEnvironment({name: SECRET for name in VARIABLES})
        with patch.object(self.readiness.os, "environ", environ):
            from_process = self.readiness.inspect_environment()
            from_empty = self.readiness.inspect_environment({})
        self.assertTrue(all(item["configuration_present"]
                            for item in from_process["capabilities"]))
        self.assertFalse(any(item["configuration_present"]
                             for item in from_empty["capabilities"]))
        self.assertEqual(environ.lookups, list(VARIABLES))

    def test_environment_failure_has_no_raw_exception_or_context(self):
        class BrokenEnvironment:
            def get(self, name):
                raise RuntimeError(SECRET + UNRELATED)

        try:
            self.readiness.inspect_environment(BrokenEnvironment())
        except ValueError as error:
            output = "".join(traceback.format_exception(
                type(error), error, error.__traceback__))
            self.assertNotIn(SECRET, output)
            self.assertNotIn(UNRELATED, output)
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
        else:
            self.fail("An inaccessible environment must not become a successful report")

    def test_rendered_text_explains_presence_and_the_existing_operator_guides(self):
        output = self.readiness.render_text(self.readiness.inspect_environment({}))
        self.assertIn("인증", output)
        self.assertIn("누락", output)
        self.assertIn("설정", output)
        self.assertIn("명시", output)
        self.assertIn("docs/STOCK_PAPER_TOSS_KIS.md", output)
        self.assertIn("docs/KIS_VTS_READONLY_QUOTE.md", output)
        self.assertIn("docs/KIS_VTS_ORDER_SNAPSHOT.md", output)
        for name in VARIABLES:
            self.assertIn(name, output)

    def test_text_renderer_uses_only_allowlisted_names_and_boolean_presence(self):
        report = self.readiness.inspect_environment({})
        report[UNRELATED] = SECRET
        report["capabilities"][0]["missing_variables"] = [SECRET]
        report["capabilities"][0]["variable_presence"][UNRELATED] = SECRET
        output = self.readiness.render_text(report)
        self.assertNotIn(SECRET, output)
        self.assertNotIn(UNRELATED, output)
        self.assertIn("TOSS_ACCESS_TOKEN", output)

    def test_cli_failure_output_is_static_and_secret_free(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(self.readiness, "inspect_environment",
                          side_effect=RuntimeError(SECRET + UNRELATED)):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = self.readiness.main([])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(stderr.getvalue().strip())
        self.assertNotIn(SECRET, stderr.getvalue())
        self.assertNotIn(UNRELATED, stderr.getvalue())

    def run_cli(self, arguments=(), values=None, *, directory=None, guarded=False):
        environment = {name: value for name, value in os.environ.items()
                       if name not in VARIABLES}
        environment.update(values or {})
        environment[UNRELATED] = SECRET
        command = [sys.executable, "-B", "-S", str(SCRIPT), *arguments]
        if guarded:
            command = [sys.executable, "-B", "-S", "-c", r'''
import builtins, io, json, os, socket, subprocess, sys
script = sys.argv[1]
with open(script, encoding="utf-8") as source:
    code = compile(source.read(), script, "exec")
sys.argv = sys.argv[1:]
violations = []
def forbidden(*args, **kwargs):
    violations.append("forbidden side effect")
    raise AssertionError("Side effects forbidden")
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name not in ("json", "os", "sys"):
        return forbidden()
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
subprocess.Popen = os.system = forbidden
try:
    exec(code, {"__name__": "__main__", "__file__": script})
finally:
    if violations:
        raise AssertionError("Diagnostic attempted a forbidden side effect")
''', str(SCRIPT), *arguments]
        return subprocess.run(command, cwd=directory or ROOT, env=environment,
                              text=True, capture_output=True, check=False, timeout=10)

    def test_real_site_disabled_cli_defaults_to_json_and_missing_is_success(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assert_safe_report(report)
        self.assertFalse(any(item["configuration_present"]
                             for item in report["capabilities"]))

    def test_real_guarded_cli_has_no_file_network_process_or_broker_imports(self):
        result = self.run_cli(values={name: SECRET for name in VARIABLES}, guarded=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assert_safe_report(report)
        self.assertTrue(all(item["configuration_present"]
                            for item in report["capabilities"]))
        self.assertNotIn(SECRET, result.stdout)
        self.assertNotIn(UNRELATED, result.stdout)

    def test_real_text_cli_ignores_dotenv_and_does_not_change_files(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            original = "\n".join(name + "=" + SECRET for name in VARIABLES)
            dotenv.write_text(original, encoding="utf-8")
            result = self.run_cli(["--format", "text"], directory=directory,
                                  values={"TOSS_ACCESS_TOKEN": " \r\n\t "})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIn("누락", result.stdout)
            self.assertIn("TOSS_ACCESS_TOKEN", result.stdout)
            self.assertNotIn(SECRET, result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [dotenv])
            self.assertEqual(dotenv.read_text(encoding="utf-8"), original)

    def test_real_cli_rejects_invalid_arguments_without_echoing_input(self):
        for arguments in (
            ["--format", SECRET], ["--format"], ["--mode", "live"],
            ["--" + SECRET], ["--for", "text"], ["@" + SECRET],
        ):
            with self.subTest(arguments=arguments):
                result = self.run_cli(arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())
                self.assertNotIn(SECRET, result.stderr)
                self.assertNotIn(UNRELATED, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_real_cli_help_is_successful_without_environment_values(self):
        result = self.run_cli(["--help"], {name: SECRET for name in VARIABLES})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("--format", result.stdout)
        self.assertNotIn(SECRET, result.stdout)


if __name__ == "__main__":
    unittest.main()
