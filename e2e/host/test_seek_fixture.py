import unittest
from e2e.host.seek_fixture import sample,qualify_seeks,RATE,TARGETS

class SeekFixtureTests(unittest.TestCase):
    def test_oracle_distinct_targets(self):
        sequences={tuple(sample(t*RATE+i) for i in range(32)) for t in set(TARGETS)}
        self.assertEqual(len(sequences),len(set(TARGETS)))
        self.assertEqual(sample(0),-16384)
    def test_causal_position_proof(self):
        events=[]
        for i,target in enumerate(TARGETS):
            for j,name in enumerate(("robot.seek.intent","component.observer.sdk_seek_call","component.observer.seek_pcm_match","component.observer.seek_callback")):
                events.append({"event":name,"source":"robot" if j==0 else "component","pid":5 if j else 9,
                    "run_id":"run00001","scenario_id":"seek0001","attempt_id":"try00001",
                    "clock":{"tick":i*100+j+1,"domain":"win-qpc:run00001","frequency_hz":10000000},
                    "metrics":{"sample_count":target*RATE}})
        self.assertTrue(qualify_seeks(events,5,[],local=True)["qualified"])
        events[2]["metrics"]["sample_count"]+=1
        self.assertFalse(qualify_seeks(events,5,[],local=True)["qualified"])

    def test_large_fixture_limit_is_explicit_and_bounded(self):
        from tools.media_matrix import fixture_server, MAX_ASSET
        asset={"seek.wav":b"x"*(MAX_ASSET+1)}
        with self.assertRaises(ValueError):
            with fixture_server(asset): pass
        with fixture_server(asset,asset_limit=16*1024*1024) as (base,events):
            self.assertTrue(base.startswith("http://127.0.0.1:"))
        with self.assertRaises(ValueError):
            with fixture_server({},asset_limit=17*1024*1024): pass

    def test_stock_route_never_uses_native_decoder(self):
        from e2e.host.seek_fixture import qualify_stock
        inventory={"inventory_complete":True,"files":[],"loaded":[]}
        self.assertTrue(qualify_stock([],[],inventory,local=True)["qualified"])
        events=[{"event":"component.archive.whole_outer.open"}]
        self.assertTrue(qualify_stock(events,[{"bytes_sent":1}],inventory,local=False)["qualified"])
        self.assertFalse(qualify_stock(events,[],inventory,local=False)["qualified"])
        self.assertFalse(qualify_stock(events,[],inventory,local=True)["qualified"])
        self.assertFalse(qualify_stock([{"event":"component.decoder.first_pcm"}],[],inventory,local=True)["qualified"])

    def test_stock_http_distinguishes_transport(self):
        from e2e.host.seek_fixture import qualify_stock
        inventory={"inventory_complete":True}
        ledger=[{"bytes_sent":1}]
        self.assertTrue(qualify_stock([],ledger,inventory,local=False,native_http=True)["qualified"])
        self.assertFalse(qualify_stock([{"event":"component.archive.whole_outer.open"}],ledger,inventory,local=False,native_http=True)["qualified"])
        self.assertFalse(qualify_stock([],[],inventory,local=False,native_http=True)["qualified"])

    def test_phase_accounting_does_not_attribute_crossing_requests(self):
        from e2e.host.seek_http_phases import analyze_phases
        clock={"domain":"win-qpc:test","frequency_hz":1000000000,"offset_lo_ns":0,"offset_hi_ns":0}
        def event(name,tick):
            return {"event":name,"clock":{"domain":clock["domain"],"frequency_hz":clock["frequency_hz"],"tick":tick}}
        events=[event("robot.play.intent",100),event("component.observer.first_pcm",200)]
        for i in range(4):
            events.extend([event("component.observer.sdk_seek_call",300+i*100),event("component.observer.seek_pcm_match",350+i*100)])
        def row(i,start,end,offset,size):
            return {"request_id":i,"start_ns":start,"end_ns":end,"status":206,"response_offset":offset,
                    "bytes_sent":size,"asset":"archive.zip","response_complete":True,"connection_id":1}
        requests=[row(1,10,20,0,100),row(2,110,190,50,100),row(3,195,205,150,100)]
        result=analyze_phases(events,requests,clock,clock)
        self.assertEqual(result["total"]["unique_body_bytes"],250)
        self.assertEqual(result["total"]["duplicate_body_bytes"],50)
        self.assertEqual(result["phases"][1]["body_bytes"],100)
        self.assertEqual(result["phases"][1]["crossing_or_clock_ambiguous_ids"],[3])
        with self.assertRaises(ValueError): analyze_phases(events,requests,clock,{**clock,"offset_lo_ns":2,"offset_hi_ns":3})
