"""No GUI: verify bounded synthetic archive fixtures and strict playback gate."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from e2e.host.fixture_assets import make_fixture, write_reference, MEMBER
from e2e.host.fixture_evidence import qualify


class FixtureContracts(unittest.TestCase):
    def test_zip_assets_are_independent_but_decode_to_same_pcm(self):
        media, assets = make_fixture()
        self.assertEqual(set(assets), {"fixture-zip-store.zip", "fixture-zip-deflate.zip"})
        for name, data in assets.items():
            with self.subTest(name=name), zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
                self.assertEqual(archive.read(MEMBER), media)
                self.assertEqual(archive.getinfo(MEMBER).compress_type,
                                 zipfile.ZIP_STORED if "store" in name else zipfile.ZIP_DEFLATED)
                self.assertGreater(len(archive.read("padding.bin")), len(media))
        self.assertEqual(make_fixture()[1], assets)

    def test_reference_is_synthetic_and_has_digest_manifest(self):
        media, assets = make_fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = write_reference(root, "http://127.0.0.1:43210",
                                     "fixture-zip-deflate", media, assets["fixture-zip-deflate.zip"])
            self.assertIn("DOUJIN-TRACK 1", target.read_text(encoding="utf-8"))
            self.assertIn("MEMBER=disc/Remote tone.wav", target.read_text(encoding="utf-8"))
            manifest = json.loads((root/"fixture-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio_sha256"], sha256(media).hexdigest())
            self.assertFalse(manifest["host_qualified"])
            with self.assertRaises(ValueError):
                write_reference(root, "http://127.0.0.1:43210",
                                "fixture-zip-deflate", media, assets["fixture-zip-deflate.zip"])
            with self.assertRaises(ValueError):
                write_reference(root, "https://example.net",
                                "fixture-zip-store", media, assets["fixture-zip-store.zip"])

    def test_missing_native_milestone_or_range_never_passes(self):
        base = {"status": "recorded",
                "event_names": ["robot.host.launch.intent", "robot.host.window.visible",
                                "robot.play.intent", "component.track.open.begin",
                                "component.decoder.first_pcm"],
                "latencies": [{"metric": "play.intent_to_first_pcm",
                               "observations": [{"status": "measured", "duration_ns": 1000}]}]}
        ok = [{"status": 206, "bytes_sent": 512}]
        self.assertTrue(qualify(base, ok)["qualified"])
        self.assertFalse(qualify(base, [])[ "qualified"])
        incomplete = dict(base)
        incomplete["event_names"] = ["robot.play.intent"]
        self.assertFalse(qualify(incomplete, ok)["qualified"])
        ambiguous = dict(base)
        ambiguous["latencies"] = [{"metric": "play.intent_to_first_pcm",
                                  "observations": [
                                      {"status": "measured", "duration_ns": 20},
                                      {"status": "measured", "duration_ns": 5}]}]
        self.assertFalse(qualify(ambiguous, ok)["qualified"])
        self.assertFalse(qualify(base, [{"status": 200, "bytes_sent": 999}])["qualified"])
        self.assertFalse(qualify(base, [{"status": 206, "bytes_sent": -1}])["qualified"])


if __name__ == "__main__":
    unittest.main()
