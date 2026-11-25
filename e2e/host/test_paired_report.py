import unittest
from e2e.host.paired_report import stage_offsets

class PairedReportContracts(unittest.TestCase):
    def test_missing_and_ambiguous_stages_not_zero(self):
        def ev(name,source,pid,tick):
            return dict(event=name,source=source,pid=pid,run_id="a"*32,scenario_id="fixtureplay",
                attempt_id="fixtureplay-0001",clock=dict(domain="win-qpc:"+"a"*32,tick=tick,frequency_hz=10000000))
        start=ev("robot.play.intent","robot",1,100)
        common=ev("component.observer.first_pcm","component",2,200)
        stage=ev("component.decoder.initialize.begin","component",2,150)
        result=stage_offsets([start,common,stage,stage],2)
        self.assertEqual(result["component.observer.first_pcm"]["offset_ns"],10000)
        self.assertIsNone(result["component.decoder.initialize.begin"]["offset_ns"])
        self.assertIsNone(result["output.pcm.first"]["offset_ns"])
        self.assertIsNone(stage_offsets([start,common],3)["component.observer.first_pcm"]["offset_ns"])
