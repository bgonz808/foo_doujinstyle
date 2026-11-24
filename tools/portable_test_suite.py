"""Run every existing portable unittest once using canonical module identities."""
import argparse
import os
import xml.etree.ElementTree as ET
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
        loader.loadTestsFromNames(["tools.test_facet_ui_source", "tools.test_build_identity",
                                   "tools.test_quality_evidence", "tools.test_quality_viewer",
                                   "tools.test_portable_test_suite", "tools.test_windows_matrix"]),
    ]
    if loader.errors:
        raise RuntimeError("Portable discovery errors:\n" + "\n".join(loader.errors))
    return unique_suite(suites)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    suite, duplicates = discover(Path(__file__).resolve().parent.parent)
    print(f"TEST-METRIC unique_cases={suite.countTestCases()} duplicate_invocations_removed={duplicates}", flush=True)
    from tools.quality.unittest_runner import RecordedResult, to_xml
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordedResult).run(suite)
    if args.junit:
        args.junit.parent.mkdir(parents=True, exist_ok=True)
        root = to_xml(result.records, "portable")
        root.set("source_commit", os.environ.get("GITHUB_SHA", ""))
        ET.ElementTree(root).write(args.junit, encoding="utf-8", xml_declaration=True)
    if any(r["outcome"] == "unknown" for r in result.records.values()):
        sys.exit(1)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
