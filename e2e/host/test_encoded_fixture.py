import struct
import tempfile
import unittest
from pathlib import Path
from e2e.host.encoded_fixture import load_cases,read_pcm,compare,FRAMES,write_reference,PROFILES

class EncodedFixtureTests(unittest.TestCase):
    def test_frozen_assets(self):
        cases=list(load_cases(Path(".")))
        self.assertEqual([c.ident for c in cases],["mp3-id3v23","aac-mp4"])
        for case in cases:
            with tempfile.TemporaryDirectory() as tmp:
                for profile in PROFILES:
                    folder=Path(tmp)/profile
                    folder.mkdir()
                    path=write_reference(folder,"http://127.0.0.1:1234",profile,case)
                    if profile=="fixture-local":
                        self.assertEqual(path.read_bytes(),case.media)
                    else:
                        self.assertIn("FILENAME=Remote tone."+case.extension,path.read_text())
    def test_capture_and_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=(Path(tmp)/name for name in ("a.bin","b.bin"))
            data=struct.pack("<4I",0x314d4350,16000,1,FRAMES)+struct.pack("<"+"d"*FRAMES,*[i/FRAMES for i in range(FRAMES)])
            a.write_bytes(data); b.write_bytes(data)
            self.assertTrue(compare(a,b)["qualified"])
            b.write_bytes(data[:-8]+struct.pack("<d",0.25))
            self.assertFalse(compare(a,b)["qualified"])
            b.write_bytes(data[:-1])
            with self.assertRaises(ValueError): read_pcm(b)
            b.write_bytes(data[:-8]+struct.pack("<d",float("nan")))
            with self.assertRaises(ValueError): read_pcm(b)
