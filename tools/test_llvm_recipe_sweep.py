import unittest
from pathlib import Path
from tools.llvm_recipe_sweep import compression_args


class RecipeTests(unittest.TestCase):
    def test_exact_control(self):
        self.assertEqual(compression_args(Path("zstd"),0),
                         ["zstd","--ultra","-21","--long=30","-T4","-B1024M","-c"])
    def test_bounded_frame(self):
        args=compression_args(Path("zstd"),256,123)
        self.assertIn("--long=28",args)
        self.assertIn("--stream-size=123",args)
        self.assertIn("-T1",args)
        with self.assertRaises(ValueError):
            compression_args(Path("zstd"),256,0)


if __name__=="__main__":
    unittest.main()
