import unittest
from tools.playback_qualification import plan,summarize

class PlaybackQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix=plan()
        cls.cell=next(c for c in cls.matrix["cells"] if c["id"]=="loopback/zip-deflate/wav-pcm16")

    def row(self,i=0,**changes):
        r={"cell":self.cell["id"],"lane":"controlled","attempt_id":str(i),"outcome":"pass",
           "action":"seek","package_sha256":"a"*64,"host_sha256":"b"*64,"decoder_sha256":"c"*64,
           "network_profile":"reference","cache_state":"cold","architecture":"x64","work_class":"bounded",
           "fixture_sha256":"d"*64,"track_sha256":"e"*64,"archive_sha256":"f"*64,
           "evidence_sha256":"1"*64,"pcm_verified":True,"latency_ms":100}
        r.update(changes)
        return r

    def test_full_three_way_not_pairwise(self):
        cells=self.matrix["cells"]
        p={c["provider"] for c in cells};a={c["archive"] for c in cells};d={c["audio"] for c in cells}
        self.assertEqual(len(cells),len(p)*len(a)*len(d))
        self.assertTrue(all(c["disposition"]=="not-run" and c["eligibility"]=="unverified" for c in cells))
        self.assertEqual(plan(),self.matrix)

    def test_empty_not_qualified(self):
        x=summarize(self.matrix,[])
        self.assertFalse(x["release_qualified"])
        self.assertEqual(len(x["not_attempted_cells"]),len(self.matrix["cells"]))

    def test_no_cross_lane_or_context_pooling(self):
        with self.assertRaisesRegex(ValueError,"lane mismatch"):
            summarize(self.matrix,[self.row(lane="live")])
        rows=[self.row(i,cache_state="cold" if i<50 else "warm") for i in range(100)]
        r=summarize(self.matrix,rows)
        self.assertEqual(len(r["groups"]),2)
        self.assertTrue(all(x["p95_ms"] is None for x in r["groups"]))

    def test_failure_censoring(self):
        rows=[self.row(i,outcome="timeout" if i<6 else "pass") for i in range(100)]
        r=summarize(self.matrix,rows)["groups"][0]
        self.assertEqual(r["attempted"],100)
        self.assertIsNone(r["p95_ms"])
        self.assertEqual(r["outcomes"]["timeout"],6)
        rows[0]["outcome"]="pass"
        self.assertEqual(summarize(self.matrix,rows)["groups"][0]["p95_ms"],100)

    def test_no_duplicate_or_missing_oracle(self):
        with self.assertRaisesRegex(ValueError,"Duplicate attempt"):
            summarize(self.matrix,[self.row(),self.row()])
        with self.assertRaisesRegex(ValueError,"Missing PCM"):
            summarize(self.matrix,[self.row(pcm_verified=False)])
        with self.assertRaisesRegex(ValueError,"Incomplete full-track"):
            summarize(self.matrix,[self.row(action="full-track")])

    def test_same_track_not_double_counted(self):
        rows=[self.row(i,action="full-track",member_bytes_equal=True,full_pcm_equal=True,
                       metadata_equal=True,eof_verified=True) for i in range(2)]
        r=summarize(self.matrix,rows)
        self.assertEqual(r["distinct_full_track_pass_hashes"],1)
        self.assertEqual(r["distinct_full_track_archive_hashes"],1)
