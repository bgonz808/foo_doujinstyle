"""No false host qualification or Python retirement from a manifest alone."""
import copy
import unittest
from pathlib import Path
from tools.migration_burndown import report

COMMIT="a"*40
FEATURES={"schema_version":1,"migration_baseline":{
  "version":"0.0.64","packaged_python_lines":1375},"features":[{
   "id":"foreground-search","migration_state":"python-adapter-active",
   "contract_tests":["native.test_search.test_contract"],
   "host_e2e_tests":["doujinstyle.search.*"]}]}
STATS={"python_lines":1375,"fallback_python_lines":10,"deprecated_active_lines":0}

class MigrationBurndownTests(unittest.TestCase):
    def generate(self,features=None,stats=None):
        return report(Path(),features if features is not None else FEATURES,
                      stats if stats is not None else STATS,COMMIT)

    def test_exact_baseline_without_false_e2e_claim(self):
        obj=self.generate()
        self.assertEqual(obj["retired_python_lines_from_baseline"],0)
        self.assertEqual(obj["host_e2e_cases_executed"],0)
        self.assertIsNone(obj["native_host_line_coverage"])
        self.assertFalse(obj["retirement_qualified"])
        self.assertFalse(obj["features"][0]["safe_to_delete_python"])

    def test_real_ast_burndown_counts_as_source_only(self):
        stats={**STATS,"python_lines":887}
        obj=self.generate(stats=stats)
        self.assertEqual(obj["retired_python_lines_from_baseline"],488)
        self.assertFalse(obj["retirement_qualified"])

    def test_python_regression_fails(self):
        with self.assertRaisesRegex(ValueError,"line regression"):
            self.generate(stats={**STATS,"python_lines":1376})

    def test_invalid_or_falsely_qualified_feature_rejected(self):
        feature=copy.deepcopy(FEATURES)
        feature["features"][0]["migration_state"]="host-qualified-without-proof"
        with self.assertRaisesRegex(ValueError,"feature"):
            self.generate(features=feature)

    def test_no_host_suite_is_not_qualified(self):
        feature=copy.deepcopy(FEATURES)
        feature["features"][0]["host_e2e_tests"]=[]
        with self.assertRaisesRegex(ValueError,"host test contract"):
            self.generate(features=feature)

    def test_no_silent_negative_counts(self):
        with self.assertRaisesRegex(ValueError,"Invalid AST"):
            self.generate(stats={**STATS,"fallback_python_lines":-1})

if __name__=="__main__":
    unittest.main()
