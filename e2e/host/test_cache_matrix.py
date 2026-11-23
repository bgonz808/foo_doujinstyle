"""Deterministic cache scenario matrix contracts; does not run foobar2000."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from e2e.host.cache_matrix import MatrixError, EXPECTED, FACTOR_NAMES, build, valid

MANIFEST = Path(__file__).resolve().parents[2] / "quality" / "cache-scenarios.json"


class CacheMatrixContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_all_feasible_factor_pairs_covered_without_false_passes(self):
        report = build(self.doc)
        self.assertEqual(report["status"], "not_run")
        self.assertEqual(report["qualification"], "scenario-generator-not-foobar2000-qualification")
        self.assertEqual(report["executed_case_count"], 0)
        self.assertEqual(report["planned_pair_coverage_fraction"], 1.0)
        self.assertGreater(report["feasible_pair_count"], 100)
        self.assertGreater(report["feasible_combination_count"], 200)
        self.assertLessEqual(
            report["mandatory_anchor_count"] + report["pairwise_added_count"],
            self.doc["pairwise_case_limit"])
        self.assertTrue(all(c["qualification"]["verdict"] == "not_run" for c in report["cases"]))
        self.assertTrue(all(c["qualification"]["native_coverage"] is None for c in report["cases"]))

    def test_deterministic_generated_plan(self):
        first = build(self.doc)
        second = build(self.doc)
        self.assertEqual(first, second)
        self.assertEqual(len({x["id"] for x in first["cases"]}), len(first["cases"]))

    def test_essential_lifecycle_and_fault_anchors_survive(self):
        report = build(self.doc)
        keyed = {row["id"]: row for row in report["cases"]}
        required = {a["id"] for a in self.doc["anchors"]}
        self.assertTrue(required.issubset(keyed))
        for row in self.doc["anchors"]:
            entry = keyed[row["id"]]
            self.assertEqual(entry["factors"]["trigger"], row["trigger"])
            self.assertEqual(entry["expected"], EXPECTED[row["trigger"]])
            self.assertEqual(entry["origin"], "mandatory-anchor")
        faults = {f["id"] for f in self.doc["directed_faults"]}
        self.assertTrue(faults.issubset(keyed))
        self.assertTrue(all(keyed[k]["origin"] == "directed-fault" for k in faults))

    def test_invalid_combinations_rejected(self):
        base = next(a for a in self.doc["anchors"] if a["id"] == "fully-warmed-seek")
        for overrides in (
            {"archive": "direct"},
            {"archive": "zip-stored"},
            {"zip_directory": "missing", "checkpoints": "complete"},
            {"runtime": "fresh", "connection": "reused"},
            {"runtime": "restarted", "range_cache": "memory"},
            {"trigger": "seek", "catalog": "empty"},
            {"trigger": "drag", "catalog": "metadata"},
            {"provider": "mediafire-stub", "archive": "direct"},
            {"provider": "mega-stub"},
            {"range_cache": "disk"},
        ):
            with self.subTest(overrides=overrides):
                row = {k: base[k] for k in FACTOR_NAMES}
                row.update(overrides)
                self.assertFalse(valid(row))

    def test_selection_stays_passive(self):
        rows = build(self.doc)["cases"]
        selection = [row for row in rows if row["factors"]["trigger"] == "select"]
        self.assertTrue(selection)
        self.assertTrue(all(
            row["expected"] == "no_new_selection_caused_network_or_index_job"
            for row in selection if row["origin"] != "directed-fault"))
        self.assertTrue(all(
            "quiesce-existing-workers-before-counting-selection-attributed-work"
            in row["qualification"]["setup"] for row in selection))

    def test_reject_modified_anchor_and_bad_budget(self):
        changed = deepcopy(self.doc)
        changed["pairwise_case_limit"] = 1
        with self.assertRaises(MatrixError):
            build(changed)
        changed = deepcopy(self.doc)
        changed["anchors"][0]["checkpoints"] = "complete"
        with self.assertRaises(MatrixError):
            build(changed)
        changed = deepcopy(self.doc)
        changed["anchors"][0]["expected"] = "fake_pass"
        with self.assertRaises(MatrixError):
            build(changed)


if __name__ == "__main__":
    unittest.main()
