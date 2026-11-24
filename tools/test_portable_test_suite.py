"""Coverage contracts for canonical portable test scheduling."""
import unittest
from tools.portable_test_suite import cases, unique_suite


class PortableSuiteContracts(unittest.TestCase):
    def test_deduplicate_overlapping_suites_without_dropping_distinct_cases(self):
        class Fixture(unittest.TestCase):
            def test_a(self):
                pass
            def test_b(self):
                pass
        a, b = Fixture("test_a"), Fixture("test_b")
        suite, duplicates = unique_suite([
            unittest.TestSuite([a, b]), unittest.TestSuite([Fixture("test_a")])])
        self.assertEqual(duplicates, 1)
        self.assertEqual([c.id() for c in cases(suite)], [a.id(), b.id()])
        self.assertEqual(suite.countTestCases(), 2)

    def test_empty_discovery_is_not_success(self):
        with self.assertRaisesRegex(ValueError, "No portable tests"):
            unique_suite([unittest.TestSuite()])

    def test_distinct_classes_with_same_identity_are_rejected(self):
        class First(unittest.TestCase):
            def id(self):
                return "same"
        class Second(First):
            pass
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            unique_suite([unittest.TestSuite([First(), Second()])])


if __name__ == "__main__":
    unittest.main()
