import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).resolve().parents[1] / "packages/strategy/walk_forward.py"
spec = importlib.util.spec_from_file_location("walk_forward", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class WalkForwardTests(unittest.TestCase):
    def test_rolling_embargo(self):
        folds = module.walk_forward_folds(20, 6, 4, embargo=2)
        self.assertEqual(folds[0], module.Fold(0, 6, 8, 12))
        self.assertEqual(folds[1], module.Fold(4, 10, 12, 16))
        self.assertTrue(all(f.train_end + 2 == f.test_start for f in folds))

    def test_anchored(self):
        folds = module.walk_forward_folds(20, 6, 4, anchored=True)
        self.assertEqual([f.train_start for f in folds], [0, 0, 0])

    def test_insufficient_history(self):
        self.assertEqual(module.walk_forward_folds(5, 6, 2), [])

    def test_invalid(self):
        with self.assertRaises(ValueError):
            module.walk_forward_folds(20, 0, 2)


if __name__ == "__main__":
    unittest.main()
