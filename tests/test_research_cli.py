import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/server'))
sys.path.insert(0, str(ROOT / 'tests'))
from test_quant_research import dataset, CANDIDATES, SETTINGS, metrics

class CLITests(unittest.TestCase):

    def setUp(self):
        self.path = ROOT / 'tools/quant_research.py'
        self.assertTrue(self.path.exists(), 'research CLI missing')
        spec = importlib.util.spec_from_file_location('quant_research_cli', self.path)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'input.json'
        self.output = Path(self.tmp.name) / 'out.json'
        import json
        self.source.write_text(json.dumps(dataset()))

    def test_paths_never_alias_or_overwrite(self):
        with self.assertRaises(ValueError):
            self.m.validate_paths(self.source, self.source)
        self.output.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.m.validate_paths(self.source, self.output)

    def test_export_calls_existing_input_validation_and_research(self):
        from trading_bot.walk_forward_research import run_research
        with patch.object(self.m, 'load_input', return_value=dataset()) as loader, patch.object(self.m, 'run_research', side_effect=lambda d, h, **kw: run_research(d, h, **kw, evaluator=lambda *a: metrics())):
            r = self.m.export_research(self.source, self.output, candidates=CANDIDATES, settings=SETTINGS)
            self.assertEqual(r['summary']['total_folds'], 3)
            loader.assert_called_once()
            before = self.output.read_bytes()
            self.m.export_research(self.source, self.output, candidates=CANDIDATES, settings=SETTINGS)
            self.assertEqual(before, self.output.read_bytes())

    def test_invalid_input_cannot_publish(self):
        with patch.object(self.m, 'load_input', side_effect=ValueError('invalid source')):
            with self.assertRaises(ValueError):
                self.m.export_research(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_no_unknown_existing_report_is_replaced(self):
        self.output.write_text('unrelated')
        with self.assertRaises(ValueError):
            self.m.validate_paths(self.source, self.output)
        self.assertEqual(self.output.read_text(), 'unrelated')
if __name__ == '__main__':
    unittest.main()
