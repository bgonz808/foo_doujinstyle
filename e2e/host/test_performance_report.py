import unittest
from copy import deepcopy
from e2e.host.performance_report import compare,ratio

class PerformanceReportTests(unittest.TestCase):
    def test_zero_denominator_and_missing(self):
        self.assertIsNone(ratio(5,0))
        self.assertIsNone(ratio(None,3))
        self.assertEqual(ratio(0,3),0)
    def test_comparison_preserves_counts_and_unmeasured_ratios(self):
        arm={"first_pcm_intent_ns":20,"first_pcm_sdk_ns":10,"total":{"body_bytes":100,"request_count":2},
             "phases":{"play_to_pcm":{"body_bytes":50,"request_count":1}},
             "seeks":[{"sdk_to_matched_pcm_ns":3,"intent_to_matched_pcm_ns":4}],
             "normalized":{"body_amplification":None}}
        a={"schema_version":1,"protocol":"p","fixtures":{"audio_sha256":"same"},
           "archive_template_inventory":[],"status":"passed","build":{"version":"1"},
           "identity":{k:"same" for k in ("host_sha256","foobar_version","playback_method","cache_state","output_backend","execution_environment")},
           "arms":{"test":arm}}
        b=deepcopy(a);b["arms"]["test"]["total"]["request_count"]=3
        c=compare(a,b)
        self.assertEqual(c["arms"]["test"]["total.request_count"]["delta"],1)
        self.assertEqual(c["arms"]["test"]["total.request_count"]["ratio"],1.5)
        self.assertFalse(c["statistical_regression_qualified"])
        b["fixtures"]["audio_sha256"]="different"
        with self.assertRaises(ValueError):compare(a,b)
