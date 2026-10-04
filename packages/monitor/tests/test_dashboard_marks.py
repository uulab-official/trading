"""Render actual embedded dashboard JavaScript without starting any service."""

from pathlib import Path
import shutil
import subprocess
import unittest


class DashboardMarkTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for embedded JavaScript rendering checks")
    def test_unknown_and_zero_marks_render_differently(self):
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run([shutil.which("node"), str(root / "tools/check_dashboard_marks.js")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("5 cases passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
