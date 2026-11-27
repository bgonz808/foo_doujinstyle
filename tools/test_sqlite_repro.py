import unittest
from collections import Counter
from tools.sqlite_repro import command_flags, sequence, summaries

class NativeDeterministicTests(unittest.TestCase):
    def test_balanced_path_profiles(self):
        for replica in ("a", "b"):
            self.assertEqual(len(sequence(replica)), 12)
            self.assertEqual(set(Counter(sequence(replica)).values()), {2})
        self.assertNotEqual(sequence("a"), sequence("b"))

    def test_actual_flags(self):
        base = "/Brepro /Z7 /Qspectre"
        self.assertTrue(command_flags(base, "baseline"))
        self.assertTrue(command_flags(base + " /experimental:deterministic", "experimental"))
        self.assertTrue(command_flags(base + " /experimental:deterministic /pathmap:C:/x=X:/y", "mapped"))
        self.assertFalse(command_flags(base + " /pathmap:C:/x=X:/y", "mapped"))
        self.assertFalse(command_flags(base + " /cgthreads1", "baseline"))

    def test_missing_samples_never_pass(self):
        rows = [{"profile":"mapped", "location":"a", "status":"ok", "seconds":1,
                 "object":{"sha256":"a"}, "library":{"sha256":"b"}}]
        self.assertFalse(summaries(rows)["mapped"]["object_equal"])
        self.assertFalse(summaries(rows)["baseline"]["complete"])
