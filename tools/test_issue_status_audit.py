"""Offline negative tests for the read-only GitHub Issues lifecycle auditor."""
from datetime import date, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from tools.issue_status_audit import audit, issue_status, main


def record(number=1, state="open", lifecycle="In progress — working"):
    today = date.today().isoformat()
    return {
        "number": number,
        "state": state,
        "html_url": f"https://github.com/bgonz808/foo_doujinstyle/issues/{number}",
        "body": (
            f"## Current work status (audited {today})\n\n"
            f"**Lifecycle:** {lifecycle}.\n\n"
            "**Verified progress:** A real source contract and test exists.\n\n"
            "**Remaining / next action:** Verify final build and host behavior.\n\n"
            "**Evidence / owner:** https://github.com/bgonz808/foo_doujinstyle/actions.\n"
        ),
    }


class IssueLifecycleAuditTests(unittest.TestCase):
    def inspect(self, issue):
        return issue_status(issue, today=date.today(), stale_days=60)

    def test_valid_open_and_closed(self):
        self.assertEqual(self.inspect(record())[0], [])
        self.assertEqual(self.inspect(record(2, "closed", "Completed — shipped"))[0], [])
        self.assertEqual(self.inspect(record(3, "closed", "Superseded/resolved — extracted"))[0], [])

    def test_missing_heading_fails(self):
        case = record()
        case["body"] = case["body"].replace("## Current work status", "## Obsolete status")
        self.assertTrue(any("one dated" in x for x in self.inspect(case)[0]))

    def test_duplicate_headings_fail(self):
        case = record()
        case["body"] += "\n## Current work status (audited " + date.today().isoformat() + ")\n"
        self.assertTrue(any("got 2" in x for x in self.inspect(case)[0]))

    def test_open_cannot_claim_completed_or_superseded(self):
        self.assertTrue(any("contradicts" in x for x in self.inspect(record(lifecycle="Completed — done"))[0]))
        self.assertTrue(any("contradicts" in x for x in self.inspect(record(lifecycle="Superseded/resolved — done"))[0]))

    def test_closed_cannot_claim_in_progress(self):
        self.assertTrue(any("contradicts" in x for x in self.inspect(record(state="closed"))[0]))

    def test_future_date_is_error(self):
        case = record()
        case["body"] = case["body"].replace(date.today().isoformat(),
                                              (date.today() + timedelta(days=1)).isoformat())
        self.assertIn("audit date lies in the future", self.inspect(case)[0])

    def test_old_open_status_warns_but_closed_does_not(self):
        old = (date.today() - timedelta(days=75)).isoformat()
        case = record()
        case["body"] = case["body"].replace(date.today().isoformat(), old)
        errors, warnings, _ = self.inspect(case)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)
        case["state"] = "closed"
        case["body"] = case["body"].replace("In progress", "Completed")
        self.assertEqual(self.inspect(case)[1], [])

    def test_missing_fields_and_url_fail(self):
        case = record()
        case["body"] = case["body"].replace("**Verified progress:**", "**Old progress:**")
        case["html_url"] = ""
        errors = self.inspect(case)[0]
        self.assertTrue(any("Verified progress" in x for x in errors))
        self.assertIn("missing issue permalink", errors)

    def test_prs_excluded_and_report_scoped(self):
        pr = record(9)
        pr["pull_request"] = {"url": "https://api.github.com/example"}
        report = audit([record(), record(2, "closed", "Completed — done"), pr],
                       repo="bgonz808/foo_doujinstyle", today=date.today(),
                       stale_days=60, source_sha="b" * 40)
        self.assertEqual(report["issue_count"], 2)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["source_commit"], "b" * 40)
        self.assertEqual((report["open_count"], report["closed_count"]), (1, 1))
        self.assertNotIn(9, [r["number"] for r in report["results"]])

    def test_empty_or_duplicated_issue_set_is_not_green(self):
        with self.assertRaises(ValueError):
            audit([], repo="bgonz808/foo_doujinstyle", today=date.today(), stale_days=60)
        with self.assertRaises(ValueError):
            audit([record(), record()], repo="bgonz808/foo_doujinstyle",
                  today=date.today(), stale_days=60)
        with self.assertRaises(ValueError):
            audit([record()], repo="../other", today=date.today(), stale_days=60)

    def test_offline_cli_writes_real_errors_without_live_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "issues.json"
            output = Path(tmp) / "result.json"
            fixture.write_text(json.dumps([record(), record(2, "closed", "Completed — done")]),
                               encoding="utf-8")
            self.assertEqual(main(["--fixture", str(fixture), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["issue_count"], 2)
            fixture.write_text(json.dumps([record(lifecycle="Completed — false pass")]),
                               encoding="utf-8")
            self.assertEqual(main(["--fixture", str(fixture), "--output", str(output)]), 1)


if __name__ == "__main__":
    unittest.main()
