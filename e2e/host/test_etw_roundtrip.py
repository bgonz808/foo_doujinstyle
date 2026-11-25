"""Synthetic negative contracts for native transport verifier (not ETW execution)."""
import copy
import unittest
from e2e.host.etw_roundtrip import NATIVE, verify_native
from e2e.host.trace import TraceError

class EtwTransportContracts(unittest.TestCase):
    def setUp(self):
        self.run_id="a"*32
        self.events=[{"source":"component","event":name,"run_id":self.run_id,"pid":42,
                      "scenario_id":"etwprobe","attempt_id":"etwprobe-0001",
                      "clock":{"domain":"win-qpc:"+self.run_id,"tick":100+i,"frequency_hz":10000000},
                      "metrics":{"sample_count":480 if i==3 else 0}}
                     for i,name in enumerate(NATIVE)]

    def test_valid(self):
        verify_native(self.events,self.run_id,42)

    def test_reject_wrong_pid_run_and_clock(self):
        for key,value in (("pid",43),("run_id","b"*32),("attempt_id","wrong-0001")):
            data=copy.deepcopy(self.events)
            data[3][key]=value
            with self.assertRaises(TraceError):
                verify_native(data,self.run_id,42)
        data=copy.deepcopy(self.events)
        data[3]["clock"]["frequency_hz"]=1
        with self.assertRaises(TraceError):
            verify_native(data,self.run_id,42)

    def test_reject_missing_duplicate_empty_pcm(self):
        for data in (self.events[:-1],self.events+[self.events[3]]):
            with self.assertRaises(TraceError):
                verify_native(data,self.run_id,42)
        data=copy.deepcopy(self.events)
        data[3]["metrics"]["sample_count"]=0
        with self.assertRaises(TraceError):
            verify_native(data,self.run_id,42)
