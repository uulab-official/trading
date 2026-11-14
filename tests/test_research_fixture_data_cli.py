"""Explicit input inspection through the actual guarded read-only CLI."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in ('apps/server', 'packages/data', 'packages/core', 'tests'):
    sys.path.insert(0, str(ROOT / part))
import test_research_report_cli as snapshots
from test_quant_research import dataset
from test_research_fixture_data import make_report
from trading_bot.research_report import research_identity


DATA_GUARD = (snapshots.GUARD.replace("'trading_data',", '')
              + '\nsys.path.insert(0, ' + repr(str(ROOT / 'packages/data')) + ')\n') + '''
class KnownDataDefinitionsOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        allowed = {'trading_data', 'trading_data.candle', 'trading_data.indicator',
                   'trading_data._validation', 'trading_data.upbit_history'}
        if fullname.startswith('trading_data') and fullname not in allowed:
            raise AssertionError('unexpected data integration imported')

sys.meta_path.insert(0, KnownDataDefinitionsOnly())
import trading_data.upbit_history as history
def no_acquisition(*args, **kwargs):
    raise AssertionError('inspection invoked market acquisition')
history.UpbitHistoryClient = no_acquisition
history.public_get = no_acquisition
history.build_opener = no_acquisition
'''


class FixtureDataCLITests(unittest.TestCase):
    local_cli = snapshots.ResearchReportCLITests.local_cli

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'report.json'
        self.fixture = self.root / 'PRIVATE_FIXTURE.json'
        self.output = self.root / 'report.html'
        self.script = ROOT / 'tools/paper_research_report.py'
        self.save()

    def save(self, *, version=1, data=None):
        data = dataset() if data is None else data
        self.raw = json.dumps(data).encode()
        self.fixture.write_bytes(self.raw)
        self.report = make_report(data, self.raw, robust=version == 2)
        self.source.write_text(json.dumps(self.report))
        self.before = self.source.read_bytes()

    def cli(self, *arguments, guard=DATA_GUARD):
        directory = self.root / 'guard'
        directory.mkdir(exist_ok=True)
        (directory / 'sitecustomize.py').write_text(guard)
        environment = dict(os.environ, PYTHONPATH=str(directory), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(self.script), *map(str, arguments)],
                                capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(result.stderr, '', result.stderr)
        self.assertTrue(result.stdout.strip())
        return result.returncode, json.loads(result.stdout)

    def arguments(self, html=False):
        result = (self.source, '--fixture', self.fixture, '--validate-fixture')
        return result + ('--html', self.output) if html else result

    def test_both_schemas_validate_and_export_without_execution_or_acquisition(self):
        for version in (1, 2):
            self.save(version=version)
            for html in (False, True):
                self.output = self.root / f'checked-{version}.html'
                with self.subTest(version=version, html=html):
                    code, result = self.cli(*self.arguments(html))
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result['fixture_check']['status'], 'matched')
                    self.assertEqual(result['fixture_data_check']['status'], 'matched')
                    self.assertEqual(result['fixture_data_check']['run_id'], self.report['run_id'])
                    self.assertEqual(result['fixture_data_check']['scope'], 'research_input_consistency')
                    self.assertEqual(result['run_id'], self.report['run_id'])
                    self.assertEqual(result['summary'], self.report['summary'])
                    self.assertEqual(result['html_written'], html)
                    self.assertNotIn(str(self.fixture), json.dumps(result))
                    self.assertEqual(self.source.read_bytes(), self.before)
                    self.assertEqual(self.fixture.read_bytes(), self.raw)
                    if html:
                        self.assertIn('입력 구조·정보 일치', self.output.read_text())
                        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_imported_synthetic_fixture_uses_validation_but_never_acquisition(self):
        from test_market_history_integration import synthetic_market_fixture
        self.save(data=synthetic_market_fixture(), version=2)
        code, result = self.cli(*self.arguments(True))
        self.assertEqual(code, 0, result)
        self.assertEqual(result['fixture_data_check']['observed']['symbol'], 'BTC/KRW')

    def test_validation_flag_requires_explicit_fixture_before_any_output(self):
        code, result = self.cli(self.source, '--validate-fixture', '--html', self.output)
        self.assertEqual(code, 2)
        self.assertEqual(result, {'error': 'research_report_arguments_invalid'})
        self.assertFalse(self.output.exists())

    def test_hash_only_keeps_arbitrary_byte_semantics_and_no_data_imports(self):
        raw = b'\xffnot JSON'
        self.fixture.write_bytes(raw)
        self.report['experiment']['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
        self.report['run_id'] = research_identity(self.report['experiment'])
        self.source.write_text(json.dumps(self.report))
        code, result = self.cli(self.source, '--fixture', self.fixture, guard=snapshots.GUARD)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['fixture_check']['status'], 'matched')
        self.assertNotIn('fixture_data_check', result)
        code, result = self.cli(self.source, guard=snapshots.GUARD)
        self.assertEqual(code, 0, result)
        self.assertNotIn('fixture_check', result)
        self.assertNotIn('fixture_data_check', result)

    def test_formatting_difference_remains_exit_one_with_matched_data(self):
        self.fixture.write_bytes(self.raw+b'\n')
        code, result = self.cli(*self.arguments(True))
        self.assertEqual(code, 1, result)
        self.assertEqual(result['fixture_check']['status'], 'mismatch')
        self.assertEqual(result['fixture_data_check']['status'], 'matched')
        self.assertIn('파일 해시 불일치', self.output.read_text())
        self.assertIn('입력 구조·정보 일치', self.output.read_text())

    def test_valid_metadata_difference_is_a_diagnostic_exit_one(self):
        data = dataset()
        data['symbol'] = 'ETH/USDT'
        self.fixture.write_text(json.dumps(data))
        code, result = self.cli(*self.arguments(True))
        self.assertEqual(code, 1, result)
        self.assertEqual(result['fixture_data_check']['status'], 'mismatch')
        self.assertEqual(result['fixture_data_check']['mismatches'], ['symbol'])
        self.assertEqual(result['summary'], self.report['summary'])
        self.assertIn('보고서 입력 정보 불일치', self.output.read_text())

    def test_invalid_content_with_matching_hash_has_sanitized_diagnostic_html(self):
        raw = b'{"PRIVATE_BAD_SOURCE":NaN}'
        self.fixture.write_bytes(raw)
        self.report['experiment']['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
        self.report['run_id'] = research_identity(self.report['experiment'])
        self.source.write_text(json.dumps(self.report))
        code, result = self.cli(*self.arguments(True))
        self.assertEqual(code, 1, result)
        self.assertEqual(result['fixture_check']['status'], 'matched')
        self.assertEqual(result['fixture_data_check']['status'], 'invalid')
        self.assertIsNone(result['fixture_data_check']['observed'])
        self.assertEqual(result['fixture_data_check']['error'], 'research_input_invalid')
        self.assertTrue(result['html_written'])
        document = self.output.read_text()
        self.assertIn('연구 입력 형식 오류', document)
        self.assertNotIn('PRIVATE_BAD_SOURCE', json.dumps(result)+document)
        self.assertNotIn(str(self.fixture), json.dumps(result)+document)

    def test_unavailable_file_still_fails_without_a_diagnostic_context_or_html(self):
        self.fixture.unlink()
        code, result = self.cli(*self.arguments(True))
        self.assertEqual(code, 2, result)
        self.assertEqual(result, {'error': 'research_report_fixture_unavailable'})
        self.assertFalse(self.output.exists())

    def test_source_alias_and_existing_output_are_preserved(self):
        hardlink = self.root / 'alias.html'
        os.link(self.fixture, hardlink)
        for output in (self.fixture, self.source, hardlink):
            with self.subTest(output=output.name):
                self.output = output
                code, result = self.cli(*self.arguments(True))
                self.assertEqual(code, 2, result)
                self.assertEqual(result, {'error': 'research_report_output_invalid'})
                self.assertEqual(self.fixture.read_bytes(), self.raw)
                self.assertEqual(self.source.read_bytes(), self.before)

    def test_unexpected_validator_failure_is_bounded_and_cannot_publish(self):
        from trading_bot import fixture_input
        with patch.object(fixture_input, 'load_fixture', side_effect=RuntimeError('PRIVATE_INTERNAL_ERROR')):
            code, result, err = self.local_cli(*self.arguments(True))
        self.assertEqual(code, 2)
        self.assertEqual(result, {'error': 'research_report_fixture_unavailable'})
        self.assertNotIn('PRIVATE_INTERNAL_ERROR', json.dumps(result)+err)
        self.assertFalse(self.output.exists())

    def test_invalid_data_does_not_override_uncertain_publication_or_interruption(self):
        from trading_core import paper_evidence
        from trading_bot import research_fixture_data
        self.fixture.write_bytes(b'invalid JSON')
        sync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('PRIVATE_PUBLICATION_ERROR')
            return sync(fd)
        with patch.object(paper_evidence.os, 'fsync', side_effect=fail_directory):
            code, result, err = self.local_cli(*self.arguments(True))
        self.assertEqual(code, 3)
        self.assertEqual(result, {'error': 'research_report_published_check_required'})
        self.assertIn('연구 입력 형식 오류', self.output.read_text())
        self.assertNotIn('PRIVATE_PUBLICATION_ERROR', json.dumps(result)+err)
        self.output = self.root / 'interrupted.html'
        with patch.object(research_fixture_data, 'check_fixture_data', side_effect=KeyboardInterrupt):
            code, result, _ = self.local_cli(*self.arguments(True))
        self.assertEqual(code, 130)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
