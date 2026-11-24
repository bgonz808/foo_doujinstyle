"""Run every existing portable unittest once using canonical module identities."""
from pathlib import Path
import sys
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def unique_suite(suites):
    unique = {}
    duplicates = 0
    for suite in suites:
        for case in cases(suite):
            key = case.id()
            if key in unique:
                # Different test objects representing the same canonical case are
                # expected for overlapping discovery. Different classes are not.
                if type(unique[key]) is not type(case):
                    raise ValueError(f"Ambiguous canonical test identity: {key}")
                duplicates += 1
            else:
                unique[key] = case
    if not unique:
        raise ValueError("No portable tests discovered")
    return unittest.TestSuite(unique.values()), duplicates


def discover(root):
    # Same discovery domains as test_portable.sh historically used, but give
    # native tests their package-qualified identity rather than a second alias.
    loader = unittest.TestLoader()
    root = root.resolve()
    suites = [
        loader.discover(str(root), pattern="test*.py", top_level_dir=str(root)),
        loader.discover(str(root / "native"), pattern="test*.py", top_level_dir=str(root)),
        loader.loadTestsFromNames(["tools.test_facet_ui_source", "tools.test_build_identity"]),
    ]
    if loader.errors:
        raise RuntimeError("Portable discovery errors:\n" + "\n".join(loader.errors))
    return unique_suite(suites)


def main():
    suite, duplicates = discover(Path(__file__).resolve().parent.parent)
    print(f"TEST-METRIC unique_cases={suite.countTestCases()} duplicate_invocations_removed={duplicates}", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
