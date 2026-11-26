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
