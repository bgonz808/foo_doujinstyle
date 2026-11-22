from base64 import urlsafe_b64encode
from contextlib import closing
import gzip
from email.message import Message
import io
import json
import os
import random
import re
import sqlite3
import struct
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from catalog_cache import CatalogCache, destination_key, restore_snapshot, wal_supported
from doujin import (
    Http, RangedObject, archive_destination, ds_url, event, export_records, inspect_zip, navigation_referrer,
    parse_detail, parse_listing, parse_popular, release_catalog_candidates, resolve_offer, run_catalog_jobs, sample_flac_member,
    sidecar_values,
)
from native.search_worker import (
    ARCHIVE_BYTES, CHART_TTL, COVER_BYTES, GUIDES_URL, IDLE_BROWSE, IDLE_TAGS, RANGE_REQUESTS, cache_cover, check_url, download_archive,
    cached_tags, index_results, offer_status, repair_release, result_record, run_idle_layers, run_popular, run_search, size_text,
    chart_refresh_lock, prefetch_charts,
)
from native.catalog_join import enrich_releases
from native.export_library import archive_album_artist, choose_release_date, export, make_descriptor, write_reference
from native.idle_backfill import idle_backfill
from native.reference_publisher import NativeCatalogWorkerUnavailable, ReferencePublisher


