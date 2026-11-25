from pathlib import Path
import tempfile
import unittest
from e2e.host.fixture_assets import make_fixture,extract_local_control
from e2e.host.paired_evidence import qualify_common

class PairedContracts(unittest.TestCase):
    def test_literal_extracted_member(self):
        media,assets=make_fixture()
        with tempfile.TemporaryDirectory() as td:
            p=extract_local_control(Path(td),assets,media)
            self.assertEqual(p.read_bytes(),media)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                extract_local_control(Path(td),assets,b"wrong")

    def test_same_boundary_not_decoder_marker(self):
        def event(name,source,tick,metrics):
            return dict(event=name,source=source,run_id="a"*32,scenario_id="fixtureplay",
                        attempt_id="fixtureplay-0001",pid=42,clock=dict(domain="win-qpc:"+"a"*32,
                        frequency_hz=10000000,tick=tick),metrics=metrics)
        start=event("robot.play.intent","robot",100,{})
        common=event("component.observer.first_pcm","component",200,{"sample_count":480})
        decoder=event("component.decoder.first_pcm","component",150,{"sample_count":480})
        self.assertEqual(qualify_common([start,common],local=True)["duration_ns"],10000)
        self.assertTrue(qualify_common([start,decoder,common],local=False)["qualified"])
        self.assertFalse(qualify_common([start,decoder],local=False)["qualified"])
        self.assertFalse(qualify_common([start,common,common],local=True)["qualified"])
        self.assertFalse(qualify_common([start,common,decoder],local=True)["qualified"])
