import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).resolve().parents[1] / "packages/strategy/quant_metrics.py"
spec = importlib.util.spec_from_file_location("quant_metrics", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class QuantMetricsTests(unittest.TestCase):
    def test_drawdown(self):
        self.assertAlmostEqual(module.max_drawdown([100, 120, 90, 110]), 0.25)
        self.assertEqual(module.max_drawdown([]), 0.0)

    def test_sharpe_undefined(self):
        self.assertIsNone(module.sharpe_ratio([100]))
        self.assertIsNone(module.sharpe_ratio([100, 110, 121]))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            module.max_drawdown([100, 0])
        with self.assertRaises(ValueError):
            module.sharpe_ratio([100, 110, 100], periods_per_year=0)

    def test_sharpe_finite(self):
        result = module.sharpe_ratio([100, 102, 101, 104, 103])
        self.assertIsInstance(result, float)


if __name__ == "__main__":
    unittest.main()
