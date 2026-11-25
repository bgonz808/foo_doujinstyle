"""Regression and negative tests for the isolated media/HTTP/ZIP fixture lab."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from http.client import IncompleteRead, HTTPConnection
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZIP_BZIP2, ZipFile
import os
import shutil
from unittest.mock import patch
import re

from tools.media_matrix import (
    CLIENT, TITLE, create_fixtures, corrupted_zip, digest, fixture_server,
    generated_pcm, http_get, probe_transport, validate_matrix, zip_bytes
)


ROOT = Path(__file__).resolve().parent.parent


class MediaMatrixTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.matrix = json.loads((ROOT / "quality/media-support.json").read_text(encoding="utf-8"))

    def fixture(self, folder="assets", include_ffmpeg=False):
        return create_fixtures(self.root / folder, self.matrix, include_ffmpeg=include_ffmpeg)

    def test_declared_audio_suffixes_match_native_inventory(self):
        """No misleading format claims when native recognition changes."""
        text = (ROOT / "native/zip_inventory.hpp").read_text(encoding="utf-8")
        match = re.search(r'constexpr std::array audio = \{(.*?)\};', text, re.S)
        self.assertIsNotNone(match)
        extensions = re.findall(r'"\.(.*?)"', match.group(1))
        self.assertEqual(["." + x for x in extensions], self.matrix["audio_candidate_extensions"])
        native_zip = (ROOT / "native/zip_catalog.hpp").read_text(encoding="utf-8")
        self.assertRegex(native_zip, r"method != 0 && method != 8")
        self.assertEqual(self.matrix["supported_remote_archive_methods"],
                         ["zip-stored", "zip-deflate"])
        self.assertIn(".webm", self.matrix["extension_gaps_for_host_review"])

    def test_pcm_fixture_is_identical_across_runs(self):
        self.assertEqual(generated_pcm(), generated_pcm())
        left, right = self.fixture("a"), self.fixture("b")
        self.assertEqual(left["assets"], right["assets"])
        self.assertEqual(left["cases"], right["cases"])
        self.assertEqual((self.root / "a" / "manifest.json").read_bytes(),
                         (self.root / "b" / "manifest.json").read_bytes())
        self.assertEqual(left["toolchain"]["ffmpeg"], None)
        self.assertEqual(left["audio_markers"]["frequencies_hz"], [220, 440, 880, 1760])
        self.assertTrue(left["pr_head_commit"] is None or
                        re.fullmatch(r"[a-fA-F0-9]{40}", left["pr_head_commit"]))
        self.assertEqual(left["qualification"], "not-foobar2000-host-qualified")

    def test_unicode_names_and_archive_roundtrip(self):
        record = self.fixture()
        direct = (self.root / "assets" / "pcm-wav.wav").read_bytes()
        for method in ("zip-stored", "zip-deflate"):
            zipfile_path = self.root / "assets" / f"pcm-wav-{method}.zip"
            with ZipFile(zipfile_path) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), 2)
                self.assertTrue(names[0].startswith("音源/"))
                self.assertTrue(archive.getinfo(names[0]).flag_bits & 0x800)
                self.assertEqual(archive.read(names[0]), direct)
                self.assertIn(TITLE, archive.read("説明/README.txt").decode("utf-8"))
        self.assertEqual(record["assets"]["direct:pcm-wav"]["sha256"], digest(direct))

    def test_crc_corruption_is_rejected(self):
        self.fixture()
        with ZipFile(self.root / "assets" / "bad-crc.zip") as archive:
            with self.assertRaises(BadZipFile):
                archive.read("音源/pcm-wav.wav")

    def test_bzip2_is_generated_as_unsupported_not_supported(self):
        record = self.fixture()
        with ZipFile(self.root / "assets" / "unsupported-bzip2.zip") as archive:
            self.assertEqual(archive.getinfo("音源/unsupported.wav").compress_type, ZIP_BZIP2)
        self.assertEqual(record["assets"]["negative:bzip2"]["expected"],
                         "not-supported-by-native-remote-reader")
        self.assertNotIn("zip-bzip2", self.matrix["supported_remote_archive_methods"])

    def test_loopback_ranges_and_conditional_requests(self):
        asset = b"0123456789" * 150
        with fixture_server({"s": asset}) as (url, events):
            status, headers, data = http_get(url, "s", headers={"Range": "bytes=10-19"})
            self.assertEqual(status, 206)
            self.assertEqual(data, asset[10:20])
            self.assertEqual(headers["Content-Range"], f"bytes 10-19/{len(asset)}")
            status, headers, data = http_get(url, "s", headers={"Range": "bytes=-6"})
            self.assertEqual(data, asset[-6:])
            self.assertEqual(status, 206)
            self.assertTrue(events.wait_count(2))
            self.assertEqual(len(events), 2)
            self.assertEqual(sum(x["bytes_sent"] for x in events), 16)
            _, headers, _ = http_get(url, "s")
            status, _, full = http_get(url, "s", headers={
                "Range": "bytes=0-2", "If-Range": headers["ETag"]})
            self.assertEqual((status, full), (206, asset[:3]))
            status, _, full = http_get(url, "s", headers={
                "Range": "bytes=0-2", "If-Range": '"stale"'})
            self.assertEqual((status, full), (200, asset))
            with self.assertRaises(HTTPError) as exc:
                http_get(url, "s", headers={"Range": "bytes=999999-"})
            self.assertEqual(exc.exception.code, 416)

    def test_fault_modes_and_redirect_never_leave_loopback(self):
        asset = b"ABCDEFG" * 100
        with fixture_server({"s": asset}) as (url, events):
            status, _, data = http_get(url, "s", mode="redirect")
            self.assertEqual((status, data), (200, asset))
            self.assertEqual(events[0]["status"], 302)
            status, _, data = http_get(url, "s", mode="ignore-range",
                                       headers={"Range": "bytes=0-2"})
            self.assertEqual((status, data), (200, asset))
            status, headers, data = http_get(url, "s", mode="etag-change",
                                              headers={"Range": "bytes=0-2"})
            self.assertEqual(headers["ETag"], '"rotated"')
            self.assertEqual(status, 200)
            status, _, data = http_get(url, "s", mode="close")
            self.assertEqual((status, data), (200, asset))
            for mode, expected in (("rate-limit", 429), ("unavailable", 503)):
                with self.assertRaises(HTTPError) as err:
                    http_get(url, "s", mode=mode)
                self.assertEqual(err.exception.code, expected)
            with self.assertRaises((IncompleteRead, ConnectionError, OSError)):
                http_get(url, "s", mode="truncate")
        with self.assertRaisesRegex(ValueError, "Only loopback"):
            http_get("https://example.org", "s")

    def test_connection_reuse_cross_asset_and_server_close(self):
        asset = b"0123456789" * 100
        with fixture_server({"a": asset, "b": asset}, latency_ms=15,
                            connection_delay_ms=30, close_after=3) as (url, events):
            u = urlsplit(url)
            c = HTTPConnection(u.hostname, u.port, timeout=3)
            try:
                for key in ("a", "b", "a", "b"):
                    c.request("GET", "/asset/" + key, headers={"Range":"bytes=5-14"})
                    response = c.getresponse()
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), asset[5:15])
                self.assertTrue(events.wait_count(4))
                rows = list(events)
                self.assertEqual([x["connection_id"] for x in rows], [0,0,0,1])
                self.assertEqual([x["connection_request"] for x in rows], [1,2,3,1])
                self.assertEqual([x["connection_reused"] for x in rows], [False,True,True,False])
                self.assertEqual([x["connection_closing"] for x in rows], [False,False,True,False])
                self.assertEqual([x["injected_delay_ms"] for x in rows], [45,15,15,45])
                for x in rows:
                    self.assertGreaterEqual(x["headers_sent_ns"]-x["start_ns"], x["injected_delay_ms"]*1_000_000)
                    self.assertGreaterEqual(x["end_ns"], x["headers_sent_ns"])
            finally:
                c.close()

    def test_limits_and_invalid_config_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsafe or unbounded"):
            with fixture_server({"s": b"x" * (4 * 1024 * 1024 + 1)}):
                pass
        with self.assertRaisesRegex(ValueError, "Unsafe fixture fault"):
            with fixture_server({"s": b"x"}, bps=1):
                pass
        with self.assertRaisesRegex(ValueError, "exceeds"):
            zip_bytes("a.wav", b"a" * (4 * 1024 * 1024 + 1), 0)
        doc = dict(self.matrix)
        doc["supported_remote_archive_methods"] = ["zip-stored", "zip-deflate", "rar"]
        with self.assertRaisesRegex(ValueError, "Archive support"):
            validate_matrix(doc)

    def test_transport_baseline_aligned_to_same_encoded_audio(self):
        manifest = self.fixture()
        out = probe_transport(self.root / "assets", manifest)
        self.assertEqual(len(out["requests"]), 9)
        self.assertTrue(all(r["status"] == 200 for r in out["requests"]))
        self.assertEqual(out["case_count"], 1)
        self.assertEqual(len(out["trace"]), 9)
        self.assertEqual(len(out["pairs"]), 6)
        self.assertTrue(all(x["status"] == "fixture-bytes-verified" for x in out["trace"]))
        self.assertEqual({p["baseline"] for p in out["pairs"]},
                         {"direct-same-encoded-audio-bytes"})
        self.assertIsNone(out["first_audio_ms"])
        self.assertIsNone(out["seek_to_audio_ms"])
        self.assertIsNone(out["native_cpu"])
        self.assertEqual(out["repetitions"], 3)
        self.assertEqual(len(out["aggregates"]), 3)
        self.assertTrue(all(row["p95_ms"] is None for row in out["aggregates"]))
        with self.assertRaisesRegex(ValueError, "Bounded repetitions"):
            probe_transport(self.root / "assets", manifest, repeats=0)
        self.assertTrue((self.root / "assets" / "transport-results.json").is_file())

    def test_frozen_corpus_preserves_full_matrix_without_tools(self):
        lock_path = ROOT / "quality/audio-fixtures/lock.json"
        with patch("tools.media_matrix.subprocess.run", side_effect=AssertionError("Encoder invoked")), \
             patch("tools.media_matrix.shutil.which", side_effect=AssertionError("Tool searched")):
            manifest = create_fixtures(self.root / "frozen", self.matrix,
                                       include_ffmpeg=False, fixture_lock=lock_path)
        self.assertEqual(manifest["fixture_mode"], "frozen-audio-cas")
        self.assertIsNone(manifest["toolchain"]["ffmpeg"])
        self.assertEqual(manifest["fixture_provenance"]["probe_status"], "recorded-at-origin-not-rerun")
        self.assertTrue(all(c["status"] == "generated" for c in manifest["cases"]))
        self.assertEqual(len(manifest["cases"]), len(self.matrix["codec_container_fixtures"]))
        self.assertEqual(len(manifest["assets"]), len(manifest["cases"]) * 3 + 2)
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for ident, entry in lock["cases"].items():
            self.assertEqual(manifest["assets"]["direct:" + ident]["sha256"], entry["sha256"])
            for route in ("zip-stored", "zip-deflate"):
                asset = manifest["assets"][route + ":" + ident]
                with ZipFile(self.root / "frozen" / asset["path"]) as arc:
                    self.assertEqual(digest(arc.read(asset["member"])), entry["sha256"])
        result = probe_transport(self.root / "frozen", manifest)
        n = sum(c["classification"] == "audio_candidate" for c in manifest["cases"])
        self.assertEqual(result["case_count"], n)
        self.assertEqual(len(result["trace"]), n * 9)

    def test_frozen_corpus_fails_closed_on_mutation(self):
        src = ROOT / "quality/audio-fixtures"
        dest = self.root / "corpus"
        shutil.copytree(src, dest)
        lock_path = dest / "lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        blob = dest / (next(iter(lock["cases"].values()))["sha256"] + ".bin")
        data = blob.read_bytes()
        blob.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            create_fixtures(self.root / "bad", self.matrix, include_ffmpeg=False, fixture_lock=lock_path)
        blob.unlink()
        with self.assertRaisesRegex(ValueError, "Missing or invalid"):
            create_fixtures(self.root / "missing", self.matrix, include_ffmpeg=False, fixture_lock=lock_path)
        self.matrix["codec_container_fixtures"][1]["args"].append("-changed")
        with self.assertRaisesRegex(ValueError, "recipe mismatch"):
            create_fixtures(self.root / "stale", self.matrix, include_ffmpeg=False, fixture_lock=lock_path)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            create_fixtures(self.root / "both", self.matrix, include_ffmpeg=True, fixture_lock=lock_path)

    @unittest.skipUnless(os.getenv("MEDIA_MATRIX_REGENERATE") == "1" and
                         shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Explicit regeneration mode required")
    def test_optional_codec_container_generation_records_ffprobe_identity(self):
        # Focused FFmpeg smoke; full matrix runs in the explicit CI matrix command.
        self.matrix["codec_container_fixtures"] = [
            row for row in self.matrix["codec_container_fixtures"]
            if row["id"] in {"pcm-wav", "flac-native", "mp3-id3v23", "opus-ogg"}]
        result = self.fixture(include_ffmpeg=True)
        for case in result["cases"]:
            self.assertEqual(case["status"], "generated", case.get("reason"))
            self.assertEqual(case["classification"], "audio_candidate")
            if case["tier"] != "required":
                self.assertEqual(case["probe"]["codec"], case["codec"])
        for case in result["cases"]:
            if case["id"] in {"flac-native", "mp3-id3v23", "opus-ogg"}:
                self.assertEqual(case["probe"]["tags"].get("title"), TITLE)
        self.assertRegex(result["toolchain"]["ffmpeg_binary_sha256"], r"^[a-f0-9]{64}$")
        self.assertIn("ffmpeg version", result["toolchain"]["ffmpeg"])
        self.assertEqual(len(result["assets"]), 4 * 3 + 2)


if __name__ == "__main__":
    unittest.main()
