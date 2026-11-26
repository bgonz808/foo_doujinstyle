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