class RangeServer(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/offer":
            self.send_error(404)
            return
        self.send_response(302)
        target = "https://www.mediafire.com/file/abc/Album+〜%20Mix.zip/file?x=%2B&name=曲"
        self.send_header("Location", target.encode("utf-8").decode("latin-1"))
        self.end_headers()

    def do_GET(self):
        if self.path == "/session-share":
            self.send_response(302)
            self.send_header("Set-Cookie", "share=ready; Path=/")
            self.send_header("Location", "/session-download")
            self.end_headers()
            return
        if self.path == "/private-share":
            self.send_response(302)
            self.send_header("Location", "/session-destination")
            self.send_header("Referrer-Policy", "origin, no-referrer")
            self.end_headers()
            return
        if self.path == "/session-download":
            self.server.download_headers = dict(self.headers)
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/session-destination")
            self.end_headers()
            return
        if self.path == "/session-destination":
            self.server.destination_headers = dict(self.headers)
            self.send_response(200)
            self.end_headers()
            return
        contents = self.server.contents
        if self.path == "/range-ignored":
            self.send_response(200)
            self.send_header("Content-Length", str(len(contents)))
            self.end_headers()
            return
        request = self.headers["Range"]
        match = re.fullmatch(r"bytes=(\d*)-(\d+)", request)
        if not match:
            self.send_error(416)
            return
        start, end = match.groups()
        size = len(contents)
        start, end = (int(start), min(int(end), size - 1)) if start else (max(0, size - int(end)), size - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Disposition", f'attachment; filename="{self.server.filename}"')
        self.send_header("ETag", '"test-archive"')
        self.end_headers()
        self.wfile.write(contents[start:end + 1])

    def log_message(self, *args):
        pass


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        buffer = io.BytesIO()
        rate, samples = 96000, 192000
        field = (rate << 44) | (1 << 41) | (23 << 36) | samples
        flac = b"fLaC\x80\0\0\x22" + bytes(10) + field.to_bytes(8, "big") + bytes(16)
        random_bytes = random.Random(23)
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Disc A/01.FLAC", flac + random_bytes.randbytes(184_320))
            archive.writestr("Disc A/02.ogg", random_bytes.randbytes(524_288), compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("[ABCDCD-001] Release/RELEASE.NFO",
                             "Album Artist: Various Artists\nPublisher: Test Records\nCatalog ID: ABCDCD-001\n",
                             compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("00 album.cue", 'PERFORMER "Compilation Artist"\nTITLE "Album"\nFILE "file.wav" WAVE\n')
        cls.zip_bytes = buffer.getvalue()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.contents = self.zip_bytes
        self.server.filename = "arbitrary name.bin"

    def test_catalog_and_audio_header_with_partial_reads(self):
        http = Http()
        result = inspect_zip(http, self.url + "/arbitrary.ext", 65_536, sample_flac=True)
        self.assertEqual(result["format"], "zip")
        self.assertEqual(result["response_filename"], "arbitrary name.bin")
        self.assertEqual(result["audio_members"], 2)
        self.assertEqual(result["sidecars"][0]["metadata"]["publisher"], "Test Records")
        self.assertEqual(result["catalog_id_candidates"], ["ABCDCD-001"])
        self.assertEqual(result["audio_sample"]["metadata"], {
            "codec": "FLAC", "sample_rate_hz": 96000, "channels": 2,
            "bits_per_sample": 24, "duration_seconds": 2.0,
        })
        self.assertLess(result["archive_http"]["body_bytes"], 50_000)
        self.assertTrue(all(request["status"] == 206 for request in http.log))

    def test_explicit_header_pass_covers_every_member_past_eight(self):
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as original:
            flac = original.read(next(name for name in original.namelist() if name.lower().endswith(".flac")))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for number in range(12):
                archive.writestr(f"{number:02}.flac", flac)
        self.server.contents = buffer.getvalue()
        report = inspect_zip(Http(), self.url + "/archive", 32_768, directory_only=True,
                             header_probes=True, max_requests=5)
        self.assertTrue(report["header_probe_pass_complete"])
        self.assertEqual(len(report["audio_probes"]), 12)
        self.assertEqual(report["audio_probes"]["11.flac"]["metadata"]["codec"], "FLAC")
        self.assertGreater(report["archive_http"]["requests"], 5)

    def test_repair_refreshes_incomplete_references_in_place(self):
        with zipfile.ZipFile(io.BytesIO(self.zip_bytes)) as original:
            flac = original.read(next(name for name in original.namelist() if name.lower().endswith(".flac")))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for number in range(1, 13):
                archive.writestr(f"{number:02}.flac", flac)
        self.server.contents = buffer.getvalue()
        url = self.url + "/archive"
        previous = inspect_zip(Http(), url, 262144, directory_only=True, header_probes=True)
        previous["audio_probes"] = dict(list(previous["audio_probes"].items())[:8])
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        release = {"id": 25, "album": "Fixture", "url": ds_url(p="page", type=1, id=25)}
        copy_id = cache.add_copy(release, {}, "https://download.mediafire.com/example.zip")
        cache.put_archive(copy_id, previous, None)
        cache.put_offers(25, [("doujinstyle", {}, "https://download.mediafire.com/example.zip")], None)
        logs = []
        actual_http = Http()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            export(cache, output, [25])
            files = {path.name: path.read_text("utf-8") for path in output.glob("*.djt")}
            track_9, = [name for name, text in files.items() if "MEMBER=09.flac\n" in text]
            self.assertNotIn("DURATION_MS=", files[track_9])
            with patch("native.search_worker.inspect_zip",
                       side_effect=lambda _, __, *args, **kwargs: inspect_zip(actual_http, url, *args, **kwargs)):
                index_results(object(), cache, {25: release}, output, logs.append,
                              emit_reference=lambda *args: None)
                self.assertTrue(any(line == f"\x1925\t{track_9}" for line in logs))
                repair_release(object(), cache, 25, output, logs.append)
            self.assertEqual(set(files), {path.name for path in output.glob("*.djt")})
            repaired = (output / track_9).read_text("utf-8")
            self.assertIn("CODEC=FLAC", repaired)
            self.assertIn("DURATION_MS=2000", repaired)
            self.assertTrue(any(line == f"\x1c25\t{track_9}" for line in logs))
            self.assertGreater(sum(record["body_bytes"] for record in actual_http.log), 0)

    def test_repair_refreshes_rejected_share_and_keeps_track_identity(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        release = {"id": 25, "album": "Fixture", "url": ds_url(p="page", type=1, id=25)}
        old_url = "https://www.mediafire.com/file/abc/Album%C3%A3%C2%80%C2%9C.zip/file"
        new_url = "https://www.mediafire.com/file/abc/Album%E3%80%9C.zip/file"
        offer = {"host": "www.mediafire.com", "format_hint": "zip"}
        copy_id = cache.add_copy(release, offer, old_url)
        cache.put_offers(25, [("doujinstyle", offer, old_url)], None)
        archive = inspect_zip(Http(), self.url + "/archive", 262144, directory_only=True, header_probes=True)
        cache.put_archive(copy_id, archive, None)
        failure = HTTPError(old_url, 403, "Forbidden", {}, None)
        calls = []

        def inspect(http, url, *args, **kwargs):
            calls.append(url)
            if url == old_url:
                raise failure
            self.assertEqual(url, new_url)
            return archive

        def refresh(*args):
            cache.put_offers(25, [("doujinstyle", offer, new_url)], None)

        with tempfile.TemporaryDirectory() as temp, patch("native.search_worker.inspect_zip", inspect), \
                patch("native.search_worker.discover_offers") as discover:
            output = Path(temp)
            export(cache, output, [25])
            files = {path.name for path in output.glob("*.djt")}
            self.assertTrue(files)
            with self.assertRaisesRegex(ValueError, "no replacement offer"):
                repair_release(object(), cache, 25, output, lambda _: None)
            self.assertEqual(calls, [old_url])
            self.assertEqual(discover.call_count, 1)
            for name in files:
                self.assertIn(f"URL={old_url}\n", (output / name).read_text("utf-8"))
            discover.side_effect = refresh
            repair_release(object(), cache, 25, output, lambda _: None)
            self.assertEqual(calls, [old_url, old_url, new_url])
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(files, {path.name for path in output.glob("*.djt")})
            self.assertEqual(cache.copies_for(25)[0]["copy_id"], copy_id)
            for name in files:
                self.assertIn(f"URL={new_url}\n", (output / name).read_text("utf-8"))

    def test_unprobed_member_inherits_consistent_album_artist(self):
        release = {"id": 25, "album": "About What You Think Is Me", "artist": "Dochakuso Records (pencil)"}
        archive = {"audio_probes": {"01.flac": {"tags": {"ALBUMARTIST": ["pencil"]}}}}
        text = make_descriptor(release, {"path": "09.flac"}, "https://example.invalid/album.zip", archive, [],
                               album_artist=archive_album_artist(archive))
        self.assertIn("ALBUM ARTIST=pencil\n", text)
        self.assertNotIn("ARTIST=Dochakuso", text)
        archive["audio_probes"]["02.flac"] = {"tags": {"ALBUMARTIST": ["Other Artist"]}}
        self.assertIsNone(archive_album_artist(archive))

    def test_archive_download_streams_without_replacing_existing_file(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        release = {"id": 25, "album": "Fixture", "url": ds_url(p="page", type=1, id=25)}
        url = "https://download.mediafire.com/example.zip"
        cache.add_copy(release, {"filename": "example.zip"}, url)
        headers = Message()
        headers["Content-Length"] = str(len(self.zip_bytes))

        def open_archive(target, source, policy):
            self.assertEqual(target, url)
            self.assertEqual(source, release["url"])
            response = io.BytesIO(self.zip_bytes)
            response.status = 200
            response.headers = headers
            return response

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            existing = output / "example.zip"
            existing.write_bytes(b"existing user file")
            saved = download_archive(SimpleNamespace(open_archive=open_archive), cache, 25, output, lambda *_: None)
            self.assertEqual(saved.name, "example (2).zip")
            self.assertEqual(saved.read_bytes(), self.zip_bytes)
            self.assertEqual(existing.read_bytes(), b"existing user file")
            self.assertEqual(list(output.glob("*.part")), [])

    def test_mega_download_verifies_plaintext_before_publishing(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            self.skipTest("Portable MEGA fixture requires the cryptography test dependency")

        key, nonce = bytes(range(16)), bytes(range(16, 24))
        plain = self.zip_bytes
        encode = lambda data: urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        def encrypt(value, mode):
            encoder = Cipher(algorithms.AES(key), mode).encryptor()
            return encoder.update(value) + encoder.finalize()

        # Generate fixture ciphertext with an independent CTR implementation and
        # fold the fixture's three MEGA chunk MACs using a single CBC operation.
        encrypted = encrypt(plain, modes.CTR(nonce + bytes(8)))
        macs = b""
        for chunk in (plain[:131072], plain[131072:393216], plain[393216:]):
            macs += encrypt(chunk + bytes(-len(chunk) % 16), modes.CBC(nonce * 2))[-16:]
        tail = encrypt(macs, modes.CBC(bytes(16)))[-16:]
        a, b, c, d = struct.unpack(">4I", tail)
        extras = nonce + struct.pack(">2I", a ^ b, c ^ d)
        token = encode(bytes(x ^ y for x, y in zip(key, extras)) + extras)
        attribute = b'MEGA{"n":"pencil.zip"}'
        attribute = encode(encrypt(attribute + bytes(-len(attribute) % 16), modes.CBC(bytes(16))))
        endpoint = "https://gfs123.userstorage.mega.co.nz/token"
        served = encrypted
        calls = []

        def serve(request, timeout):
            calls.append(request.full_url)
            self.assertNotIn(token, request.full_url)
            if request.get_method() == "POST":
                self.assertEqual(json.loads(request.data), [{"a": "g", "p": "PublicID", "g": 1, "ssl": 2}])
                return io.BytesIO(json.dumps([{"g": endpoint, "s": len(plain), "at": attribute}]).encode())
            if request.full_url != endpoint:
                self.assertTrue(request.full_url.startswith(endpoint + "/"))
                low, high = map(int, request.full_url.removeprefix(endpoint + "/").split("-"))
                data = served[low:high + 1]
            else:
                data = served
            response = io.BytesIO(data)
            response.status = 200
            response.headers = {"Content-Length": str(len(data) if request.full_url != endpoint else len(plain))}
            return response

        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        links = (f"https://mega.nz/file/PublicID#{token}", f"https://mega.nz/#!PublicID!{token}",
                 f"https://mega.nz/folder/FolderID#{token}")
        for identifier, link in enumerate(links, 25):
            cache.add_copy({"id": identifier, "album": "Fixture", "url": ds_url(id=identifier)}, {}, link)
        logs = []
        with tempfile.TemporaryDirectory() as temp, patch("native.mega_download.build_opener",
                                                         return_value=SimpleNamespace(open=serve)):
            output = Path(temp)
            for identifier, filename in ((25, "pencil.zip"), (26, "pencil (2).zip")):
                saved = download_archive(object(), cache, identifier, output, logs.append)
                self.assertEqual(saved.name, filename)
                self.assertEqual(saved.read_bytes(), plain)
            self.assertEqual(len(calls), 4)
            self.assertNotIn(token, "\n".join(logs))
            share = links[0]
            report = inspect_zip(object(), share, 262144, directory_only=True, header_probes=True)
            self.assertEqual(report["format"], "zip")
            self.assertEqual(report["audio_probes"]["Disc A/01.FLAC"]["metadata"]["codec"], "FLAC")
            self.assertLess(report["archive_http"]["body_bytes"], len(plain))
            self.assertTrue(any(int(url.removeprefix(endpoint + "/").split("-")[0]) % 16
                                for url in calls if url.startswith(endpoint + "/")))
            copy_id = cache.copies_for(25)[0]["copy_id"]
            cache.put_archive(copy_id, report, None)
            self.assertEqual(export(cache, output, [25])["references"], 2)
            self.assertTrue(all(share in path.read_text() for path in output.glob("*.djt")))
            def folder_release(url, **kwargs):
                return {}, b"<mainbar><h2>Folder fixture</h2></mainbar>", url

            with self.assertRaisesRegex(ValueError, "no supported MediaFire or public MEGA file offer"):
                download_archive(SimpleNamespace(get=folder_release), cache, 27, output, logs.append)
            served = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with self.assertRaisesRegex(ValueError, "verification failed"):
                download_archive(object(), cache, 25, output, logs.append)
            served = encrypted[:-1]
            with self.assertRaisesRegex(ValueError, "advertised length"):
                download_archive(object(), cache, 25, output, logs.append)
            endpoint = "https://archive.test.invalid/token"
            with self.assertRaisesRegex(ValueError, "unrecognized HTTPS storage endpoint"):
                download_archive(object(), cache, 25, output, logs.append)
            self.assertEqual({p.name for p in output.glob("*.zip")}, {"pencil.zip", "pencil (2).zip"})
            self.assertEqual(list(output.glob("*.part")), [])

    def test_pencil_style_release_metadata_fills_genre_and_separates_dates(self):
        member = {"path": "01.flac"}
        pencil = {"id": 26802, "album": "about what you think is me", "listed_at": "2024-11-29",
                  "tags": ["M3-54", "Electronic", "Breakcore", "Hardcore", "J-Core", "Vocal", "DnB", "breakcore"]}
        data = make_descriptor(pencil, member, "https://example.invalid/album.zip", {}, [])
        genres = [line for line in data.splitlines() if line.startswith("GENRE=")]
        self.assertEqual(genres, ["GENRE=Electronic", "GENRE=Breakcore", "GENRE=Hardcore", "GENRE=J-Core", "GENRE=DnB"])
        self.assertIn("RELEASE EVENT=M3-54\n", data)
        self.assertEqual(event("M3-54")["start"], "2024-10-27")
        self.assertIn("DOUJINSTYLE TAG=Vocal\n", data)
        self.assertIn("DOUJINSTYLE DATE ADDED=2024-11-29\n", data)
        self.assertIn("\nDATE=2024-10-27\n", data)
        self.assertIn("\nDATE SOURCE=Release event M3-54\n", data)
        archive = {"audio_probes": {"01.flac": {"tags": {"GENRE": ["Footwork", "Juke"], "DATE": ["2024"],
                                                         "ALBUM": ["Embedded Album"]}}}}
        merged = make_descriptor(pencil, member, "https://example.invalid/album.zip", archive, [])
        genres = [line for line in merged.splitlines() if line.startswith("GENRE=")]
        self.assertEqual(genres, ["GENRE=Footwork", "GENRE=Juke"])
        self.assertIn("RELEASE GENRE=Breakcore\n", merged)
        self.assertIn("\nDATE=2024-10-27\n", merged)
        self.assertIn("\nALBUM=Embedded Album\n", merged)

    def test_size_display_selects_binary_units(self):
        self.assertEqual([size_text(n) for n in (0, 1023, 1024, 2**20, 2**30, 2**40)],
                         ["0 B", "1,023 B", "1.00 KiB", "1.00 MiB", "1.00 GiB", "1.00 TiB"])

    def test_site_date_fallback_and_event_codes(self):
        release = {"listed_at": "2025-03-09", "tags": ["RTS19", "J-pop", "Touhou"]}
        self.assertEqual(choose_release_date(release), ("2022-05-08", "Release event RTS19"))
        self.assertEqual(choose_release_date({**release, "tags": ["C108"]}),
                         ("2025-03-09", "DoujinStyle date added"))
        self.assertEqual(choose_release_date({**release, "tags": ["C108"]}, "2024"), (None, None))
        for value in ("RTS22", "ARTS13", "THK21", "TMKS14", "RTSSP2"):
            self.assertEqual(event(value)["id"], value)
        self.assertIsNone(event("J-pop"))

    def test_appledouble_sidecars_are_not_published_from_new_or_old_archive_indexes(self):
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("Disc/01.m4a", b"audio candidate")
            archive.writestr("__MACOSX/Disc/._01.m4a", b"appledouble")
            archive.writestr("Disc/._01.m4a", b"appledouble")
        self.server.contents = source.getvalue()
        report = inspect_zip(Http(), self.url + "/archive", 32768, directory_only=True)
        self.assertEqual(report["audio_members"], 1)
        self.assertEqual(report["member_types"]["archive_metadata"], 2)
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        copy = cache.add_copy({"id": 25, "album": "AppleDouble", "url": ds_url(id=25)}, {}, self.url + "/archive")
        for entry in report["inventory"]:
            entry["kind"] = "audio_candidate"  # Old cached inventory incorrectly counted all three.
        cache.put_archive(copy, report, None)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertEqual(export(cache, output, [25])["references"], 1)
            self.assertIn("MEMBER=Disc/01.m4a", next(output.glob("*.djt")).read_text())

    def test_popularity_rank_order_and_cached_listing_provenance(self):
        page = b'''<html><head><link rel="shortcut icon" href="/theme/site.png"></head><body>
          <h3>This Month</h3>
          <div><a href="/?p=page&amp;type=1&amp;id=25">Top track</a><span>Artist</span><b>1,280 downloads</b></div>
          <div><a href="/?p=page&amp;type=1&amp;id=24">Number two</a><span>Artist</span><b>47 downloads</b></div>
          <h3>All-Time</h3>
          <div><a href="/?p=page&amp;type=1&amp;id=33">All-time lead</a><span>Artist</span><b>13,820 downloads</b></div>
        </body></html>'''
        charts, icon = parse_popular(page)
        self.assertEqual(icon, "https://doujinstyle.com/theme/site.png")
        self.assertEqual([row["id"] for row in charts["month"]], [25, 24])
        self.assertEqual(charts["all"][0]["chart_downloads"], 13820)
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_listing({"id": 25, "album": "Top track", "artist": "Artist", "tags": ["Trance"]})
        get = SimpleNamespace(get=lambda url, **_: ({}, page, url))
        lines, icons = [], []
        with patch("native.search_worker.index_results") as index:
            run_popular(get, cache, "month", Path(), lambda *_: None,
                        emit_result=lambda row: lines.append(result_record(row)), emit_icon=icons.append)
            self.assertEqual(list(index.call_args.args[2]), [25, 24])
            self.assertIn("\t#1 · 1,280 downloads", lines[0])
            self.assertEqual(cache.listing(25)["tags"], ["Trance"])
            run_popular(object(), cache, "all", Path(), lambda *_: None)
            self.assertEqual(list(index.call_args.args[2]), [33])
        self.assertEqual(icons, [icon])
        for period, ids in (("month", [25, 24]), ("all", [33])):
            with self.subTest(period=period):
                seen, messages = [], []
                failure = NativeCatalogWorkerUnavailable("fixture worker closed its output")
                run_popular(get, None, period, Path(), messages.append, emit_result=lambda row: seen.append(row["id"]),
                            read_only_reason=failure)
                self.assertEqual(seen, ids)
                self.assertIn("Indexing unavailable", messages[-1])
                with patch.object(cache, "put_page", side_effect=failure):
                    seen.clear()
                    run_popular(get, cache, period, Path(), messages.append, refresh=True,
                                emit_result=lambda row: seen.append(row["id"]))
                    self.assertEqual(seen, ids)
                    self.assertIn("Indexing unavailable", messages[-1])

    def test_chart_prefetch_saves_both_lists_and_reuses_the_normal_indexer(self):
        page = b"""<html><body><h3>This Month</h3>
          <div><a href="/?p=page&amp;type=1&amp;id=25">First</a><b>801 downloads</b></div>
          <h3>All-Time</h3>
          <div><a href="/?p=page&amp;type=1&amp;id=25">First</a><b>1,200 downloads</b></div>
          <div><a href="/?p=page&amp;type=1&amp;id=26">Second</a><b>1,090 downloads</b></div>
          </body></html>"""
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_listing({"id": 25, "album": "Verified spelling", "tags": ["J-core"]})
        http = SimpleNamespace(get=lambda url, **_: ({}, page, url))
        with patch("native.search_worker.index_results") as index:
            prefetch_charts(http, cache, Path(), lambda *_: None)
            self.assertEqual(list(index.call_args.args[2]), [25, 26])
            self.assertEqual(index.call_args.kwargs["workers"], 1)
            self.assertEqual(cache.listing(25)["album"], "Verified spelling")
            self.assertEqual(cache.listing(25)["tags"], ["J-core"])
            self.assertNotIn("chart_rank", cache.listing(26))
            snapshot, fetched = cache.page_snapshot(GUIDES_URL)
            self.assertEqual(snapshot["charts"]["month"][0]["chart_downloads"], 801)
            self.assertEqual(snapshot["charts"]["all"][0]["chart_downloads"], 1200)

            # The same saved response is stale for monthly results and still fresh for all-time.
            moment = fetched + CHART_TTL["month"] + 1
            with patch("native.search_worker.time.time", return_value=moment):
                run_popular(object(), cache, "all", Path(), lambda *_: None)
                revised = page.replace(b"id=25\">First", b"id=27\">Third").replace(b"801 downloads", b"902 downloads")
                events = []

                def get(url, **unused):
                    self.assertEqual(events[0], (25,))
                    return {}, revised, url

                run_popular(SimpleNamespace(get=get), cache, "month", Path(), lambda *_: None,
                            emit_chart=lambda rows: events.append(tuple(rows)))
                self.assertEqual(events, [(25,), (27,)])
                self.assertEqual(cache.page_snapshot(GUIDES_URL)[1], moment)
                self.assertEqual(index.call_args.args[2][27]["album"], "Third")

                messages = []
                run_popular(SimpleNamespace(get=lambda url, **_: ({}, b"<html>bad page</html>", url)),
                            cache, "month", Path(), messages.append, refresh=True)
                self.assertIn("Showing saved rankings", messages[-1])
                self.assertEqual(cache.page_snapshot(GUIDES_URL)[0]["charts"]["month"][0]["id"], 27)

    def test_chart_fetch_lock_coalesces_catalog_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.sqlite"
            with closing(CatalogCache(path)) as first, closing(CatalogCache(path)) as second:
                with chart_refresh_lock(first) as acquired:
                    self.assertTrue(acquired)
                    with chart_refresh_lock(second) as acquired:
                        self.assertFalse(acquired)
                with chart_refresh_lock(second) as acquired:
                    self.assertTrue(acquired)

    def test_bangumi_enrichment_preserves_distinct_dates_and_normalizes_track_fields(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        listing = {"id": 26802, "url": ds_url(id=26802), "album": "about what you think is me",
                   "artist": "Dochakuso Records (pencil)", "tags": ["M3-54", "Breakcore"], "listed_at": "2024-11-29"}
        cache.put_listing(listing)
        candidates = [{"id": identifier, "name": listing["album"]} for identifier in (517537, 52)]

        def get(url, **options):
            self.assertTrue(url.startswith("https://api.bgm.tv/"))
            if "search/subjects" in url:
                self.assertEqual(json.loads(options["data"])["filter"], {"type": [3]})
                result = {"data": candidates}
            else:
                result = {"name": listing["album"], "type": 3, "date": "2024-10-27",
                          "infobox": [{"key": "艺术家", "value": [{"v": "pencil"}]},
                                      {"key": "厂牌", "value": "Dochakuso Records"},
                                      {"key": "发售日期", "value": "2024-10-27 (M3-54)"}]}
                if url.endswith("/52"):
                    result["infobox"][2]["value"] = "2024-10-27 (M3-55)"
            return {}, json.dumps(result).encode(), url

        records = []
        enrich_releases(cache, [26802], records.append, http=SimpleNamespace(get=get))
        self.assertEqual(records, ["  26802   Matched Bangumi album 517537"])
        fields = {"audio_probes": {"04.flac": {"tags": {"COMMENT": ["DCKSC-0004"], "DATE": ["2024"]}}}}
        saved = make_descriptor(cache.listing(26802), {"path": "04.flac"}, "https://example.invalid/archive.zip", fields, [])
        for tag in ("DATE=2024-10-27", "DOUJINSTYLE DATE ADDED=2024-11-29", "RELEASE EVENT=M3-54",
                    "LABEL=Dochakuso Records", "COMMENT=DCKSC-0004",
                    "BANGUMI URL=https://bgm.tv/subject/517537"):
            self.assertIn(tag, saved.splitlines())
        self.assertNotIn("CATALOGNUMBER=", saved)
        enrich_releases(cache, [26802], records.append, http=object())
        self.assertEqual(len(records), 1)

    def test_overlapping_catalog_signals_claim_one_lookup_across_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.sqlite"
            first, second = CatalogCache(path), CatalogCache(path)
            try:
                first.put_listing({"id": 25, "album": "Album", "artist": "Artist", "tags": ["M3-54"]})
                called = []

                def lookup(url, **options):
                    called.append(url)
                    # The second signal arrives while the winner has released the SQL lock
                    # but has a request in flight.
                    enrich_releases(second, [25], lambda *_: None, http=object())
                    return {}, b'{"data":[]}', url

                enrich_releases(first, [25, 25], lambda *_: None, http=SimpleNamespace(get=lookup))
                self.assertEqual(len(called), 1)
                self.assertEqual(first.db.execute("SELECT COUNT(*) FROM catalog_lookup_claims").fetchone()[0], 0)
                first.put_observation({"id": 25, "catalog_id": "TEST-0001"})
                enrich_releases(first, [25], lambda *_: None, http=SimpleNamespace(get=lookup))
                self.assertEqual(len(called), 2)
            finally:
                first.close()
                second.close()

    def test_non_flac_members_keep_the_native_decoder_extension(self):
        release = {"id": 25, "album": "Formats"}
        for extension in ("m4a", "mp3", "opus", "ogg", "wav", "ape"):
            filename = f"01.{extension}"
            data = make_descriptor(release, {"path": "Disc/" + filename}, "https://example.invalid/a.zip", {}, [])
            self.assertIn("FILENAME=" + filename + "\n", data)
            self.assertIn("MEMBER=Disc/" + filename + "\n", data)

    def test_idle_tag_layer_works_only_on_existing_zip_directories(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        for identifier in (25, 26):
            release = {"id": identifier, "album": "Fixture", "url": ds_url(p="page", type=1, id=identifier)}
            copy_id = cache.add_copy(release, {}, f"https://download.mediafire.com/{identifier}.zip")
            if identifier == 25:
                cache.put_archive(copy_id, {"format": "zip", "audio_members": 1, "members": 1}, None)
        calls, logs = [], []
        def inspect(http, jobs_cache, jobs, found, output, emit, publish, artwork, totals, **options):
            calls.append((tuple(found), options))
        with patch("native.search_worker.inspect_archives", inspect):
            run_idle_layers(object(), cache, IDLE_TAGS, Path(), None, logs.append, slot=lambda _: 0)
        self.assertEqual([(ids, options["probe_headers"], options["probe_cover"]) for ids, options in calls],
                         [((25,), True, False)])
        self.assertEqual([line for line in logs if line.startswith("\x1b")], ["\x1b", "\x1b"])

    def test_idle_browse_requests_status_refresh_every_ten_pages(self):
        logs = []

        def browse(_http, _cache, emit, *, slot):
            for page in range(1, 23):
                emit(f"Idle catalog page {page}")

        with patch("native.search_worker.idle_backfill", browse):
            run_idle_layers(object(), None, IDLE_BROWSE, Path(), None, logs.append, slot=lambda _: 0)
        self.assertEqual(logs[:2], ["\x1b", "\x1b"])
        self.assertEqual([logs[i - 1] for i, line in enumerate(logs) if i >= 2 and line == "\x1b"],
                         ["Idle catalog page 10", "Idle catalog page 20", "Idle catalog page 22"])

    def test_referrer_and_session_cookie_across_redirects(self):
        source = self.url + "/release?id=19746#fragment"
        Http().get(self.url + "/session-share", headers={"Referer": source})
        self.assertEqual(self.server.download_headers["Referer"], self.url + "/release?id=19746")
        self.assertEqual(self.server.download_headers["Cookie"], "share=ready")
        self.assertEqual(self.server.destination_headers["Referer"], self.url + "/")
        self.assertNotIn("Cookie", self.server.destination_headers)
        Http().get(self.url + "/private-share", headers={"Referer": source})
        self.assertNotIn("Referer", self.server.destination_headers)
        self.assertIsNone(navigation_referrer("https://example.test/release", "http://example.test/archive"))

    def test_download_navigation_uses_observed_form_and_link_policy(self):
        document = b"""<meta name="referrer" content="origin"><mainbar><h2>Test</h2>
        <form method="post" action="/form"><input type="hidden" name="id" value="25">
        <input type="hidden" name="type" value="1">
        <button name="download_link" formaction="/chosen" value="Download">GET</button></form></mainbar>"""
        row, forms = parse_detail(document, 25, "https://doujinstyle.com/release?id=25")
        calls = []

        def get(url, **kwargs):
            calls.append((url, kwargs))
            if kwargs.get("data"):
                return {"Location": "https://www.mediafire.com/file/release"}, b"", url
            return ({"Referrer-Policy": "unsafe-url"},
                    b'<base href="//download.mediafire.com/archives/"><a rel="noreferrer" '
                    b'id="downloadButton" href="album.zip?x=1&amp;y=2">Download</a>', url)

        http = SimpleNamespace(get=get)
        _, share = resolve_offer(http, row, forms[0])
        destination, source, policy = archive_destination(http, share, row["url"])
        self.assertEqual(calls[0][0], "https://doujinstyle.com/chosen")
        self.assertEqual(calls[0][1]["data"], b"id=25&type=1&download_link=Download")
        self.assertEqual(calls[0][1]["policy"], "origin")
        self.assertEqual(calls[0][1]["headers"]["Origin"], "https://doujinstyle.com")
        self.assertEqual((destination, source, policy),
                         ("https://download.mediafire.com/archives/album.zip?x=1&y=2", share, "no-referrer"))

    def test_download_offer_preserves_raw_utf8_redirect_bytes(self):
        release = {"id": 25, "url": self.url + "/release?id=25"}
        _, share = resolve_offer(Http(), release, {
            "id": "25", "type": "1", "action": self.url + "/offer", "method": "post",
        })
        self.assertEqual(share, "https://www.mediafire.com/file/abc/Album+%E3%80%9C%20Mix.zip/file?x=%2B&name=%E6%9B%B2")

    def test_shared_ranges_and_independent_seek_positions(self):
        source = RangedObject(Http(), self.url + "/archive", 32_768)
        first, second = source.cursor(), source.cursor()
        first.seek(49, io.SEEK_SET)
        self.assertEqual(first.read(13), self.zip_bytes[49:62])
        requests = source.requests
        second.seek(53, io.SEEK_SET)
        self.assertEqual(second.read(4), self.zip_bytes[53:57])
        self.assertEqual(first.tell(), 62)
        self.assertEqual(second.tell(), 57)
        self.assertEqual(source.requests, requests)

    def test_range_ignored_is_reported_with_no_body_consumed(self):
        http = Http()
        with self.assertRaisesRegex(ValueError, r"HTTP 200; response body skipped"):
            inspect_zip(http, self.url + "/range-ignored", 8192)
        self.assertEqual(http.log[0]["body_bytes"], 0)

    def test_validated_range_can_reveal_different_archive_type(self):
        self.server.contents = b"7z\xbc\xaf\x27\x1c" + bytes(17000)
        result = inspect_zip(Http(), self.url + "/looks-like.zip", 32768)
        self.assertEqual(result["format"], "7z")

    def test_directory_only_uses_tail_range_for_fixture(self):
        result = inspect_zip(Http(), self.url + "/archive", 8192, directory_only=True)
        self.assertEqual(result["audio_members"], 2)
        self.assertEqual(result["sidecars"], [])
        self.assertIn("[ABCDCD-001] Release/RELEASE.NFO", result["sidecar_candidates"])
        self.assertEqual(result["archive_http"]["body_bytes"], 8192)
        self.assertEqual(result["archive_http"]["requests"], 1)

    def test_directory_only_combines_footer_and_larger_directory_in_one_range(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("padding.bin", bytes(90_000))
            for number in range(80):
                archive.writestr(f"release/{number:02d}-{'x' * 100}.flac", b"")
        self.server.contents = buffer.getvalue()
        report = inspect_zip(Http(), self.url + "/archive", 262_144, directory_only=True)
        self.assertEqual(report["audio_members"], 80)
        self.assertEqual(report["archive_http"]["requests"], 1)
        self.assertEqual(report["archive_http"]["body_bytes"], 65_557)

    def test_zip_front_cover_obeys_budget_and_leaves_back_cover_unselected(self):
        buffer = io.BytesIO()
        jpeg = b"\xff\xd8\xff" + b"cover fixture"
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("back.jpg", jpeg)
            archive.writestr("cover.jpg", jpeg)
            archive.writestr("padding.bin", b"\0" * 128_000)
        self.server.contents = buffer.getvalue()
        artwork = []
        report = inspect_zip(Http(), self.url + "/archive", 24_576, directory_only=True, cover_sink=artwork.append)
        self.assertEqual(report["cached_front_cover"], "cover.jpg")
        self.assertEqual(artwork, [jpeg])
        self.assertLess(report["archive_http"]["body_bytes"], 24_576)

        report = inspect_zip(Http(), self.url + "/archive", 8192, directory_only=True, cover_sink=artwork.append)
        self.assertNotIn("cached_front_cover", report)
        self.assertEqual(artwork, [jpeg])

    def test_interactive_search_commits_then_indexes_and_publishes_once(self):
        self.verify_interactive_search()

    @unittest.skipUnless(os.getenv("DOUJINSTYLE_TEST_PUBLISHER"), "Native catalog worker is required")
    def test_interactive_search_with_native_catalog_mutations(self):
        self.verify_interactive_search(ReferencePublisher(os.environ["DOUJINSTYLE_TEST_PUBLISHER"]))

    def verify_interactive_search(self, publisher=None):
        catalog_page = b"""<mainbar><div class="gridBox"><div class="gridDetails">
        <a href="/?p=page&amp;type=1&amp;id=25">Example</a></div></div> Total Results Found: 1</mainbar>"""
        detail_page = b"""<mainbar><h2>Example</h2>
        <span class="pageSpan1">Date Added:</span><span class="pageSpan2">Nov. 16th, 2026</span>
        <span class="pageSpan1">Publisher:</span><span class="pageSpan2">Fixture Label</span><form method="post" action="./">
        <input type="hidden" name="id" value="25"><input type="hidden" name="type" value="1">
        <button name="download_link">Download</button></form></mainbar>"""
        server = self.url
        copy = "https://download.mediafire.com/example.zip"
        requests = []

        class FixtureHttp(Http):
            def get(self, url, **kwargs):
                requests.append(url)
                if kwargs.get("data"):
                    return {"Location": copy}, b"", url
                if "p=search" in url:
                    return {}, catalog_page, url
                if "p=page" in url:
                    return {}, detail_page, url
                if url == copy:
                    return super().get(server + "/archive", **kwargs)
                raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            db = profile / "catalog.sqlite"
            tracks = profile / "tracks"
            with sqlite3.connect(db) as independent:
                cache = CatalogCache(db, publisher=publisher)
                self.addCleanup(cache.close)
                cache.add_copy({"id": 99, "album": "Unrelated"}, {"format_hint": "zip"},
                               "https://download.mediafire.com/unrelated.zip")
                logs = []

                def emit(line):
                    if line.startswith("Page 1"):
                        self.assertEqual(independent.execute("SELECT count(*) FROM releases WHERE id=25").fetchone()[0], 1)
                    logs.append(line)

                http = FixtureHttp()
                visible, published = [], []

                def publish(identifier, filename):
                    self.assertTrue((tracks / filename).is_file())
                    published.append(identifier)

                totals = run_search(http, cache, "Example", 3, tracks, emit, emit_result=visible.append,
                                    emit_reference=publish)
                self.assertEqual((totals["listings"], totals["archives_indexed"], totals["references_written"]),
                                 (1, 1, 2))
                self.assertEqual([row.get("listed_at") for row in visible], [None, "2026-11-16"])
                self.assertEqual(published, [25, 25])
                self.assertEqual([row["id"] for row in cache.local_search("Fixture Label", 10)], [25])
                report = cache.copies_for(25)[0]["archive"]
                self.assertEqual(report["audio_probes"]["Disc A/01.FLAC"]["metadata"]["bits_per_sample"], 24)
                descriptors = [path.read_text("utf-8") for path in tracks.glob("*.djt")]
                flac_descriptor = next(value for value in descriptors if "01.FLAC" in value)
                self.assertIn("SOURCE_BYTES=184362", flac_descriptor)
                self.assertIn("CODEC=FLAC", flac_descriptor)
                self.assertIn("BITRATE_KBPS=737", flac_descriptor)
                self.assertLessEqual(report["archive_http"]["body_bytes"], ARCHIVE_BYTES)
                self.assertTrue(report["header_probe_pass_complete"])
                self.assertIsNone(cache.copies_for(99)[0]["archive"])
                requests.clear()
                repeated = run_search(http, cache, "Example", 3, tracks, emit)
                self.assertEqual(requests, [])
                self.assertEqual((repeated["archives_attempted"], repeated["references_written"]), (0, 0))
                archive = {key: value for key, value in report.items() if key != "audio_probes"}
                copy_id = cache.copies_for(25)[0]["copy_id"]
                cache.put_archive(copy_id, archive, None)
                self.assertEqual(cache.copies_for(25)[0]["archive"]["audio_probes"], report["audio_probes"])
                with independent:
                    independent.execute("UPDATE copies SET archive=json_remove(archive, '$.audio_probes') WHERE id=?",
                                        (copy_id,))
                due = cache.index_jobs(6, "mediafire.com", release_ids=[25], require_header_probes=True)
                self.assertEqual([job["id"] for job in due], [copy_id])
                cache.close()

    def test_search_automatically_indexes_past_first_batch_and_skips_recent_failure(self):
        identifiers = list(range(101, 139))
        requests, indexed, logs = [], [], []

        class FixtureHttp:
            def get(self, url, **kwargs):
                requests.append(url)
                params = parse_qs(urlsplit(url).query)
                if data := kwargs.get("data"):
                    identifier = int(parse_qs(data)[b"id"][0])
                    host = "mega.nz" if identifier == 102 else "download.mediafire.com"
                    return {"Location": f"https://{host}/{identifier}.zip"}, b"", url
                if params["p"] == ["search"]:
                    offset = 20 * int(params["page"][0])
                    cards = "".join(f'<div class="gridBox"><div class="gridDetails">'
                                    f'<a href="/?p=page&amp;type=1&amp;id={identifier}">Release {identifier}</a>'
                                    f'</div></div>' for identifier in identifiers[offset:offset + 20])
                    return {}, (cards + "Total Results Found: 38").encode(), url
                identifier = int(params["id"][0])
                detail = (f'<mainbar><h2>Release {identifier}</h2><form method="post" action="./">'
                          f'<input type="hidden" name="id" value="{identifier}">'
                          f'<input type="hidden" name="type" value="1">'
                          f'<button name="download_link">Download</button></form></mainbar>')
                return {}, detail.encode(), url

        def inspect(http, url, budget, **options):
            self.assertLessEqual(budget, ARCHIVE_BYTES)
            self.assertLessEqual(options["max_requests"], RANGE_REQUESTS)
            identifier = int(urlsplit(url).path.removesuffix(".zip").lstrip("/"))
            indexed.append(identifier)
            if identifier == 103:
                raise HTTPError(url, 403, "Forbidden", None, None)
            member = {"path": "Track.flac", "uncompressed_bytes": 1234, "compressed_bytes": 1200}
            return {"format": "zip", "archive_http": {"body_bytes": 8192}, "audio_members": 1, "inventory": [member],
                    "artwork": [], "audio_probes": {}, "header_probe_pass_complete": True}

        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        with tempfile.TemporaryDirectory() as directory, \
                patch("native.search_worker.inspect_zip", inspect), patch("native.search_worker.time.sleep") as pause:
            output = Path(directory)
            first = run_search(FixtureHttp(), cache, "Fixture", 2, output, logs.append)
            self.assertEqual(first["listings"], 38)
            self.assertEqual(first["archives_attempted"], 37)
            self.assertEqual(first["releases_with_tracks"], 36)
            self.assertEqual(first["references_written"], 36)
            self.assertEqual(len(list(output.glob("*.djt"))), 36)
            self.assertCountEqual(indexed, set(identifiers) - {102})
            self.assertTrue(any(line.startswith("Indexing 38 unresolved releases") for line in logs))
            pause.assert_not_called()
            requests.clear()
            again = run_search(FixtureHttp(), cache, "Fixture", 2, output, logs.append)
            self.assertEqual(requests, [])
            self.assertEqual(again["releases_with_tracks"], 36)
            self.assertEqual(again["references_written"], 0)

    def test_fast_offer_starts_archive_probe_while_slow_offer_is_pending(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        found = {identifier: {"id": identifier, "url": f"https://doujinstyle.com/?id={identifier}"}
                 for identifier in (1, 2)}
        for row in found.values():
            cache.put_listing(row)
        fast_archive = threading.Event()

        def fetch(_, row):
            if row["id"] == 2:
                self.assertTrue(fast_archive.wait(5), "Slow offer blocked independent archive work")
                return 2, None, [], "No offer"
            return 1, None, [("doujinstyle", {"host": "www.mediafire.com"},
                              "https://www.mediafire.com/file/fast/album.zip/file")], None

        def inspect(*args, **kwargs):
            fast_archive.set()
            return {"format": "rar", "archive_http": {"body_bytes": 64}}

        with tempfile.TemporaryDirectory() as directory, patch("native.search_worker.fetch_offer", fetch), \
                patch("native.search_worker.inspect_zip", inspect):
            totals = index_results(None, cache, found, Path(directory), lambda _: None)
        self.assertEqual(totals["archives_indexed"], 1)
        self.assertEqual(cache.copies_for(1)[0]["archive"]["format"], "rar")

    def test_interactive_worker_origin_boundary(self):
        check_url("https://download.mediafire.com/example.zip")
        for url in ("http://doujinstyle.com", "https://localhost/archive.zip",
                    "https://www.mediafire.com.evil.example/a", "https://www.mediafire.com:444/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_url(url)

    def test_native_result_record_keeps_untrusted_text_on_one_line(self):
        row = {"id": 25, "album": "夜桜\nRelease\x1e", "artist": "Circle\tName",
               "tags": ["M3-58", "Hardcore"], "technical": {"advertised_format": "FLAC"},
               "release_date": "2026-10-25", "listed_at": "2026-11-16"}
        prefix = "\x1e25\t夜桜 Release\tCircle Name\tFLAC\tM3-58, Hardcore\t"
        suffix = "\t\tM3-58\x1fHardcore"
        self.assertEqual(result_record(row), prefix + "2026-10-25" + suffix)
        self.assertEqual(result_record({**row, "release_date": "2026-11-17"}), prefix + "2026-11-17" + suffix)
        self.assertEqual(result_record({**row, "release_date": "2026-10-32"}), prefix + "2026-10-25" + suffix)

    def test_cover_fetch_is_bounded_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = b"\xff\xd8\xff" + b"fixture"
            with patch("native.search_worker.SearchHttp.get", return_value=({}, data, "")) as get:
                self.assertTrue(cache_cover(path, 25))
                self.assertTrue(cache_cover(path, 25))
                get.assert_called_once_with("https://doujinstyle.com/thumbs/25.jpg",
                                            cap=COVER_BYTES, redirects=False, timeout=4)
            self.assertEqual((path / "25.jpg").read_bytes(), data)

    def test_artwork_names_do_not_become_catalog_ids(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("art-0.png", b"cover")
            archive.writestr("art-1.png", b"back")
            archive.writestr("art-2/scan.png", b"scan")
            archive.writestr("[ART-03] Cover.png", b"variant")
            archive.writestr("101 - Riku - Zhulong (Headlights).flac", b"fLaC")
            archive.writestr("unidentified.blob", b"data")
            archive.writestr("Subpackage.rar", b"Rar!")
        self.server.contents = buffer.getvalue()
        self.server.filename = "[UMSZ-002] ORIENTAL RADIANCE Vol.2.zip"
        report = inspect_zip(Http(), self.url + "/archive", 8192, directory_only=True)
        self.assertEqual(report["catalog_id_candidates"], ["UMSZ-002"])
        self.assertEqual(report["catalog_id_candidate_evidence"]["UMSZ-002"][0]["source"], "archive_filename")
        self.assertEqual(report["sidecar_candidates"], [])
        self.assertEqual(report["unknown_suffixes"], {".blob": 1})
        self.assertEqual(report["member_types"], {"artwork": 4, "audio_candidate": 1, "unknown": 1, "archive": 1})
        self.assertEqual(len(report["inventory"]), report["members"])

    def test_cached_index_jobs_persist_members_and_search_by_filename(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_page("test-page", {"releases": [{"id": 25, "album": "Test album"}]})
        cache.put_offer(25, {"format_hint": "zip"}, self.url + "/archive.zip", None)
        args = SimpleNamespace(command="index", limit=1, host=None, byte_budget=65536)
        self.assertEqual(run_catalog_jobs(Http(), cache, args)["attempted"], 1)
        self.assertEqual(run_catalog_jobs(Http(), cache, args)["attempted"], 0)
        self.assertEqual(cache.summary()["archive_formats"], {"zip": 1})
        found = cache.local_search("０１.FLAC", 10)
        self.assertEqual([row["id"] for row in found], [25])
        self.assertEqual(found[0]["archive"]["archive_http"]["requests"], 1)

    def test_event_and_cue_metadata(self):
        self.assertEqual(event("Ｃ１０８")["start"], "2026-08-15")
        self.assertEqual(event("M3‑58")["start"], "2026-10-25")
        self.assertIsNone(event("M3-63")["start"])
        self.assertIsNone(event("Hardcore"))
        self.assertEqual(sidecar_values("x.cue", 'PERFORMER "例"\nTITLE "曲集"\nFILE "x.wav" WAVE\n  TITLE "Track"\n'.encode("cp932")),
                         {"album": "曲集", "album_artist": "例"})

    def test_flac_probe_parses_embedded_tags_within_metadata_budget(self):
        values = ["TITLE=夜桜電光".encode(), b"ARTIST=Example"]
        comment = struct.pack("<II", 0, len(values))
        comment += b"".join(struct.pack("<I", len(value)) + value for value in values)
        packed = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
        header = b"fLaC\0\0\0\x22" + bytes(10) + packed.to_bytes(8, "big") + bytes(16)
        sample = sample_flac_member(io.BytesIO(header + b"\x84" + len(comment).to_bytes(3, "big") + comment))
        self.assertEqual(sample["metadata"]["duration_seconds"], 1.0)
        self.assertEqual(sample["tags"]["TITLE"], ["夜桜電光"])
        oversized = sample_flac_member(io.BytesIO(header + b"\x84\x01\x00\x00"))
        self.assertEqual(oversized["metadata"], sample["metadata"])
        self.assertIn("budget", oversized["tags_status"])


class CatalogTests(unittest.TestCase):
    def test_wal_reader_uses_a_committed_snapshot_and_backup_is_portable(self):
        if not wal_supported(sqlite3.sqlite_version_info):
            self.skipTest("SQLite runtime lacks the upstream WAL-reset fix")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.sqlite"
            with closing(CatalogCache(path)) as first, closing(CatalogCache(path)) as second, \
                    closing(CatalogCache(path, read_only=True)) as reader:
                self.assertEqual(reader.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                first.put_listing({"id": 1, "album": "First"})
                reader.db.execute("BEGIN")
                self.assertEqual(reader.listing(1)["album"], "First")
                with first.db:
                    first.db.execute("BEGIN IMMEDIATE")
                    first.put_listings([{"id": 1, "album": "Updated"}], 42)
                    self.assertEqual(second.listing(1)["album"], "First")
                second.put_listing({"id": 2, "album": "Second writer"})
                self.assertIsNone(reader.listing(2))
                self.assertEqual(reader.listing(1)["album"], "First")
                reader.db.commit()
                self.assertEqual(reader.listing(1)["album"], "Updated")
                self.assertEqual(reader.listing(2)["album"], "Second writer")
                backup = path.with_name("backup.sqlite")
                first.backup(backup)
                with closing(CatalogCache(backup, read_only=True)) as saved:
                    self.assertEqual(saved.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                    self.assertEqual(saved.listing(2), reader.listing(2))

    def test_old_sqlite_keeps_rollback_and_will_only_read_a_wal_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "catalog.sqlite"
            with patch("catalog_cache.sqlite3.sqlite_version_info", (3, 51, 2)):
                with closing(CatalogCache(path)) as cache:
                    self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            with patch("catalog_cache.sqlite3.sqlite_version_info", (3, 51, 2)):
                with closing(CatalogCache(path, read_only=True)) as cache:
                    self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                with self.assertRaisesRegex(sqlite3.OperationalError, "requires patched SQLite"):
                    CatalogCache(path)

    def test_slow_track_publication_does_not_hold_the_sqlite_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            with closing(CatalogCache(catalog, structured_references=True)) as cache:
                copy = cache.add_copy({"id": 1, "album": "Release"}, {}, "https://example.test/archive.zip")
                cache.put_archive(copy, {"format": "zip", "inventory": [{"path": "Track.flac"}]}, None)
                writing = threading.Event()
                resume = threading.Event()
                failures = []

                def slow_write(path, data):
                    writing.set()
                    if not resume.wait(5):
                        raise TimeoutError("No concurrent SQLite commit")
                    return write_reference(path, data)

                def publish():
                    try:
                        with closing(CatalogCache(catalog, structured_references=True)) as publisher:
                            export(publisher, root / "tracks", [1])
                    except Exception as error:
                        failures.append(error)

                with patch("native.export_library.write_reference", slow_write):
                    thread = threading.Thread(target=publish)
                    thread.start()
                    try:
                        self.assertTrue(writing.wait(5))
                        cache.put_observation({"id": 1, "artist": "Concurrent writer"})
                        self.assertEqual(cache.listing(1)["artist"], "Concurrent writer")
                    finally:
                        resume.set()
                        thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])
                self.assertEqual(cache.db.execute("SELECT count(*) FROM published_references").fetchone()[0], 1)

    def test_comment_keeps_release_event_and_explicit_catalog_provenance(self):
        release = {"id": 9649, "album": "Discography", "catalog_id": "SITE-123"}
        path = "Vol. 8/Evolution Schema.flac"
        samples = {"audio_probes": {path: {"tags": {"COMMENT": ["M3-44", "CAT-009"],
                                                     "CATALOG NUMBER": ["TRACK-456"]}}}}
        reference = make_descriptor(release, {"path": path}, "https://example.invalid/discography.zip", samples,
                                    [], album_catalog="ALBUM-789")
        for field in ("COMMENT=M3-44", "COMMENT=CAT-009", "RELEASE EVENT=M3-44", "DATE=2019-10-27",
                      "CATALOG SOURCES=1", "CATALOGNUMBER=TRACK-456", "CATALOG SOURCE TRACK=TRACK-456",
                      "CATALOG SOURCE RELEASE=SITE-123", "CATALOG SOURCE ALBUM=ALBUM-789"):
            self.assertIn(field, reference.splitlines())
        self.assertNotIn("CATALOGNUMBER=M3-44", reference)

    def test_structured_export_preserves_references_across_upgrades(self):
        cache = CatalogCache(":memory:", structured_references=True)
        self.addCleanup(cache.close)
        release = {"id": 9649, "album": "Discography"}
        copy_id = cache.add_copy(release, {}, "https://download.mediafire.com/discography.zip")
        dated, undated, existing = "Vol. 8/04.flac", "Unknown/01.flac", "Legacy/02.flac"
        archive = {"format": "zip", "inventory": [{"path": p} for p in (dated, undated, existing)],
                   "audio_probes": {dated: {"tags": {"DATE": ["2019-10-27"], "CATALOGNUMBER": ["ED-008"]}}}}
        cache.put_archive(copy_id, archive, None)
        import hashlib
        def basename(path):
            return f"9649-{hashlib.sha256(path.encode()).hexdigest()[:24]}.djt"

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            flat = output / basename(existing)
            flat.write_text("Old descriptor")
            locations = []
            export(cache, output, [9649], on_reference=lambda _, path: locations.append(path))
            dated_path = output / "2019" / "10" / basename(dated)
            undated_path = output / "undated" / basename(undated)
            self.assertEqual(set(output.rglob("*.djt")), {flat, dated_path, undated_path})
            self.assertEqual(set(locations), {file.relative_to(output).as_posix() for file in output.rglob("*.djt")})
            self.assertNotIn("CATALOGNUMBER=ED-008", undated_path.read_text())
            # A restored database can lack the placement index.
            with cache.db:
                cache.db.execute("DELETE FROM published_references")
            cache.put_observation({**release, "release_date": "2024-05-01"})
            export(cache, output, [9649])
            self.assertEqual(set(output.rglob("*.djt")), {flat, dated_path, undated_path})
            self.assertIn("DATE=2024-05-01", undated_path.read_text())

    def test_site_tag_search_and_suggestions(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_listing({"id": 1, "tags": ["Touhou", "東方"]})
        cache.put_listing({"id": 2, "tags": ["touhou", "House"]})
        cache.put_observation({"id": 1, "tags": [], "events": []})
        self.assertEqual([tag.casefold() for tag in cached_tags(cache)], ["touhou", "house", "東方"])
        batch = {"total_results": 0, "rows_on_page": 0, "cache_hit": True, "releases": []}
        with patch("native.search_worker.cached_listing", return_value=batch) as listing:
            run_search(object(), cache, "tag: 東方", 2, Path(), lambda _: None)
            self.assertEqual(parse_qs(urlsplit(listing.call_args.args[2]).query)["type"], ["tags"])
            self.assertEqual(parse_qs(urlsplit(listing.call_args.args[2]).query)["result"], ["東方"])
            run_search(object(), cache, "東方", 2, Path(), lambda _: None)
            self.assertEqual(parse_qs(urlsplit(listing.call_args.args[2]).query)["type"], ["blanket"])
        with self.assertRaisesRegex(ValueError, "Enter a DoujinStyle tag"):
            run_search(object(), cache, "tag: ", 1, Path(), lambda _: None)

    def test_mega_offer_status_distinguishes_host_from_eligibility(self):
        offer = {"host": "mega.nz"}
        self.assertIn("eligible for archive indexing", offer_status(
            offer, "https://mega.nz/file/abcdefgh#" + "A" * 43))
        self.assertIn("folder link", offer_status(offer, "https://mega.nz/folder/abcdefgh#folder-key"))
        self.assertIn("folder link", offer_status(offer, "https://mega.nz/#F!abcdefgh!folder-key"))
        self.assertIn("without a supported public file", offer_status(offer, "https://mega.nz/file/abcdefgh"))

    def test_depth_counts_shared_copies_once_and_keeps_site_dates_separate(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        dated = {"id": 7, "album": "A", "listed_at": "2021-02-03"}
        undated = {"id": 99, "album": "B", "download_offer": True}
        browse_only = {"id": 120, "album": "C"}
        for listing in (dated, undated, browse_only):
            cache.put_listing(listing)
        shared = cache.add_copy(dated, {}, "https://example.com/a.zip")
        cache.add_copy(undated, {}, "https://example.com/a.zip")
        retired = cache.put_offer(7, {}, "https://example.com/retired.zip", None)
        cache.put_offer(7, {}, "https://example.com/current.zip", None)
        cache.put_archive(retired, {"format": "zip", "members": 50, "audio_members": 50}, None)
        cache.put_archive(shared, {
            "format": "zip", "members": 2, "audio_members": 2,
            "inventory": [{"kind": "artwork"}],
            "audio_probes": {"a.flac": {"metadata": {"codec": "FLAC"}}, "b.mp3": {"prefix_status": "truncated"}},
            "audio_sample": {"path": "a.flac", "tags": {"TITLE": ["Song"]}},
            "cached_front_cover": "cover.jpg",
        }, None)
        depth = cache.depth_report()
        self.assertEqual(depth["listings"]["releases"], 3)
        self.assertEqual(depth["listings"]["max_release_id"], 120)
        self.assertEqual(depth["listings"]["latest_site_date"], {"date": "2021-02-03", "release_id": 7})
        self.assertEqual(depth["release_details_with_evidence"]["releases"], 2)
        self.assertEqual(depth["archive_directories"]["copies"], 1)
        self.assertEqual(depth["archive_directories"]["releases"], 2)
        self.assertEqual(depth["archive_directories"]["audio_members"], 2)
        self.assertEqual(depth["track_headers_probed"]["members_probed"], 2)
        self.assertEqual(depth["technical_metadata"]["members_with_technical_metadata"], 1)
        self.assertEqual(depth["track_tags"]["members_with_tags"], 1)
        self.assertEqual(depth["cover_candidates"]["candidates"], 1)
        self.assertEqual(depth["front_covers_recorded"]["copies"], 1)
        storage = cache.storage_report()
        self.assertGreater(storage["payload_bytes"]["copies.archive"], 0)
        self.assertIn("release_copies_by_copy", {index["name"] for index in storage["indexes"]})

    def test_idle_backfill_relocates_checkpoint_and_keeps_navigation_referrers(self):
        def fixture(offset, ids):
            releases = "".join(
                f'<div class="gridBox"><div class="gridDetails">'
                f'<a href="/?p=page&amp;type=1&amp;id={identifier}">Album {identifier}</a></div></div>'
                for identifier in ids)
            pager = "".join(f'<a href="./?p=home&amp;page={i}">{i + 1}</a>' for i in range(3) if i != offset)
            return f'<mainbar>{releases}<div class="pageScroll">{pager}</div></mainbar>'.encode()

        urls = [ds_url(p="home", page=i) for i in range(3)]
        pages = {url: fixture(i, ids) for i, (url, ids) in enumerate(zip(urls, [(9, 8, 7), (6, 5, 4), (3, 2, 1)]))}
        calls = []

        def get(url, **kwargs):
            self.assertFalse(kwargs["redirects"])
            self.assertIn("idle catalog backfill", kwargs["headers"]["User-Agent"])
            calls.append((url, kwargs.get("headers", {}).get("Referer"), kwargs.get("policy")))
            if url.endswith("robots.txt"):
                return {}, b"User-agent: *\nAllow: /\n", url
            return {"Referrer-Policy": "origin"}, pages[url], url

        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        old_tail = parse_listing(fixture(1, (3, 2, 1)), 20)
        cache.put_page(urls[1], old_tail, checkpoint={
            "url": urls[1], "referrer": urls[0], "ids": [3, 2, 1], "complete": False,
        })
        activity = []
        idle_backfill(SimpleNamespace(get=get), cache, activity.append, slot=lambda stamp: stamp + 10)
        self.assertTrue(cache.backfill_checkpoint()["complete"])
        self.assertEqual(cache.summary()["listings"], 9)
        self.assertEqual(calls[2:], [(urls[1], urls[0], ""), (urls[2], urls[1], "origin"),
                                     (urls[1], urls[2], "origin"), (urls[0], urls[1], "origin")])
        self.assertIn("Relocated checkpoint to page 3.", activity)

    def test_idle_backfill_reports_robots_and_stops_on_actual_rate_limit(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        requests = []

        def get(url, **kwargs):
            requests.append(url)
            if url.endswith("robots.txt"):
                return {}, b"User-agent: *\nDisallow: /\n", url
            raise HTTPError(url, 429, "Too Many Requests", {"Retry-After": "60"}, None)

        messages, slots = [], []
        with self.assertRaisesRegex(HTTPError, "429"):
            idle_backfill(SimpleNamespace(get=get), cache, messages.append, slot=lambda stamp: slots.append(stamp) or stamp + 10)
        self.assertEqual(requests, ["https://doujinstyle.com/robots.txt", ds_url(p="home", page=0)])
        self.assertIn("excludes this crawler", messages[0])
        self.assertEqual(len(slots), 1)
        self.assertIsNone(cache.backfill_checkpoint())

    def test_activity_and_song_tracks_survive_snapshot_and_replay(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            dump = root / "export.jsonl"
            dump.write_text('{"id":1,"album":"Album","shares":[],"tracks":[{"title":"夜桜電光"}],'
                            '"track_source_url":"https://example.com/catalog"}\n', encoding="utf-8")
            cache = CatalogCache(root / "cache.sqlite")
            try:
                baseline = cache.import_listings(export_records(dump, cache), baseline=True)
                self.assertEqual(baseline["new_listings"], 1)
                self.assertEqual(baseline["new_listing_activity"], 0)
                with dump.open("a", encoding="utf-8") as handle:
                    handle.write('{"id":2,"album":"New entry"}\n')
                self.assertEqual(cache.import_listings(export_records(dump, cache))["new_listing_activity"], 1)
                cache.backup(root / "backup.sqlite")
            finally:
                cache.close()
            restore_snapshot(root / "backup.sqlite", root / "restored.sqlite")
            with self.assertRaises(FileExistsError):
                restore_snapshot(root / "backup.sqlite", root / "restored.sqlite")
            restored = CatalogCache(root / "restored.sqlite")
            try:
                self.assertEqual(restored.import_listings(export_records(dump, restored))["new_listing_activity"], 0)
                self.assertEqual(restored.summary()["recorded_new_listing_activity"], 1)
                self.assertEqual(restored.song_search("夜桜", 10)[0]["source_url"], "https://example.com/catalog")
                dump.write_text('{"id":3,"album":"Uncommitted"}\ninvalid\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "line 2"):
                    restored.import_listings(export_records(dump, restored))
                self.assertIsNone(restored.listing(3))
            finally:
                restored.close()

    def test_compressed_import_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            data = root / "export.jsonl.gz"
            with gzip.open(data, "wt", encoding="utf-8") as output:
                output.write('{"id":7,"album":"Compact"}\n')
            cache = CatalogCache(root / "cache.sqlite")
            try:
                cache.import_listings(export_records(data, cache))
                cache.backup(root / "backup.sqlite.gz")
            finally:
                cache.close()
            restore_snapshot(root / "backup.sqlite.gz", root / "restored.sqlite")
            restored = CatalogCache(root / "restored.sqlite")
            try:
                self.assertEqual(restored.listing(7)["album"], "Compact")
                self.assertEqual(restored.import_listings(export_records(data, restored))["new_listing_activity"], 0)
            finally:
                restored.close()

    def test_host_identity_preserves_opaque_credentials_and_query(self):
        first = destination_key("https://www.mediafire.com/file/abcdef12345/First.zip/file")
        self.assertEqual(first, destination_key("https://mediafire.com/file/abcdef12345/file"))
        self.assertNotEqual(first, destination_key("https://mediafire.com/file/abcdef12345/file?token=other"))
        self.assertNotEqual(destination_key("https://mega.nz/file/123#one"), destination_key("https://mega.nz/file/123#two"))
        self.assertNotEqual(destination_key("https://example.com/file?id=1"), destination_key("https://example.com/file?id=2"))

    def test_multiple_copies_share_one_destination_across_listings(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        a = {"id": 25, "album": "A"}
        b = {"id": 26, "album": "B"}
        cache.put_page("front", {"releases": [a, b]})
        flac = {"filename": "FLAC.zip", "format_hint": "zip"}
        first = cache.put_offer(25, flac, "https://www.mediafire.com/file/abc/FLAC.zip/file", None)
        alias = cache.add_copy(b, {"filename": None}, "https://mediafire.com/file/abc/file")
        mp3 = cache.add_copy(a, {"filename": "MP3.zip"}, "https://example.com/MP3.zip")
        self.assertEqual(alias, first)
        self.assertNotEqual(mp3, first)
        cache.put_archive(first, {"format": "zip", "response_filename": "FLAC.zip"}, None)
        self.assertEqual(cache.copies_for(26)[0]["archive"]["response_filename"], "FLAC.zip")
        self.assertEqual({row["copy_id"] for row in cache.copies_for(25)}, {first, mp3})
        self.assertEqual(cache.summary()["copies"], 2)
        self.assertEqual(cache.summary()["copy_links"], 3)
        self.assertEqual(cache.summary()["archive_indexed"], 1)

    def test_mirror_slots_and_previous_primary_links_are_retained(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_listing({"id": 25, "album": "A"})
        a = "https://mediafire.com/file/first/archive.zip/file"
        b = "https://example.com/mirror.zip"
        c = "https://mediafire.com/file/new/archive.zip/file"
        old, mirror = cache.put_offers(25, [("doujinstyle", {}, a), ("doujinstyle:1", {}, b)], None)
        latest = cache.put_offer(25, {}, c, None)
        copies = {row["copy_id"]: row for row in cache.copies_for(25)}
        self.assertFalse(copies[old]["sources"][0]["current"])
        self.assertTrue(copies[mirror]["sources"][0]["current"])
        self.assertTrue(copies[latest]["sources"][0]["current"])
        cache.put_offers(25, [("doujinstyle", {}, c)], None, {"doujinstyle"})
        copies = {row["copy_id"]: row for row in cache.copies_for(25)}
        self.assertFalse(copies[mirror]["sources"][0]["current"])
        self.assertEqual(len(copies), 3)

    def test_old_database_migrates_without_losing_counts_or_archives(self):
        with tempfile.TemporaryDirectory() as root:
            path = root + "/cache.sqlite"
            db = sqlite3.connect(path)
            with db:
                db.executescript("""CREATE TABLE releases (
                    id INTEGER PRIMARY KEY, listing TEXT NOT NULL, seen INTEGER NOT NULL,
                    offer TEXT, share_url TEXT, host TEXT, offer_checked INTEGER, offer_error TEXT,
                    archive TEXT, archive_checked INTEGER, archive_error TEXT
                );""")
                for n in range(1, 21):
                    url = f"https://www.mediafire.com/file/key{n}/archive.zip/file" if n <= 12 else None
                    manifest = '{"format":"zip","inventory":[{"path":"Preserved.flac"}]}' if n <= 3 else None
                    db.execute("INSERT INTO releases VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        n, f'{{"id":{n},"album":"Album"}}', 100, '{"format_hint":"zip"}' if url else None,
                        url, "mediafire.com" if url else None, 110 if url else None, None,
                        manifest, 120 if manifest else None, None,
                    ))
            db.close()
            for _ in range(2):
                cache = CatalogCache(path)
                try:
                    summary = cache.summary()
                    self.assertEqual((summary["listings"], summary["host_resolved"], summary["archive_indexed"]),
                                     (20, 12, 3))
                    self.assertEqual(summary["copies"], 12)
                    self.assertEqual(summary["archive_formats"], {"zip": 3})
                    self.assertEqual(len(cache.local_search("Preserved.flac", 20)), 3)
                finally:
                    cache.close()

    def test_release_detail_extracts_only_matching_mirror_forms(self):
        def form(id, slot):
            return (f'<form><input type="hidden" name="id" value="{id}">'
                    f'<input type="hidden" name="source" value="{slot}">'
                    '<input type="hidden" name="type" value="1"><button name="download_link">Download</button></form>')
        page = "<mainbar><h2>Album</h2>" + form(25, 0) + form(25, 1) + form(25, 1) + form(26, 2) + "</mainbar>"
        row, forms = parse_detail(page.encode(), 25)
        self.assertTrue(row["download_offer"])
        self.assertEqual([fields["source"] for fields in forms], ["0", "1"])

    def test_cache_deduplicates_listing_and_does_not_requeue_fresh_offers(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        page = {"releases": [{"id": 25, "album": "Test album"}]}
        with patch("catalog_cache.time.time", return_value=20_000):
            cache.put_page("search", page)
            cache.put_page("front", page)
            self.assertEqual(cache.page("search"), page)
            self.assertEqual(cache.summary()["listings"], 1)
            self.assertEqual(len(cache.offer_jobs(10)), 1)
            cache.put_offer(25, {"format_hint": "zip"}, "https://www.mediafire.com/file/abc/file", None)
            self.assertEqual(cache.offer_jobs(10), [])
            self.assertEqual(cache.summary()["hosts"], {"mediafire.com": 1})
        with patch("catalog_cache.time.time", return_value=20_601):
            self.assertIsNone(cache.page("search"))
            self.assertEqual(len(cache.index_jobs(10, "mediafire.com")), 1)

    def test_search_refresh_writes_page_without_erasing_release_enrichment(self):
        cache = CatalogCache(":memory:")
        self.addCleanup(cache.close)
        cache.put_listing({
            "id": 25, "album": "Old title", "catalog_id": "XYZ-001", "publisher": "Circle",
            "technical": {"sample_rate": 96000}, "evidence": {"catalog_id": "creator"},
            "download_forms": [{"source": "1", "id": "25"}],
        })
        page = {"releases": [{
            "id": 25, "album": "Current title", "catalog_id": None, "publisher": None,
            "technical": {"advertised_format": "FLAC"}, "evidence": {"album": "DoujinStyle"},
            "download_forms": [{"source": "0", "id": "25"}],
        }]}
        cache.put_page("search", page)
        self.assertEqual(cache.page("search"), page)
        listing = cache.listing(25)
        self.assertEqual(listing["album"], "Current title")
        self.assertEqual(listing["publisher"], "Circle")
        self.assertEqual(listing["catalog_id"], "XYZ-001")
        self.assertEqual(listing["technical"], {"sample_rate": 96000, "advertised_format": "FLAC"})
        self.assertEqual(listing["evidence"], {"catalog_id": "creator", "album": "DoujinStyle"})
        self.assertEqual({form["source"] for form in listing["download_forms"]}, {"0", "1"})

    def test_offer_and_archive_evidence_keep_catalog_id_unverified(self):
        row = {
            "catalog_id": None,
            "offer": {"filename": "[umsz-002] Release.zip", "catalog_id_candidate": "umsz-002"},
            "archive": {"catalog_id_candidate_evidence": {
                "UMSZ-002": [{"source": "archive_filename", "name": "[UMSZ-002] Release.zip"}],
            }},
        }
        release_catalog_candidates(row)
        self.assertEqual(row["catalog_id_candidate"], "UMSZ-002")
        self.assertIsNone(row["catalog_id"])
        self.assertEqual([item["source"] for item in row["catalog_id_candidate_evidence"]["UMSZ-002"]],
                         ["offer_filename", "archive_filename"])

    def test_conflicting_catalog_id_hints_are_retained(self):
        row = {
            "offer": {"filename": "[UMSZ-002] Release.zip", "catalog_id_candidate": "UMSZ-002"},
            "archive": {"catalog_id_candidate_evidence": {
                "UMSZ-003": [{"source": "sidecar", "name": "release.nfo"}],
            }},
        }
        release_catalog_candidates(row)
        self.assertIsNone(row["catalog_id_candidate"])
        self.assertEqual(set(row["catalog_id_candidate_evidence"]), {"UMSZ-002", "UMSZ-003"})

    def test_empty_search_is_a_valid_result(self):
        body = b"<html><mainbar><center><h3>No Matches Found.</h3></center></mainbar></html>"
        self.assertEqual(parse_listing(body, 1), {"total_results": 0, "rows_on_page": 0, "releases": []})

    def test_doujinstyle_transport_encoding_overrides_meta(self):
        body = """<html><meta http-equiv="Content-Type" content="text/html; charset=UTF-16">
        <h3>Total Results Found: 1</h3>
        <span class="gridBox"><span title="Music Format: FLAC">FLAC</span>
        <div class="gridDetails">
          <a href="?p=page&amp;type=1&amp;id=25">曲集</a>
          <a href="?p=search&amp;type=artist">サークル</a>
          <a href="?p=search&amp;type=tags">C108</a>
        </div></span></html>""".encode()
        listing = parse_listing(body, 1)
        self.assertEqual(listing["total_results"], 1)
        self.assertEqual(listing["releases"][0]["album"], "曲集")
        self.assertEqual(listing["releases"][0]["technical"]["advertised_format"], "FLAC")

    def test_listing_date_and_release_date_are_distinct(self):
        body = b"""<mainbar><h2>Album</h2><span class="pageSpan1">Artist</span>
        <span class="pageSpan2">Display Credit</span><span class="pageSpan1">Date Added:</span>
        <span class="pageSpan2">Nov. 16th, 2026</span></mainbar>"""
        row, _ = parse_detail(body, 25)
        self.assertEqual(row["listed_at"], "2026-11-16")
        self.assertIsNone(row["release_date"])
        self.assertIsNone(row["album_artist"])
        self.assertIsNone(row["publisher"])


if __name__ == "__main__":
    unittest.main()
