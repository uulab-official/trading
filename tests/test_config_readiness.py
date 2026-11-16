"""Configuration completeness never certifies an operational trading runtime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages' / 'core'))

import yaml
from trading_core.config_manager import ConfigManager


class ConfigurationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manager = ConfigManager(str(self.directory))

    def write_configuration(self, mode='live', **exchange_overrides):
        # These are inert test values; no provider or outbound integration runs.
        exchange = {'id': 'test', 'sandbox': False, 'testnet': False,
                    'api_key': 'synthetic-key', 'api_secret': 'synthetic-secret'}
        exchange.update(exchange_overrides)
        settings = {'bot': {'mode': mode}, 'exchange': exchange,
                    'strategies': [{'name': 'example', 'enabled': True}]}
        (self.directory / 'active' / 'bot_config.yml').write_text(
            yaml.safe_dump(settings), encoding='utf-8')
        return self.manager.load_config(use_env_vars=False)

    def run_cli(self, command):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'packages' / 'core'))
        return subprocess.run(
            [sys.executable, '-m', 'trading_core.config_cli', '--config-dir',
             str(self.directory), command], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=10)

    def test_unloaded_configuration_cannot_be_complete_or_production_ready(self):
        self.assertFalse(self.manager.is_production_ready())
        self.assertFalse(self.manager.is_live_configuration_complete())

    def test_complete_live_configuration_does_not_certify_production_readiness(self):
        self.write_configuration()
        self.assertEqual(self.manager.validate_config(), [])
        self.assertFalse(self.manager.is_production_ready())
        self.assertTrue(self.manager.is_live_configuration_complete())

    def test_configuration_completeness_preserves_live_validation_requirements(self):
        for overrides in ({'api_key': ''}, {'api_secret': ''},
                          {'sandbox': True}, {'testnet': True}):
            with self.subTest(overrides=overrides):
                self.write_configuration(**overrides)
                self.assertFalse(self.manager.is_live_configuration_complete())
                self.assertFalse(self.manager.is_production_ready())
        self.write_configuration(mode='paper')
        self.assertEqual(self.manager.validate_config(), [])
        self.assertFalse(self.manager.is_live_configuration_complete())

    def test_validate_complete_live_config_succeeds_without_operational_claim(self):
        self.write_configuration()
        result = self.run_cli('validate')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Live configuration: complete', result.stdout)
        self.assertIn('Operational readiness: unverified', result.stdout)
        self.assertNotIn('production ready', result.stdout.lower())
        self.assertNotIn('synthetic-secret', result.stdout)

    def test_show_distinguishes_configuration_from_operational_readiness(self):
        self.write_configuration()
        result = self.run_cli('show')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Live configuration: complete', result.stdout)
        self.assertIn('Operational readiness: unverified', result.stdout)
        self.assertNotIn('production ready', result.stdout.lower())

    def test_validate_paper_success_and_invalid_live_failure_keep_exit_codes(self):
        self.write_configuration(mode='paper')
        paper = self.run_cli('validate')
        self.assertEqual(paper.returncode, 0, paper.stderr)
        self.assertIn('Live configuration: incomplete', paper.stdout)
        self.assertIn('Operational readiness: unverified', paper.stdout)
        self.write_configuration(api_key='')
        invalid = self.run_cli('validate')
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        self.assertIn('Live mode requires API keys', invalid.stdout)


if __name__ == '__main__':
    unittest.main()
