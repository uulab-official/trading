import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExportPathTests(unittest.TestCase):
    def setUp(self):
        path = ROOT/'tools/paper_replay_dashboard.py'
        self.assertTrue(path.is_file(), 'replay exporter is missing')
        spec = importlib.util.spec_from_file_location('paper_replay_dashboard', path)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root/'fixture.json'; self.fixture.write_text('{}')
        self.checkpoint = self.root/'checkpoint.json'
        self.report = self.root/'report.json'

    def test_input_output_and_lock_aliases_are_rejected_before_running(self):
        for fixture, checkpoint, report in [
            (self.fixture, self.fixture, self.report),
            (self.fixture, self.checkpoint, self.fixture),
            (self.fixture, self.checkpoint, self.checkpoint),
            (self.fixture, self.checkpoint, self.root/'.checkpoint.json.lock'),
            (self.root/'.report.json.lock', self.checkpoint, self.report),
        ]:
            with self.subTest(paths=(fixture, checkpoint, report)), self.assertRaises(ValueError):
                self.m.validate_paths(fixture, checkpoint, report)
        self.assertEqual(self.fixture.read_text(), '{}')

    def test_hardlink_and_symlink_aliases(self):
        self.report.hardlink_to(self.fixture)
        with self.assertRaises(ValueError): self.m.validate_paths(self.fixture, self.checkpoint, self.report)
        self.report.unlink(); self.report.symlink_to(self.fixture)
        with self.assertRaises(ValueError): self.m.validate_paths(self.fixture, self.checkpoint, self.report)

    def test_unrelated_report_file_is_not_overwritten(self):
        self.report.write_text('unrelated file')
        with self.assertRaises(ValueError): self.m.validate_paths(self.fixture, self.checkpoint, self.report)
        self.assertEqual(self.report.read_text(), 'unrelated file')

    def test_distinct_paths_and_no_parent_creation(self):
        result = self.m.validate_paths(self.fixture, self.checkpoint, self.report)
        self.assertEqual(tuple(map(Path, result)), (self.fixture, self.checkpoint, self.report))
        self.assertFalse(self.checkpoint.exists()); self.assertFalse(self.report.exists())


if __name__ == '__main__': unittest.main()
