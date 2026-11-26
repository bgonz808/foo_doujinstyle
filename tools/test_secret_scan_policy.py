"""Exact historical adjudication, without broad file/rule/line exclusions."""
import unittest
from tools.secret_scan_policy import HISTORICAL, FINGERPRINT, verdict


def known():
    return {**HISTORICAL, "Fingerprint": FINGERPRINT}


class SecretPolicyTests(unittest.TestCase):
    def test_only_historical_occurrence(self):
        self.assertEqual(verdict([known()], 23), (1, 0))

    def test_each_coordinate_is_required(self):
        for field, changed in (("Commit", "a"*40), ("File", "other.md"),
                               ("RuleID", "github-pat"), ("StartLine", 112),
                               ("EndLine", 112), ("Fingerprint", "different")):
            with self.subTest(field=field):
                self.assertEqual(verdict([{**known(), field: changed}], 23), (0, 1))

    def test_future_commit_even_at_same_line_is_not_exempt(self):
        new = {**known(), "Commit": "b"*40}
        new["Fingerprint"] = "{Commit}:{File}:{RuleID}:{StartLine}".format(**new)
        self.assertEqual(verdict([known(), new], 23), (1, 1))

    def test_duplicate_fingerprint_is_not_blanket_exclusion(self):
        self.assertEqual(verdict([known(), known()], 23), (1, 1))

    def test_report_exit_consistency(self):
        self.assertEqual(verdict([], 0), (0, 0))
        for data, rc in (([], 23), ([known()], 0), ([{}], 23), (None, 0), ([], 1)):
            with self.subTest(rc=rc), self.assertRaises(ValueError):
                verdict(data, rc)
