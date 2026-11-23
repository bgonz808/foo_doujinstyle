"""Run unittest discovery and emit JUnit XML with real per-case outcomes.

This is development tooling; it never ships with the foobar2000 component.
"""
from __future__ import annotations

import argparse
import sys
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.records.setdefault(test.id(), {"outcome": "unknown"})
        self.records[test.id()]["duration"] = max(0.0, time.monotonic() -
                                                   self.started.get(test.id(), time.monotonic()))
        super().stopTest(test)

    def addSuccess(self, test):
        self.records[test.id()] = {"outcome": "passed"}
        super().addSuccess(test)

    def addSkip(self, test, reason):
        self.records[test.id()] = {"outcome": "skipped", "message": reason}
        super().addSkip(test, reason)

    def addFailure(self, test, err):
        self.records[test.id()] = {"outcome": "failure",
                                   "message": self._exc_info_to_string(err, test)}
        super().addFailure(test, err)

    def addError(self, test, err):
        self.records[test.id()] = {"outcome": "error",
                                   "message": self._exc_info_to_string(err, test)}
        super().addError(test, err)

    def addExpectedFailure(self, test, err):
        self.records[test.id()] = {"outcome": "skipped",
                                   "message": "expected failure: " + self._exc_info_to_string(err, test)}
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.records[test.id()] = {"outcome": "failure", "message": "unexpected success"}
        super().addUnexpectedSuccess(test)


def to_xml(records: dict, suite_name: str) -> ET.Element:
    failures = sum(r["outcome"] == "failure" for r in records.values())
    errors = sum(r["outcome"] == "error" for r in records.values())
    skips = sum(r["outcome"] == "skipped" for r in records.values())
    root = ET.Element("testsuite", {"name": suite_name, "tests": str(len(records)),
                      "failures": str(failures), "errors": str(errors),
                      "skipped": str(skips)})
    for test_id, item in sorted(records.items()):
        klass, _, name = test_id.rpartition(".")
        case = ET.SubElement(root, "testcase", {
            "classname": klass, "name": name,
            "time": f"{item.get('duration', 0.0):.6f}"})
        if item["outcome"] in {"failure", "error", "skipped"}:
            elem = ET.SubElement(case, item["outcome"] if item["outcome"] != "failure"
                                 else "failure", {"message": item.get("message", "")[:2048]})
            elem.text = item.get("message", "")[:32768]
    return root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--pattern", default="test*.py")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--suite-name", required=True)
    args = ap.parse_args()
    suite = unittest.TestLoader().discover(args.start, pattern=args.pattern)
    runner = unittest.TextTestRunner(verbosity=1, resultclass=RecordedResult)
    result = runner.run(suite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    root = to_xml(result.records, args.suite_name)
    ET.ElementTree(root).write(args.output, encoding="utf-8", xml_declaration=True)
    if result.testsRun == 0:
        print("FAIL: empty unittest suite", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
