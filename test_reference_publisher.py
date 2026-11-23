"""Integration tests for the same native helper used by foobar2000."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from email.message import Message
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from shutil import which
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import quote
import zipfile
import zlib
from zipfile import ZIP_DEFLATED, ZipFile

from audio_probe import audio_prefix
from catalog_cache import CatalogCache
from native.archive_providers import ARCHIVE_PROVIDERS, ArchiveProvider
from native.catalog_join import enrich_releases
from native.export_library import archive_catalog_number, export, make_descriptor, release_snapshot
from native.pack_search_worker import payload, worker_sources
from native.idle_backfill import page_links, compatibility_page_links
from native.reference_publisher import ReferencePublisher
from native.search_worker import GUIDES_URL, fetch_offer, inspect_archives, open_worker_catalog, run_popular, run_search
from doujin import ds_url, flac_info, inspect_zip, parse_detail, parse_listing, parse_popular, vorbis_comment_tags
from test_site_offers import PAGE


PUBLISHER = os.getenv("DOUJINSTYLE_TEST_PUBLISHER")
INPUT_FIXTURE = os.getenv("DOUJINSTYLE_TEST_INPUT_FIXTURE")


class InstalledAudioBoundaryTests(unittest.TestCase):
    """Verify the packaged worker never silently promotes a missing native probe."""

    def test_missing_native_prefix_never_marks_zip_headers_complete(self):
        from builtins import __import__ as real_import
        from email.message import Message
        wave = (b"RIFF" + struct.pack("<I", 8036) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8) +
                b"data" + struct.pack("<I", 8000) + bytes(8000))
        buffer = BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr("sample.wav", wave)
        zipped = buffer.getvalue()

        class Ranges:
            def get(self, url, *, headers, cap, **unused):
                first, last = headers["Range"][6:].split("-")
                start = int(first) if first else max(0, len(zipped) - int(last))
                end = min(len(zipped), start + cap)
                response = Message()
                response["Content-Range"] = f"bytes {start}-{end - 1}/{len(zipped)}"
                response["ETag"] = '"probe-retry-fixture"'
                return response, zipped[start:end], url

        def no_legacy_audio(name, *args, **kwargs):
            if name == "audio_probe":
                raise ModuleNotFoundError("No audio_probe in installed zipapp", name=name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_legacy_audio):
            result = inspect_zip(Ranges(), "https://download.mediafire.com/test.zip", 262144,
                                 directory_only=True, header_probes=True)
        self.assertFalse(result["header_probe_pass_complete"])
        self.assertEqual(result["audio_probes"]["sample.wav"],
                         {"prefix_status": "native audio prefix helper unavailable; retry indexing"})

    def test_installed_worker_has_no_python_prefix_decoder(self):
        source = Path(__file__).resolve().parent
        members, report = worker_sources(source)
        self.assertNotIn("audio_probe.py", members)
        self.assertLess(report["python_lines"], 3700)
        self.assertNotIn("doujin.py", members)
        self.assertEqual(audio_prefix(b"RIFF", ".wav", 4096, None, None)["prefix_status"],
                         "no supported audio header in probe")
        with tempfile.TemporaryDirectory() as folder:
            # -I excludes the source directory: this tests the bytes users install,
            # not Python's implicit access to the developer's reference parser.
            app = Path(folder) / "installed.pyz"
            with ZipFile(app, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            # A planted same-name local module must never execute in the worker.
            (Path(folder) / "audio_probe.py").write_text(
                "raise AssertionError('untrusted audio_probe import executed')\n", encoding="utf-8")
            code = ("import sys; sys.path.insert(0, sys.argv[2]); sys.path.insert(0, sys.argv[1]); "
                    "from doujin import compatibility_audio_prefix")
            outcome = subprocess.run([sys.executable, "-I", "-c", code, str(app), folder],
                                     cwd=folder, text=True, capture_output=True, timeout=10)
            self.assertNotEqual(outcome.returncode, 0)
            self.assertIn('No module named', outcome.stderr)
            self.assertNotIn('untrusted audio_probe', outcome.stderr)


@unittest.skipUnless(PUBLISHER, "Set DOUJINSTYLE_TEST_PUBLISHER to the compiled reference publisher")
class NativePublisherTests(unittest.TestCase):
    member = "夜桜/01 春.flac"
    dated = "2019/10"

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="doujin-桜-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.database = self.root / "カタログ.sqlite"
        with closing(CatalogCache(self.database)) as cache:
            copy = cache.add_copy({"id": 12, "album": "夜桜", "tags": ["Techno"]}, {},
                                  "https://example.test/archive.zip")
            cache.put_archive(copy, {"format": "zip", "inventory": [{"path": self.member}, {"path": "02.wav"}],
                                     "audio_probes": {self.member: {"tags": {"DATE": ["2019-10-27"]}}}}, None)
        self.filename = f"12-{sha256(self.member.encode()).hexdigest()[:24]}.djt"

    def publish(self, output, native=True):
        publisher = ReferencePublisher(PUBLISHER) if native else None
        with closing(CatalogCache(self.database, structured_references=True, publisher=publisher)) as cache:
            return export(cache, output, [12])

    def test_native_batch_publisher_preserves_notifications_and_rejects_bad_ids(self):
        from native.search_worker import export as bundled_export
        target = self.root / "batched tracks"
        seen, changed = [], []
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            with closing(CatalogCache(self.database, structured_references=True, publisher=publisher)) as cache:
                first = bundled_export(cache, target, [12, 999999],
                                       on_reference=lambda release_id, filename: seen.append((release_id, filename)),
                                       on_updated=lambda release_id, filename: changed.append((release_id, filename)))
                self.assertEqual(first["references"], 2)
                self.assertEqual(first["written"], 2)
                self.assertEqual(first["skipped_releases"], 1)
                self.assertEqual(changed, seen)
                second = bundled_export(cache, target, [12, 999999])
                self.assertEqual(second, {"references": 2, "written": 0, "skipped_releases": 1})
                existing = next(target.rglob("*.djt"))
                moved = target / "2025" / "09" / existing.name
                moved.parent.mkdir(parents=True, exist_ok=True)
                existing.rename(moved)
                # Batch publication must honor moved playlist references, too.
                third = bundled_export(cache, target, [12])
                self.assertEqual(third["written"], 0)
                self.assertTrue(moved.is_file())
                self.assertEqual(len(list(target.rglob("*.djt"))), 2)
                with self.assertRaisesRegex(OSError, "invalid release ID"):
                    publisher.request(self.database, "publish_many", ids=[12, -1],
                                      output=str(target), root=str(target), copy_id=None)
                with self.assertRaisesRegex(OSError, "Invalid publication batch size"):
                    publisher.request(self.database, "publish_many", ids=list(range(4097)),
                                      output=str(target), root=str(target), copy_id=None)
                self.assertEqual(len(list(target.rglob("*.djt"))), 2)

    def test_native_browse_link_projection_matches_python_and_falls_back_on_worker_failure(self):
        page = (b'<meta name="referrer" content="origin"><div class="pageScroll">'
                b'<a href="?p=home&amp;page=7">7</a>'
                b'<a href="?p=home&amp;page=6" rel="noreferrer">6</a>'
                b'<a href="?p=home&amp;page=5" referrerpolicy="same-origin">5</a>'
                b'<a href="https://evil.invalid/?p=home&amp;page=9">9</a></div>'
                b'<a href="?p=home&amp;page=3">outside</a>')
        url = "https://doujinstyle.com/?p=home&page=8"
        headers = Message()
        headers["Referrer-Policy"] = "strict-origin"
        expected = compatibility_page_links(page, url, headers)
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            with patch("native.idle_backfill.compatibility_page_links",
                       side_effect=AssertionError("Python lxml projection was reached")):
                self.assertEqual(page_links(page, url, headers, publisher=publisher), expected)
        worker = Mock()
        worker.request.side_effect = OSError("native helper unavailable")
        self.assertEqual(page_links(page, url, headers, publisher=worker), expected)
        self.assertEqual(worker.request.call_count, 1)

    def test_matches_python_output_and_keeps_an_existing_moved_reference(self):
        native = self.root / "日本語 tracks"
        baseline = self.root / "python tracks"
        self.assertEqual(self.publish(native), self.publish(baseline, native=False))
        files = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*.djt")}
        self.assertEqual(files(native), files(baseline))
        self.assertIn(Path(self.dated) / self.filename, files(native))
        self.assertEqual(self.publish(native)["written"], 0)
        new = native / "2025" / "09" / self.filename
        new.parent.mkdir(parents=True)
        (native / self.dated / self.filename).rename(new)
        self.assertEqual(self.publish(native)["written"], 0)
        self.assertEqual(list(native.rglob(self.filename)), [new])
        with closing(CatalogCache(self.database, read_only=True)) as cache:
            saved = [r[0] for r in cache.db.execute("SELECT path FROM published_references WHERE filename=?",
                                                   (self.filename,))]
            self.assertCountEqual(saved, [f"{self.dated}/{self.filename}", f"2025/09/{self.filename}"])

    def test_concurrent_publishers_and_uncommitted_record_recovery(self):
        output = self.root / "tracks"
        with closing(CatalogCache(self.database)) as cache:
            with cache.db:
                cache.db.execute("CREATE TRIGGER fail_publication BEFORE INSERT ON published_references "
                                 "BEGIN SELECT RAISE(ABORT,'fixture interruption'); END")
            with self.assertRaisesRegex(OSError, "fixture interruption"):
                self.publish(output)
            self.assertEqual(cache.db.execute("SELECT count(*) FROM published_references").fetchone()[0], 0)
            self.assertEqual(len(list(output.rglob("*.djt"))), 2)
            with cache.db:
                cache.db.execute("DROP TRIGGER fail_publication")
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.publish, [output, output]))
        self.assertEqual([r["written"] for r in results], [0, 0])
        with closing(CatalogCache(self.database, read_only=True)) as cache:
            self.assertEqual(cache.db.execute("SELECT count(*) FROM published_references").fetchone()[0], 2)
            self.assertEqual(cache.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_embedded_worker_uses_native_publisher(self):
        app = self.root / "worker.pyz"
        source = Path(__file__).resolve().parent
        app.write_bytes(payload(source, Path(PUBLISHER)))
        output = self.root / "tracks"
        self.seed_publication_search()
        # The installed zipapp now refuses foreground queries: the plugin
        # launches the same native executable used by catalog operations.
        legacy = subprocess.run([sys.executable, str(app), "--cache", str(self.database),
                                 "--output", str(output), "--query", "Offline fixture"],
                                capture_output=True, timeout=15)
        self.assertNotEqual(legacy.returncode, 0)
        self.assertIn(b"Foreground search is native-only", legacy.stdout)
        self.assertFalse(list(output.rglob("*.djt")))
        result = subprocess.run([PUBLISHER, "--foreground-search", "--cache",
                                 str(self.database), "--output", str(output),
                                 "--query=Offline fixture", "--pages", "1", "--result-rows"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertIn(b"Done. 1 listings", result.stdout)
        self.assertEqual(len(list(output.rglob("*.djt"))), 2)
        with ZipFile(app) as bundle:
            self.assertNotIn("native/catalog_join.py", bundle.namelist())
            self.assertNotIn(b"--enrich-releases", bundle.read("native/search_worker.py"))
            self.assertNotIn("native/export_library.py", bundle.namelist())
            self.assertNotIn(b"def claim_lookup(", bundle.read("catalog_cache.py"))
            self.assertNotIn(b"from native.export_library import", bundle.read("native/search_worker.py"))
            self.assertNotIn(b"def restore_snapshot(", bundle.read("catalog_cache.py"))
            self.assertNotIn(b"def song_search(", bundle.read("catalog_cache.py"))
            self.assertNotIn("native/worker_scope.py", bundle.namelist())
            self.assertNotIn("audio_probe.py", bundle.namelist())
            self.assertNotIn("doujin.py", bundle.namelist())
            self.assertNotIn("native/archive_zip.py", bundle.namelist())
            self.assertNotIn("native/idle_backfill.py", bundle.namelist())
            self.assertNotIn("native/archive_providers.py", bundle.namelist())
            self.assertFalse(any(b"native.worker_scope" in bundle.read(name) for name in bundle.namelist()
                                 if name.endswith(".py")))
            _, report = worker_sources(source)
            self.assertEqual(report["python_lines"], sum(len(bundle.read(name).splitlines())
                                                         for name in bundle.namelist() if name.endswith(".py")))
            self.assertEqual(report["python_lines"],
                             report["unmarked_python_lines"] + report["fallback_python_lines"])
            # Deprecation is not deletion: these lines MUST remain in the
            # installed runtime until their native replacements have parity.
            self.assertLessEqual(report["deprecated_active_lines"], 300)
            self.assertLessEqual(report["python_lines"], 1400)
            self.assertIn("run_idle_layers", report["modules"]["native/search_worker.py"]["standalone"])
            self.assertNotIn(b"def run_idle_layers(", bundle.read("native/search_worker.py"))
            self.assertGreater(report["unmarked_python_lines"], report["deprecated_active_lines"])
            self.assertIn("schedule_index", report["modules"]["native/search_worker.py"]["standalone"])
            self.assertTrue({"run_search", "run_popular", "prefetch_charts", "_consume_native_chart"}
                            <= set(report["modules"]["native/search_worker.py"]["standalone"]))
            self.assertNotIn(b"def run_search(", bundle.read("native/search_worker.py"))
            self.assertNotIn(b"runtime_deprecated", bundle.read("native/search_worker.py"))
            # This is an upper bound for subsequent iterations, not our zero-Python goal.
            self.assertLessEqual(report["python_lines"], 3028)
            self.assertTrue({"CatalogCache.depth_report", "CatalogCache.storage_report"}
                            <= set(report["modules"]["catalog_cache.py"]["standalone"]))
            self.assertTrue({"index_status", "cached_tags"}
                            <= set(report["modules"]["native/search_worker.py"]["standalone"]))
            for obsolete in (b"def index_status(", b"def cached_tags(", b"--index-status", b"--cached-tags"):
                self.assertNotIn(obsolete, bundle.read("native/search_worker.py"))
            for obsolete in (b"def depth_report(", b"def storage_report("):
                self.assertNotIn(obsolete, bundle.read("catalog_cache.py"))
            self.assertIn("ReferencePublisher.publish", report["modules"]["native/reference_publisher.py"]["standalone"])
            self.assertNotIn(b"--join-bangumi", bundle.read("native/search_worker.py"))
            self.assertNotIn("doujin.py", report["modules"])
            self.assertTrue({"compatibility_search_page", "fetch_python_offers", "resolve_archive_offers"}
                            <= set(report["modules"]["native/search_worker.py"]["standalone"]))
            self.assertNotIn(b"def release_offer_session(", bundle.read("native/search_worker.py"))

    def test_trace_packaged_native_and_fail_closed_publication(self):
        source = Path(__file__).resolve().parent
        failed_publisher = self.root / "unavailable-worker"
        failed_publisher.write_bytes(b"intentionally invalid executable for recovery test")
        outputs = {}
        qualified_names = sys.version_info >= (3, 11)
        with closing(CatalogCache(self.database)) as cache:
            copy = cache.copies_for(12)[0]
            archive = copy["archive"]
            archive["inventory"][0]["zip_name"] = {"raw": b"legacy.flac".hex()}
            cache.put_archive(copy["copy_id"], archive, None)
        self.seed_publication_search()
        # Run recovery before the native writer can enable WAL. Older Python
        # SQLite builds can exercise this fixture in rollback mode.
        for name, publisher in (("native", Path(PUBLISHER)),):
            app = self.root / f"{name}.pyz"
            app.write_bytes(payload(source, publisher))
            output = self.root / name
            report = self.root / f"{name}.json"
            lines = self.root / f"{name}-lines.json"
            traced = subprocess.run([sys.executable, str(source / "native/trace_worker.py"),
                            "--report", str(report), "--line-report", str(lines), str(app), "--cache", str(self.database),
                            "--output", str(output), "--query", "Offline fixture"],
                           capture_output=True, timeout=15)
            self.assertNotEqual(traced.returncode, 0)
            # Real publication is now in C++, not in this embedded Python
            # query launcher. The source-only implementation remains covered
            # by the independent native publisher parity tests.
            native = subprocess.run([PUBLISHER, "--foreground-search", "--cache",
                                     str(self.database), "--output", str(output),
                                     "--query=Offline fixture", "--pages", "1", "--result-rows"],
                                    capture_output=True, timeout=15)
            self.assertEqual(native.returncode, 0, (native.stdout, native.stderr))
            functions = set(map(tuple, json.loads(report.read_text(encoding="utf-8"))))
            line_report = json.loads(lines.read_text(encoding="utf-8"))
            counts = line_report["modules"]
            self.assertGreater(line_report["executed_python_lines"], 0)
            self.assertEqual(line_report["executed_python_lines"], sum(map(len, counts.values())))
            with ZipFile(app) as bundle:
                for file, numbers in counts.items():
                    self.assertEqual(numbers, sorted(set(numbers)))
                    self.assertTrue(all(1 <= n <= len(bundle.read(file).splitlines()) for n in numbers))
            python_writer = ("catalog_cache.py", "CatalogCache.initialize" if qualified_names else "initialize")
            python_exporter = ("native/export_library.py", "publish_python_references")
            projection = ("native/export_library.py", "make_descriptor")
            filename = ("native/export_library.py", "descriptor_filename")
            location = ("native/export_library.py", "reference_location")
            if name == "native":
                self.assertNotIn(("native/search_worker.py", "export"), functions)
                self.assertNotIn(("native/search_worker.py", "run_search"), functions)
                self.assertIn(("native/reference_publisher.py", "ReferencePublisher.request"), functions)
                self.assertNotIn(("native/export_library.py", "export"), functions)
                self.assertNotIn(python_writer, functions)
                self.assertNotIn(python_exporter, functions)
                self.assertNotIn(("native/export_library.py", "release_snapshot"), functions)
                self.assertTrue({projection, filename, location}.isdisjoint(functions))
            else:
                self.assertIn(python_writer, functions)
                self.assertIn(python_exporter, functions)
                self.assertIn(("native/export_library.py", "release_snapshot"), functions)
                self.assertTrue({projection, filename, location} <= functions)
            outputs[name] = {p.relative_to(output): p.read_bytes() for p in output.rglob("*.djt")}
        self.assertEqual(len(outputs["native"]), 2)
        # Source parity is still covered by test_matches_python_output_and_keeps_an_existing_moved_reference.
        app = self.root / "fail_closed.pyz"
        app.write_bytes(payload(source, failed_publisher))
        result = subprocess.run([sys.executable, str(app), "--cache", str(self.database),
                                 "--output", str(self.root / "forbidden"), "--query", "Offline fixture"],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list((self.root / "forbidden").rglob("*.djt")))

    def seed_publication_search(self):
        url = ds_url(p="search", source=1, type="blanket", result="Offline fixture", page=0)
        with closing(CatalogCache(self.database)) as cache:
            cache.put_page(url, {"releases": [cache.listing(12)], "total_results": 1, "rows_on_page": 1})
            with cache.db:
                cache.db.execute("UPDATE releases SET offer_checked=? WHERE id=12", (int(time.time()),))

    def test_native_projection_matches_unicode_metadata_and_legacy_placement(self):
        release = {"id": 26, "album": "Ｆｉｘｔｕｒｅ", "artist": "Listing",
                   "tags": ["Ｔｅｃｈｎｏ", "techno", "Ｍ３–５４"], "listed_at": "2025/1/2",
                   "catalog_id": "TH-121", "url": "https://doujinstyle.com/?p=page&id=26"}
        detail = "disc\\01 - Straße - 夜桜.flac"
        other = "disc/② - Other.wav"
        old_url = "https://example.test/音 mix.zip?x=%2B"
        alias = "disc/01 - Old path.flac"
        raw = b"disc\\01 - Caf\x82.flac"
        archive = {"format": "zip", "inventory": [
            {"path": detail, "uncompressed_bytes": 8000, "compressed_bytes": 4000, "zip_method": 8,
             "zip_header_offset": 42, "crc32": "abcdef01", "zip_name": {
                 "raw": raw.hex(), "legacy_unicode_path": alias}},
            {"path": other, "uncompressed_bytes": 6000, "compressed_bytes": 6000},
            {"path": "__MACOSX/disc/._Other.wav"}, {"path": "disc/unsupported?.flac"}],
            "audio_probes": {detail: {
                "tags": {"TITLE": ["Straße", "STRASSE"], "ALBUMARTIST": [" Circle　"],
                         "COMMENT": ["C１０８", "note\nline"], "CATALOGNUMBER": ["M3-54", "ＡＡ-12"]},
                "metadata": {"duration_seconds": 0.0625, "channels": 2, "codec": "FLAC"}},
                other: {"tags": {"CATALOG NUMBER": ["TT‐02"], "ALBUM ARTIST": ["circle"]}}}}
        paths = [self.root / "legacy", self.root / "native"]
        survivor = "2020/01/" + f"26-{sha256(alias.encode()).hexdigest()[:24]}.djt"
        for path in paths:
            (path / survivor).parent.mkdir(parents=True)
            (path / survivor).write_text("previous description", encoding="utf-8")
        with closing(CatalogCache(self.database)) as cache:
            copy = cache.add_copy(release, {}, old_url)
            cache.put_archive(copy, archive, None)
            with cache.db:
                cache.put_tracklist(26, "https://example.test/tracks", [
                    {"title": "夜桜", "artist": "Track Artist", "number": 1},
                    {"title": "Other", "artist": "Other Artist", "number": 2}])
            expected = export(cache, paths[0], [26], structured=True)
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            actual = export(cache, paths[1], [26], structured=True)
            self.assertEqual(export(cache, paths[1], [26], structured=True)["written"], 0)
        self.assertEqual(expected, actual)
        content = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.djt")}
        self.assertEqual(len(content(paths[0])), 2)
        self.assertIn(survivor, content(paths[1]))
        self.assertTrue(any(b"TRACKNUMBER=2\n" in data for data in content(paths[1]).values()))
        self.assertEqual(content(paths[0]), content(paths[1]))

    def test_native_publication_snapshot_preserves_copy_and_tracklist_selection(self):
        release = {"id": 25, "album": "Fixture", "url": ds_url(p="page", type=1, id=25)}
        archive = {"format": "zip", "inventory": [{"path": "track.flac"}]}
        primary_url = "https://mega.nz/file/ABCDEFGH#" + "A" * 43
        mirror_url = "https://example.test/mirror.zip"
        with closing(CatalogCache(self.database)) as cache:
            mirror = cache.add_copy(release, {}, mirror_url)
            primary = cache.put_offer(25, {}, primary_url, None)
            cache.put_archive(primary, archive, None)
            cache.put_archive(mirror, archive, None)
            with cache.db:
                cache.put_tracklist(25, "https://example.test/b", [{"title": "一", "number": 1}, {"title": "二"}])
                cache.put_tracklist(25, "https://example.test/a", [{"number": 1, "title": "一"}])
            with closing(ReferencePublisher(PUBLISHER)) as native:
                for selected in (None, primary, mirror, -1):
                    expected = release_snapshot(cache, 25, selected)
                    self.assertEqual(native.request(cache.path, "publication_snapshot", id=25, copy_id=selected),
                                     list(expected) if expected is not None else None)
                baseline = native.request(cache.path, "publication_snapshot", id=25)
                self.assertEqual(baseline[1], primary_url)
                self.assertEqual(len(baseline[3]), 2)
                with cache.write_transaction():
                    cache.db.execute("UPDATE copies SET archive_error='failed' WHERE id=?", (primary,))
                    self.assertEqual(native.request(cache.path, "publication_snapshot", id=25), baseline)
                self.assertEqual(native.request(cache.path, "publication_snapshot", id=25)[1], mirror_url)

    @unittest.skipUnless(INPUT_FIXTURE, "Set DOUJINSTYLE_TEST_INPUT_FIXTURE to the compiled input fixture")
    def test_published_stored_and_deflated_members_open_through_native_input_code(self):
        for method, placeholder, raw, name in ((zipfile.ZIP_STORED, "track.wav", b"track.wav", "track.wav"),
                                              (zipfile.ZIP_DEFLATED, "cafe.wav", b"caf\x82.wav", "café.wav")):
            with self.subTest(method=method):
                buffer = BytesIO()
                with ZipFile(buffer, "w", method) as archive:
                    archive.writestr(placeholder, b"fixture audio bytes" * 50)
                data = buffer.getvalue().replace(placeholder.encode("ascii"), raw)
                (self.root / "archive.zip").write_bytes(data)
                release = {"id": 25 + method, "album": "Fixture", "url": ds_url(p="page", type=1, id=25 + method)}
                with ZipFile(BytesIO(data)) as archive:
                    member = archive.getinfo(name)
                    item = {"path": name, "compressed_bytes": member.compress_size, "uncompressed_bytes": member.file_size,
                            "zip_method": method, "zip_header_offset": member.header_offset, "crc32": f"{member.CRC:08x}",
                            "zip_name": {"raw": raw.hex()}}
                output = self.root / str(method)
                with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
                    copy = cache.add_copy(release, {}, f"https://example.test/{method}.zip")
                    cache.put_archive(copy, {"format": "zip", "inventory": [item]}, None)
                    self.assertEqual(export(cache, output, [release["id"]])["written"], 1)
                track, = output.glob("*.djt")
                subprocess.run([str(Path(INPUT_FIXTURE).resolve()), str(track.relative_to(self.root)), "archive.zip"],
                               cwd=self.root, check=True, capture_output=True, timeout=10)

    def test_packaged_idle_worker_requires_native_writer_and_refreshes_status(self):
        # Native idle entrypoint MUST work without Python; the old zipapp mode
        # is intentionally rejected instead of silently restoring an adapter.
        source = Path(__file__).resolve().parent
        bad_exe = self.root / "unavailable-worker"
        bad_exe.write_bytes(b"invalid native helper")
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            cache.put_observation({"id": 12, "listed_at": "2025-01-31"})
        output = self.root / 'published'
        for publisher in (bad_exe, Path(PUBLISHER)):
            app = self.root / "idle.pyz"
            app.write_bytes(payload(source, publisher))
            old = subprocess.run([sys.executable, str(app), "--cache", str(self.database),
                                  "--idle-backfill", "--idle-layers", "2"],
                                 capture_output=True, timeout=15)
            self.assertNotEqual(old.returncode, 0)
            self.assertNotIn(b"Selected idle indexing pass finished.", old.stdout)
        run = subprocess.run([str(Path(PUBLISHER)), "--idle-backfill", "--cache", str(self.database),
                              "--output", str(output), "--idle-layers", "2"],
                             capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([line for line in run.stdout.splitlines() if line.startswith(b"\x1b")],
                         [b"\x1b", b"\x1b"])
        self.assertIn(b"Selected idle indexing pass finished.", run.stdout)

    def test_worker_source_pack_rejects_imports_of_removed_tools(self):
        import shutil
        source = self.root / "project"
        shutil.copytree(Path(__file__).resolve().parent, source, ignore=shutil.ignore_patterns("__pycache__"))
        with (source / "native/search_worker.py").open("a", encoding="utf-8") as worker:
            worker.write("\nfrom doujin import dojin_info\n")
        with self.assertRaisesRegex(ValueError, "installed worker imports retired module doujin"):
            worker_sources(source)

    def test_site_discovery_persists_native_search_and_chart_pages(self):
        listing = """<html><head><link rel="shortcut icon" href="./theme/site.png"></head>
        <mainbar><b>Total Results Found: 1</b>
        <script>const fake = '<div class="gridBox">';</script>
        <span class="gridBox"><div title="Music Format: FLAC">FLAC</div><div class="gridDetails">
        <a href="./?p=page&amp;type=1&amp;id=25">夜桜 &amp; Caf&eacute; &#x1f338;</a>
        <a href="?p=search&amp;type=artist">サークル</a><a href="?p=search&amp;type=tags">Ｍ３－５８</a>
        <form method="post"><input type="hidden" name="id" value="25">
        <input type="hidden" name="type" value="1"><input type="hidden" name="source" value="2">
        <button name="download_link">Download</button></form></div></span></mainbar></html>""".encode()
        chart = b"""<head><link rel="icon" href="/theme/ranking.ico"></head><mainbar></mainbar><sidebar>
          <h3>This Month</h3>
          <div><a href="?p=page&amp;type=1&amp;id=25">Spring chart</a><span>Artist A</span><b>1,280 downloads</b></div>
          <div><a href="?p=page&amp;type=1&amp;id=26">Number two</a><span>Artist B</span><b>47 downloads</b></div>
          <h3>All-Time</h3>
          <div><a href="?p=page&amp;type=1&amp;id=33">All-time lead</a><span>Artist C</span><b>13,820 downloads</b></div>
          <div><a href="?p=page&amp;type=1&amp;id=25">All-time repeat</a><span>Artist A</span><b>12,400 downloads</b></div>
        </sidebar>"""
        url = ds_url(p="search", source=1, type="blanket", result="桜", page=0)
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            worker = cache.publisher
            cold = worker.request(self.database, "site_search", url=url, html=listing.hex())
            self.assertEqual(cold, {"snapshot": parse_listing(listing, 20, True), "cache_hit": False})
            warm = worker.request(self.database, "site_search", url=url, html=b"wrong page".hex())
            self.assertEqual(warm, {**cold, "cache_hit": True})
            with self.assertRaisesRegex(OSError, "recognizable catalog cards"):
                worker.request(self.database, "site_search", url=url, html=b"wrong page".hex(), refresh=True)
            with self.assertRaisesRegex(OSError, "Unexpected DoujinStyle search URL"):
                worker.request(self.database, "site_search", url="https://doujinstyle.com.evil.test/?p=search",
                               html=listing.hex(), refresh=True)
            self.assertEqual(cache.listing(25)["album"], "夜桜 & Café 🌸")
            parsed, icon = parse_popular(chart)
            expected = {"charts": parsed, "site_icon": icon, "releases": []}
            self.assertEqual(worker.request(self.database, "site_charts", html=chart.hex()), expected)
            self.assertEqual(cache.page_snapshot(GUIDES_URL)[0], expected)
            self.assertEqual(cache.listing(25)["album"], "夜桜 & Café 🌸")
            self.assertEqual(cache.listing(33)["album"], "All-time lead")
            with self.assertRaisesRegex(OSError, "both recognizable"):
                worker.request(self.database, "site_charts", html=b"<h3>This Month</h3>".hex())
            self.assertEqual(cache.page_snapshot(GUIDES_URL)[0], expected)
            self.assertEqual(cache.page_snapshot(url)[0], cold["snapshot"])

            rows = []
            with (patch("native.search_worker.discovery_worker", return_value=worker),
                  patch("native.search_worker.cached_listing", side_effect=AssertionError("Python listing reader called")),
                  patch("native.search_worker.index_results") as index):
                run_search(None, cache, "桜", 3, self.root, lambda _: None, emit_result=rows.append)
                self.assertEqual(list(index.call_args.args[2]), [25])
                self.assertEqual(rows[0]["album"], "夜桜 & Café 🌸")
                rows.clear()
                run_popular(None, cache, "all", self.root, lambda _: None, emit_result=rows.append)
                self.assertEqual([(row["id"], row["chart_rank"]) for row in rows], [(33, 1), (25, 2)])

    def test_foreground_search_pages_preserve_order_and_partial_results(self):
        def html(*ids):
            cards = "".join(f'<div class="gridBox"><div class="gridDetails">'
                            f'<a href="/?p=page&amp;type=1&amp;id={release}">Track {release}</a>'
                            f'</div></div>' for release in ids)
            return f"<mainbar>{cards} Total Results Found: 61</mainbar>".encode()

        query = "桜 + & mix"
        pages = {1: html(25, 25), 2: html(25, 26), 3: b"<mainbar>Unavailable</mainbar>"}
        def url(page):
            return ds_url(p="search", source=1, type="tags", result=query, page=page - 1)

        snapshots = []
        for mode in ("python", "native"):
            with self.subTest(mode=mode):
                database = self.root / f"{mode}.sqlite"
                publisher = ReferencePublisher(PUBLISHER) if mode == "native" else None
                with closing(CatalogCache(database, publisher=publisher)) as cache, closing(sqlite3.connect(database)) as reader:
                    native_request = publisher.request if publisher else None
                    sent = []
                    visible, messages = [], []
                    warm = False

                    def request(path, operation, **params):
                        if operation == "site_search_job":
                            # Native C++ now owns all pages in one request. A
                            # deliberately missing page exercises its partial
                            # result/error contract without Python GET retry.
                            params["fixture_pages"] = ({url(page): body.hex()
                                for page, body in pages.items() if page != 3} if not warm else {})
                            callback = params["on_event"]
                            def observed(event):
                                sent.append((event["page"], [row["id"] for row in event["batch"]["releases"]]))
                                callback(event)
                            params["on_event"] = observed
                        return native_request(path, operation, **params)

                    def fetch(target):
                        for page, body in pages.items():
                            if target == url(page):
                                if page == 3:
                                    raise OSError("Fixture unavailable")
                                return {}, body, target
                        raise AssertionError(target)

                    def show(row):
                        committed = reader.execute("SELECT listing FROM releases WHERE id=?", (row["id"],)).fetchone()
                        self.assertEqual({key: value for key, value in row.items()
                                          if key != "_native_ui_date"}, json.loads(committed[0]))
                        visible.append(row["id"])

                    http = Mock()
                    http.get.side_effect = fetch
                    with (patch("native.search_worker.discovery_worker", return_value=publisher),
                          patch("native.search_worker.index_results") as index,
                          patch.object(publisher, "request", side_effect=request) if publisher else nullcontext()):
                        run_search(http, cache, f"TAG: {query}", 3, self.root, messages.append, emit_result=show)
                        self.assertEqual(visible, [25, 26])
                        self.assertEqual(list(index.call_args.args[2]), visible)
                        self.assertTrue(any("Results page 3 unavailable" in line for line in messages))
                        self.assertEqual([call.args[0] for call in http.get.call_args_list],
                                         [] if publisher else [url(1), url(2), url(3)])
                        if publisher:
                            self.assertEqual(sent, [(1, [25]), (2, [26])])
                        self.assertIsNone(cache.page_snapshot(url(3))[0])
                        snapshots.append([cache.page_snapshot(url(page))[0] for page in (1, 2)])
                        warm = True
                        visible.clear()
                        messages.clear()
                        http.get.reset_mock()
                        run_search(http, cache, f"TAG: {query}", 2, self.root, messages.append, emit_result=show)
                        self.assertEqual(visible, [25, 26])
                        http.get.assert_not_called()
                        mode_label = "cached by native catalog" if publisher else "cached"
                        self.assertEqual(len(messages), 2)
                        self.assertTrue(all(line.endswith(mode_label) for line in messages))
        self.assertEqual(snapshots[0], snapshots[1])

    def test_catalogid_alias_publishes_and_supplies_bangumi_evidence(self):
        release = {"id": 10989, "album": "Aboot Phaze ～Notebook Aboot～",
                   "artist": "NOTEBOOK RECORDS (RoughSketch vs MAD CHILD)", "listed_at": "2020-06-05"}
        source = {"path": "Disc/05.flac"}
        invalid = {"path": "Other/01.flac"}
        url = "https://example.test/fixture.zip"
        archive = {"format": "zip", "inventory": [source, invalid], "audio_probes": {
            source["path"]: {"tags": {"CATALOGID": ["10989", "nbcd‑025"]}},
            invalid["path"]: {"tags": {"CATALOGID": ["10989", "C90"]}},
        }}
        subject = {"name": release["album"], "type": 3, "infobox": [
            {"key": "artist", "value": "RoughSketch vs MAD CHILD"},
            {"key": "catalog number", "value": "NBCD-025"},
        ]}
        output = self.root / "tracks"
        self.assertEqual(archive_catalog_number(archive), "NBCD-025")
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            copy = cache.add_copy(release, {}, url)
            cache.put_archive(copy, archive, None)
            self.assertEqual(export(cache, output, [10989], structured=True)["written"], 2)
            refs = {path.read_text("utf-8").split("MEMBER=", 1)[1].splitlines()[0]: path
                    for path in output.rglob("10989-*.djt")}
            for item in (source, invalid):
                expected = make_descriptor(release, item, url, archive, [],
                                           album_catalog="NBCD-025" if item == source else None)
                rendered = refs[item["path"]].read_text("utf-8")
                self.assertEqual(rendered, expected)
                self.assertEqual("CATALOG SOURCE TRACK=NBCD-025\n" in rendered, item == source)
                self.assertNotIn("CATALOGNUMBER=10989\n", rendered)
            result = cache.publisher.request(self.database, "bangumi", ids=[10989], output=str(output), responses=[
                {"status": 200, "json": {"data": [{"id": 55, "name": release["album"]}]}},
                {"status": 200, "json": subject},
            ])
            self.assertEqual(result["messages"], ["  10989   Matched Bangumi album 55"])
            self.assertEqual(cache.listing(10989)["external_lookup"]["bangumi"]["fields"]["catalog_id"], "NBCD-025")
            self.assertIn("BANGUMI URL=https://bgm.tv/subject/55", refs[source["path"]].read_text("utf-8"))

    def test_native_bangumi_matches_python_and_refreshes_a_moved_reference(self):
        release = {"id": 12, "album": "夜桜 (Ｎｏｉｓｅ)", "artist": "Circle (Ａｒｔｉｓｔ)",
                   "tags": ["M3-54"], "listed_at": "2024-11-29"}
        subject = {"name": "夜桜 (Noise)", "type": 3, "date": "2024-10-27",
                   "infobox": [{"key": "艺术家", "value": [{"v": "artist"}]},
                               {"key": "厂牌", "value": "Fixture Label"},
                               {"key": "发售日期", "value": "2024-10-27 (M3‑54)"}]}
        responses = [{"status": 403}, {"status": 200, "json": {"list": [{"id": 55, "name": subject["name"]}]}},
                     {"status": 200, "json": subject}]
        output = self.root / "トラック"
        reference_db = self.root / "python-reference.sqlite"
        with closing(CatalogCache(reference_db)) as cache:
            cache.put_listing(release)

            class FixtureHttp:
                def get(self, url, **kwargs):
                    reply = expected_responses.pop(0)
                    if reply["status"] != 200:
                        raise HTTPError(url, reply["status"], "fixture", None, None)
                    return {}, json.dumps(reply["json"]).encode(), url

            expected_responses = responses.copy()
            enrich_releases(cache, [12], lambda _: None, http=FixtureHttp())
            expected = cache.listing(12)["external_lookup"]["bangumi"]
        self.assertIn("url", expected)
        self.assertEqual(expected_responses, [])

        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            cache.put_observation(release)
            self.assertEqual(export(cache, output, [12], structured=True)["written"], 2)
            original = next(path for path in output.rglob("*.djt") if "春.flac" in path.read_text("utf-8"))
            moved = output / "2020" / "01" / original.name
            moved.parent.mkdir(parents=True)
            original.rename(moved)
            worker = cache.publisher

            now = int(time.time())
            self.assertTrue(cache.claim_lookup("bangumi", 12, "active-fixture", now + 120, now))
            claimed = worker.request(self.database, "bangumi", ids=[12], output=str(output), responses=[])
            self.assertEqual(claimed["requests"], [])
            self.assertNotIn("external_lookup", cache.listing(12))
            cache.release_lookup("bangumi", 12, "active-fixture")

            result = worker.request(self.database, "bangumi", ids=[12], output=str(output), responses=responses)
            self.assertEqual([request["path"] for request in result["requests"]], [
                "/v0/search/subjects?limit=8", "/search/subject/" + quote(release["album"], safe="") + "?type=3&max_results=8",
                "/v0/subjects/55"])
            self.assertEqual(result["requests"][0]["body"]["filter"], {"type": [3]})
            self.assertEqual(result["messages"], ["  12   Matched Bangumi album 55"])
            actual = cache.listing(12)["external_lookup"]["bangumi"]
            expected.pop("checked")
            self.assertGreaterEqual(actual.pop("checked"), now)
            self.assertEqual(actual, expected)
            self.assertEqual(cache.db.execute("SELECT count(*) FROM catalog_lookup_claims").fetchone()[0], 0)
            self.assertIn([12, moved.relative_to(output).as_posix()], result["references"])
            saved = moved.read_bytes()
            self.assertIn(b"LABEL=Fixture Label\n", saved)
            self.assertIn(b"DOUJINSTYLE DATE ADDED=2024-11-29\n", saved)
            self.assertFalse(original.exists())
            warm = worker.request(self.database, "bangumi", ids=[12], output=str(output), responses=[])
            self.assertEqual(warm, {"messages": [], "references": [], "requests": []})

            moved.write_bytes(b"stale")
            command = subprocess.run([PUBLISHER, "--bangumi", str(self.database), str(output), "12"],
                                     check=True, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(command.stdout, f"\x1912\t{moved.relative_to(output).as_posix()}\n")
            self.assertEqual(moved.read_bytes(), saved)

    def test_native_bangumi_ambiguous_match_and_rate_limit(self):
        subject = {"name": "Fixture", "type": 3, "infobox": [{"key": "artist", "value": "Artist"},
                                                                            {"key": "event", "value": "M3-54"}]}
        responses = [{"status": 200, "json": {"data": [{"id": 77, "name": "Fixture"}, {"id": 88, "name": "Fixture"}]}},
                     {"status": 200, "json": subject}, {"status": 200, "json": subject}, {"status": 429}]
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            for identifier in (25, 26, 27):
                release = {"id": identifier, "album": "Fixture", "artist": "Artist", "tags": ["M3-54"]}
                cached = cache.add_copy(release, {}, f"https://example.test/{identifier}.zip")
                cache.put_archive(cached, {"format": "zip", "inventory": []}, None if identifier != 27 else "bad")
            self.assertEqual(cache.publisher.request(self.database, "bangumi_idle"), [26, 25, 12])
            outcome = cache.publisher.request(self.database, "bangumi", ids=[25, 26, 27], output=str(self.root / "tracks"),
                                              responses=responses)
            self.assertEqual(len(outcome["requests"]), 4)
            self.assertEqual(outcome["messages"], ["  26   Bangumi lookup unavailable (HTTP 429)"])
            self.assertEqual(cache.publisher.request(self.database, "bangumi_idle"), [26, 12])
            self.assertNotIn("url", cache.listing(25)["external_lookup"]["bangumi"])
            for identifier in (26, 27):
                self.assertNotIn("external_lookup", cache.listing(identifier))
            self.assertEqual(cache.db.execute("SELECT count(*) FROM catalog_lookup_claims").fetchone()[0], 0)

    def test_native_release_discovery_keeps_form_submission_semantics(self):
        page = b"""<meta name="referrer" content="unsafe-url, origin"><mainbar><h2>Album &mdash; Test</h2>
        <span class="pageSpan1">Artist:</span><span class="pageSpan2">Display credit</span>
        <span class="pageSpan1">Date Added:</span><span class="pageSpan2">Nov. 16th, 2026</span>
        <span class="pageSpan1">Release Date:</span><span class="pageSpan2">2026-01-21</span>
        <span class="pageSpan1">Catalog No.:</span><span class="pageSpan2">TEST-102</span>
        <form method="get" action="/unused"><input type="hidden" name="id" value="25">
        <input type="hidden" name="type" value="1"><input type="hidden" name="source" value="3">
        <button name="download_link" disabled>Ignore me</button>
        <button name="download_link" formaction="../chosen?slot=3&amp;x=ok" formmethod="POST" value="go">Get</button>
        </form></mainbar>"""
        url = "https://doujinstyle.com/path/release?id=25"
        headers = {"Referrer-Policy": "same-origin"}
        expected, forms = parse_detail(page, 25, url, headers)
        with closing(ReferencePublisher(PUBLISHER)) as worker:
            result = worker.request(None, "site_parse", kind="detail", id=25, url=url,
                                    policy=headers["Referrer-Policy"], html=page.hex())
            self.assertEqual(result, {"row": expected, "forms": forms})
            empty = b"<mainbar><h3>No Matches Found.<br><br>Please click <a href='/list.html'>here</a>.<//h3></mainbar>"
            self.assertEqual(worker.request(None, "site_parse", kind="listing", html=empty.hex()),
                             {"total_results": 0, "rows_on_page": 0, "releases": []})

    def test_malformed_tag_openings_do_not_hide_later_catalog_cards(self):
        page = b"<mainbar><h3>No Matches Found.</h3></mainbar>"
        with closing(ReferencePublisher(PUBLISHER)) as worker:
            expected = worker.request(None, "site_parse", kind="listing", html=page.hex())
            malformed = (b"<broken" * (64 * 1024)) + page
            self.assertEqual(worker.request(None, "site_parse", kind="listing", html=malformed.hex()), expected)

    def test_native_request_limits_keep_subsequent_frames_aligned(self):
        request = b'{"operation":"audio_prefix","prefix":"","suffix":".flac","file_size":1}'
        oversized = b" " * (32 * 1024 * 1024 + 1)
        nested = b'{"data":' + b"[" * 80 + b"0" + b"]" * 80 + b"}"
        frames = b"\n".join((request, request + b"\0" + request, nested, oversized, request, b""))
        output = subprocess.run([PUBLISHER, "--serve"], input=frames, capture_output=True, timeout=15, check=True)
        banner, before, nul, depth, size, after = output.stdout.splitlines()
        self.assertEqual(banner, b"DoujinStyle catalog 1")
        self.assertIn("result", json.loads(before))
        self.assertIn("NUL", json.loads(nul)["error"])
        self.assertIn("nesting limit", json.loads(depth)["error"])
        self.assertIn("frame limit", json.loads(size)["error"])
        self.assertEqual(json.loads(after), json.loads(before))

    def test_native_offer_orchestration_matches_python_through_catalog_writes(self):
        url = ds_url(p="page", type=1, id=25)
        row = {"id": 25, "url": url}
        another_form = PAGE[PAGE.index(b"<form"):].replace(b'value="3"', b'value="05"')
        page = PAGE.replace(b"</mainbar>", another_form)
        supported = "https://WWW.MediaFire.com/file/abc/[TEST-25]%20Mix.ZIP/file"
        mirror = "https://mirror.example.test/songs%2fset%2e7z%2f."
        mega = "https://mega.nz/file/ABCDEFGH#" + "A" * 43
        folder = "https://mega.nz/folder/ABCDEFGH#public-folder"
        drive = "https://drive.google.com/a.zip"

        with closing(ReferencePublisher(PUBLISHER)) as worker:
            for index, (initial, all_forms, expected_posts) in enumerate((
                (supported, False, 1), (supported, True, 2), (folder, False, 2),
                (mega, False, 1), (drive, False, 2), (None, False, 2),
                ("https://unlisted.example.test/archive%FF.ZIP", False, 1),
            )):
                with self.subTest(initial=initial, all_forms=all_forms):
                    def response(location):
                        if location is None:
                            return {"error": "offer POST timed out"}
                        return {"status": 302, "location": location}

                    parsed = worker.request(None, "site_parse", kind="offers", id=25, url=url, html=page.hex(),
                                             all_forms=all_forms, responses=[response(initial), response(mirror)])
                    self.assertEqual(parsed["posts"], expected_posts)
                    native = fetch_offer(Mock(), row, Mock(request=Mock(return_value=parsed)), all_forms=all_forms)
                    python_http = Mock()
                    first = ({"Location": initial}, b"", url) if initial else OSError("offer POST timed out")
                    python_http.get.side_effect = [({}, page, url), first, ({"Location": mirror}, b"", url)]
                    python = fetch_offer(python_http, row, all_forms=all_forms)
                    self.assertEqual(python_http.get.call_count, expected_posts + 1)
                    self.assertEqual(native[:2], python[:2])
                    self.assertEqual(native[2], [list(offer) for offer in python[2]])
                    self.assertEqual(native[3], python[3])

                    snapshots = []
                    for name, result, publisher in (("native", native, worker), ("python", python, None)):
                        with closing(CatalogCache(self.root / f"{name}-{index}.sqlite", publisher=publisher)) as cache:
                            release_id, observation, offers, error = result
                            cache.put_offers(release_id, offers, error, observation=observation)
                            snapshots.append((cache.listing(release_id), cache.copies_for(release_id),
                                              cache.db.execute("SELECT offer_error FROM releases").fetchone()[0]))
                    # Independent writes can straddle a wall-clock second under
                    # instrumentation; timestamp equality is not a semantic
                    # difference. Verify freshness and compare all other data.
                    def stable(value):
                        if isinstance(value, dict):
                            return {key: stable(item) for key, item in value.items()
                                    if key not in ("first_seen", "last_seen")}
                        if isinstance(value, list):
                            return [stable(item) for item in value]
                        if isinstance(value, tuple):
                            return tuple(stable(item) for item in value)
                        return value

                    for _, copies, _ in snapshots:
                        for copy in copies:
                            for source in copy.get("sources", []):
                                for field in ("first_seen", "last_seen"):
                                    if field in source:
                                        self.assertLessEqual(abs(source[field] - int(time.time())), 5)
                    self.assertEqual(*(stable(snapshot) for snapshot in snapshots))

    def test_native_directory_indexes_and_publishes_a_cached_offer_with_legacy_parity(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as zip:
            zip.writestr("Disc/", b"")
            zip.writestr(self.member, b"fixture audio" * 8000)
            zip.writestr("Disc/02.mp3", b"fixture mp3" * 100, compress_type=ZIP_DEFLATED)
            zip.writestr("__MACOSX/._cover.png", b"sidecar")
            zip.writestr("README.txt", b"notes")
            zip.comment = b"fixture comment"
        data = buffer.getvalue()
        requests = []

        class Http:
            def get(self, target, *, headers, cap, **unused):
                span = headers["Range"].removeprefix("bytes=")
                first, last = span.split("-")
                lower = int(first) if first else len(data) - int(last)
                upper = min(lower + cap, len(data)) - 1
                response = Message()
                response["Content-Range"] = f"bytes {lower}-{upper}/{len(data)}"
                response["ETag"] = '"fixture"'
                requests.append((lower, upper))
                return response, data[lower:upper + 1], target

        url = "https://download.mediafire.com/archive.zip"
        listing = {"id": 20, "album": "Native inventory", "url": "https://doujinstyle.com/?p=page&type=1&id=20"}
        expected = inspect_zip(Http(), url, 2 * 1024 * 1024, directory_only=True, max_requests=12,
                               referrer=listing["url"])
        legacy_requests = requests[:]
        requests.clear()
        output = self.root / "tracks"
        with closing(CatalogCache(self.database, structured_references=True,
                                  publisher=ReferencePublisher(PUBLISHER))) as cache:
            copy = cache.add_copy(listing, {}, url)
            seen, log = [], []
            totals = dict.fromkeys(("archives_attempted", "archives_indexed", "archive_body_bytes", "references_written"), 0)
            with patch("doujin.zipfile.ZipFile", side_effect=AssertionError("Legacy reader entered")):
                inspect_archives(Http(), cache, [{"id": copy, "share_url": url}], {20: listing},
                                 output, log.append, lambda rid, name: seen.append((rid, name)),
                                 None, totals, probe_headers=False, probe_cover=False)
            archive = cache.copies_for(20)[0]["archive"]
            self.assertEqual(archive["archive_http"].pop("directory_reader"), "native")
            # Python's zipfile may fetch one extra byte immediately before the
            # central directory (observed on Python 3.12). The native directory
            # parser avoids this redundant I/O. Preserve EXACT metadata parity
            # while validating both readers' real wire-byte/request accounting.
            native_http = dict(archive["archive_http"])
            legacy_http = dict(expected["archive_http"])
            for field in ("requests", "body_bytes"):
                archive["archive_http"].pop(field)
                expected["archive_http"].pop(field)
            self.assertEqual(archive, expected)
            self.assertEqual(native_http["requests"], len(requests))
            self.assertEqual(legacy_http["requests"], len(legacy_requests))
            self.assertEqual(native_http["body_bytes"],
                             sum(upper - lower + 1 for lower, upper in requests))
            self.assertEqual(legacy_http["body_bytes"],
                             sum(upper - lower + 1 for lower, upper in legacy_requests))
            self.assertLessEqual(native_http["requests"], legacy_http["requests"])
            self.assertLessEqual(native_http["body_bytes"], legacy_http["body_bytes"])
            self.assertEqual(totals["references_written"], 2)
            self.assertEqual(len(seen), 2)
            self.assertTrue(any("native ZIP directory" in line for line in log))
            for _, name in seen:
                self.assertTrue((output / name).exists())

    def test_zip64_cp437_header_and_cover_index_without_python_zipfile(self):
        frame = bytearray(417)
        frame[:4] = bytes.fromhex("fffb9064")
        frame[36:48] = b"Info" + struct.pack(">II", 1, 4)
        image = b"\x89PNG\r\n\x1a\nfixture cover"
        wave = (b"RIFF" + struct.pack("<I", 8036) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8) +
                b"data" + struct.pack("<I", 8000) + bytes(8000))
        payload = BytesIO()
        with patch.object(zipfile, "ZIP64_LIMIT", 32):
            with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
                archive.writestr("cafe.mp3", frame * 4)
                archive.writestr("sample.wav", wave)
                archive.writestr("cover.png", image, compress_type=zipfile.ZIP_STORED)
                for i in range(150):
                    archive.writestr(f"auxiliary/{i:03}.bin", b"")
        data = payload.getvalue().replace(b"cafe.mp3", b"caf\x82.mp3")

        class FixtureRanges:
            def get(self, url, *, headers, cap, **unused):
                first, last = headers["Range"][6:].split("-")
                start = int(first) if first else max(0, len(data) - int(last))
                end = min(len(data), start + cap)
                response = Message()
                response["Content-Range"] = f"bytes {start}-{end - 1}/{len(data)}"
                response["ETag"] = '"zip64-fixture"'
                return response, data[start:end], url

        url = "https://download.mediafire.com/fixture.zip"
        legacy_covers = []
        legacy = inspect_zip(FixtureRanges(), url, 2 * 1024 * 1024, directory_only=True,
                             header_probes=True, cover_sink=legacy_covers.append)
        covers = []
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            with (patch("doujin.zipfile.ZipFile", side_effect=AssertionError("Python ZIP reader entered")),
                  patch("audio_probe.probe_wave", side_effect=AssertionError("Python WAV probe entered"))):
                result = inspect_zip(FixtureRanges(), url, 2 * 1024 * 1024, directory_only=True,
                                     header_probes=True, cover_sink=covers.append, zip_worker=publisher)
            self.assertEqual(result["archive_http"]["directory_reader"], "native")
            self.assertEqual(result["audio_probes"], legacy["audio_probes"])
            self.assertEqual(result["audio_probes"]["sample.wav"]["metadata"]["duration_seconds"], 1)
            self.assertEqual([{k: v for k, v in row.items() if k != "zip_name"} for row in result["inventory"]],
                             legacy["inventory"])
            self.assertEqual(result["inventory"][0]["zip_name"]["encoding"], "cp437")
            self.assertEqual(result["cached_front_cover"], "cover.png")
            self.assertEqual(covers, legacy_covers)

            provider = ArchiveProvider(lambda _: True, open_ranges=lambda _: FixtureRanges())
            with patch.dict(ARCHIVE_PROVIDERS, {"fixture.invalid": provider}):
                via_adapter = inspect_zip(None, "https://fixture.invalid/offer", 2 * 1024 * 1024, directory_only=True,
                                          header_probes=True, zip_worker=publisher)
            self.assertEqual(via_adapter["inventory"], result["inventory"])
            self.assertEqual(via_adapter["audio_probes"], result["audio_probes"])

            # Change the name in the local header only. A valid central
            # directory must not let that member's payload reach the probe.
            data = data.replace(b"caf\x82.mp3", b"bad\x82.mp3", 1)
            broken = inspect_zip(FixtureRanges(), url, 2 * 1024 * 1024, directory_only=True,
                                 header_probes=True, zip_worker=publisher)
            self.assertIn("failed native validation", broken["audio_probes"]["café.mp3"]["prefix_status"])

    def test_cp932_and_crc_checked_unicode_path_preserve_published_name(self):
        japanese = "同人/表紙.mp3"
        raw = japanese.encode("cp932")
        placeholder = "x" * (len(raw) - 4) + ".mp3"
        def upath(original, display, *, corrupt=False):
            contents = struct.pack("<BI", 1, zlib.crc32(original) ^ int(corrupt)) + display.encode()
            return struct.pack("<HH", 0x7075, len(contents)) + contents

        frame = (bytes.fromhex("fffb9064") + bytes(413)) * 2
        memory = BytesIO()
        with ZipFile(memory, "w", ZIP_DEFLATED) as zip:
            zip.writestr(placeholder, frame)
            good = zipfile.ZipInfo("good.mp3")
            good.extra = upath(b"good.mp3", "正しい.mp3")
            zip.writestr(good, frame)
            bad = zipfile.ZipInfo("bad.mp3")
            bad.extra = upath(b"bad.mp3", "誤り.mp3", corrupt=True)
            zip.writestr(bad, frame)
            conflict = zipfile.ZipInfo("本来.mp3")
            conflict.extra = upath("本来.mp3".encode(), "別名.mp3")
            zip.writestr(conflict, frame)
        data = memory.getvalue().replace(placeholder.encode(), raw)

        class FixtureRanges:
            def get(self, url, *, headers, cap, **unused):
                first, last = headers["Range"][6:].split("-")
                start = int(first) if first else max(0, len(data) - int(last))
                end = min(len(data), start + cap)
                response = Message()
                response["Content-Range"] = f"bytes {start}-{end - 1}/{len(data)}"
                response["ETag"] = '"cp932-fixture"'
                return response, data[start:end], url

        url = "https://download.mediafire.com/fixture.zip"
        listing = {"id": 34, "album": "Encoding fixture", "url": "https://doujinstyle.com/?p=page&type=1&id=34"}
        old = inspect_zip(FixtureRanges(), url, 65536, directory_only=True, header_probes=True)
        output = self.root / "unicode-tracks"
        with closing(CatalogCache(self.database, structured_references=True,
                                  publisher=ReferencePublisher(PUBLISHER))) as cache:
            copy = cache.add_copy(listing, {}, url)
            cache.put_archive(copy, old, None)
            export(cache, output, [34])
            old_paths = set(output.rglob("*.djt"))
            legacy_hash = sha256(raw.decode("cp437").encode()).hexdigest()[:24]
            track = next(p for p in old_paths if p.name == f"34-{legacy_hash}.djt")
            self.assertIn("FILENAME=Äå.mp3", track.read_text(encoding="utf-8"))

            report = inspect_zip(FixtureRanges(), url, 65536, directory_only=True, header_probes=True,
                                 zip_worker=cache.publisher)
            names = {row["path"]: row for row in report["inventory"]}
            self.assertEqual(names[japanese]["zip_name"]["encoding"], "cp932-inferred")
            self.assertEqual(names["正しい.mp3"]["zip_name"]["encoding"], "unicode-path")
            self.assertIn("warning", names["bad.mp3"]["zip_name"])
            self.assertIn("warning", names["本来.mp3"]["zip_name"])
            self.assertEqual(report["audio_probes"][japanese]["metadata"]["codec"], "MP3")
            cache.put_archive(copy, report, None)
            export(cache, output, [34])
            self.assertEqual(set(output.rglob("*.djt")), old_paths)
            text = track.read_text(encoding="utf-8")
            self.assertIn("FILENAME=表紙.mp3", text)
            self.assertIn(f"MEMBER_LOCATOR={raw.hex()}:0:{zlib.crc32(frame)}", text)

    def test_native_mp3_probe_matches_fallback_and_reads_vbri_frame_count(self):
        frame_size = 417
        frame = bytearray(frame_size)
        frame[:4] = bytes.fromhex("fffb9064")  # MPEG-1 Layer III, 44.1 kHz, 128 kbps, stereo.
        tag = b"ID3\x03\x00\x00\x00\x00\x00\x10TIT2\x00\x00\x00\x06\x00\x00\x00Title"
        file_size = 4_000_000
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            unknown = bytes(frame + frame)
            for prefix in (unknown, tag + unknown, tag, unknown[:frame_size], b"bad"):
                expected = audio_prefix(prefix, ".mp3", file_size, flac_info, vorbis_comment_tags)
                self.assertEqual(publisher.audio_prefix(prefix, ".mp3", file_size), expected)

            frame[36:48] = b"Xing" + struct.pack(">II", 1, 1047)
            xing = tag + frame + frame
            expected = audio_prefix(xing, ".mp3", file_size, flac_info, vorbis_comment_tags)
            self.assertEqual(publisher.audio_prefix(xing, ".mp3", file_size), expected)
            self.assertNotIn("duration_seconds", publisher.audio_prefix(xing, ".mp3", 100)["metadata"])

            frame[36:54] = b"VBRI" + struct.pack(">HHHII", 1, 0, 0, file_size, 1047)
            vbri = publisher.audio_prefix(frame + frame, ".mp3", file_size)["metadata"]
            self.assertEqual(vbri["sample_rate_hz"], 44100)
            self.assertAlmostEqual(vbri["duration_seconds"], 1047 * 1152 / 44100)

            with self.assertRaisesRegex(OSError, "invalid hexadecimal byte sequence"):
                publisher.audio_prefix(bytes(4097), ".mp3", file_size)

    def test_wave_prefix_formats_and_bounds_match_python_fallback(self):
        header = b"RIFF\0\0\0\0WAVE"
        pcm = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 48000, 192000, 4, 16)
        invalid = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 0, 0, 4, 16)
        odd = b"JUNK\x01\0\0\0a\0"
        data = b"data" + struct.pack("<I", 192000)
        extended = (b"fmt " + struct.pack("<IHHIIHHHHI", 40, 0xfffe, 2, 48000, 384000, 8, 32, 22, 24, 3) +
                    bytes.fromhex("0300000000001000800000aa00389b71"))
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            for prefix, physical_size in (
                (header + odd + pcm + data, 192054),
                (header + pcm + invalid + data, 192068),
                (header + extended + data, 192068),
                (header + pcm + data, 40),
                (header + pcm + b"JUNK\xff\xff\xff\xff", 192044),
                (header + extended[:-1], len(header + extended) - 1),
                (header[:11], 11),
            ):
                with self.subTest(prefix=prefix, physical_size=physical_size):
                    expected = audio_prefix(prefix, ".wav", physical_size, flac_info, vorbis_comment_tags)
                    self.assertEqual(publisher.audio_prefix(prefix, ".wav", physical_size), expected)
            metadata = publisher.audio_prefix(header + extended + data, ".wav", 192068)["metadata"]
            self.assertEqual((metadata["codec"], metadata["bits_per_sample"], metadata["duration_seconds"]),
                             ("IEEE float PCM", 24, 0.5))
            with self.assertRaisesRegex(OSError, "invalid hexadecimal byte sequence"):
                publisher.audio_prefix(bytes(4097), ".wav", 4097)

    def test_native_audio_prefix_matches_fallback_on_flac_opus_vorbis_and_id3(self):
        def flac_block(kind, payload):
            return bytes([kind]) + len(payload).to_bytes(3, "big") + payload

        fields = [b"test encoder", "TITLE=夜桜".encode(), b"title=Second title", b"METADATA_BLOCK_PICTURE=example"]
        comments = struct.pack("<I", len(fields[0])) + fields[0] + struct.pack("<I", len(fields) - 1)
        comments += b"".join(struct.pack("<I", len(value)) + value for value in fields[1:])
        streaminfo = bytes(10) + struct.pack(">Q", (44100 << 44) | (1 << 41) | (23 << 36) | 44100) + bytes(16)
        flac = b"fLaC" + flac_block(0, streaminfo) + flac_block(3, bytes(36)) + flac_block(132, comments)
        picture = b"fLaC" + flac_block(0, streaminfo) + b"\x86\0\x10\0" + bytes(4) + b"\0\0\0\x09image/png"

        def ogg_page(packets, serial=12):
            return (b"OggS" + bytes(10) + struct.pack("<I", serial) + bytes(8) + bytes([len(packets)]) +
                    bytes(map(len, packets)) + b"".join(packets))

        opus_id = b"OpusHead" + bytes([1, 2, 0, 0]) + struct.pack("<I", 44100) + bytes(3)
        vorbis_id = b"\x01vorbis" + bytes(4) + bytes([2]) + struct.pack("<Iiii", 44100, 0, 128000, 0) + bytes(2)
        opus = ogg_page([opus_id]) + ogg_page([b"bogus"], serial=11) + ogg_page([b"OpusTags" + comments])
        vorbis = ogg_page([vorbis_id, b"\x03vorbis" + comments])

        def id3_frame(name, data):
            return name + struct.pack(">I", len(data)) + bytes(2) + data

        id3 = (id3_frame(b"TIT2", b"\x01" + "雨𠮷\0晴".encode("utf-16")) +
               id3_frame(b"TALB", b"\x00L\xe9gacy") + id3_frame(b"TPE1", b"\x03bad\xff") +
               id3_frame(b"APIC", b"fixture"))
        self.assertLess(len(id3), 128)
        mp3_frame = bytes.fromhex("fffb9064") + bytes(413)
        mp3 = b"ID3\x03\0\0\0\0\0" + bytes([len(id3)]) + id3 + mp3_frame * 2

        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            for suffix, prefix in ((".flac", flac), (".flac", flac[:49]), (".flac", picture),
                                   (".flac", b"fLaC" + flac_block(0, streaminfo) + flac_block(132, b"\x01")),
                                   (".opus", opus), (".opus", opus[:45]), (".ogg", vorbis), (".mp3", mp3),
                                   (".mp3", mp3[:len(id3) + 4]), (".ogg", b"invalid input")):
                with self.subTest(suffix=suffix, size=len(prefix)):
                    expected = audio_prefix(prefix, suffix, 50000, flac_info, vorbis_comment_tags)
                    self.assertEqual(publisher.audio_prefix(prefix, suffix, 50000), expected)

    def test_mediafire_zip_bridge_batches_members_without_raw_range_ipc(self):
        share = "https://www.mediafire.com/file/fixture/file"
        direct = "https://download.mediafire.com/fixture.zip"
        referrer = "https://doujinstyle.com/?p=page&id=1"
        worker = ReferencePublisher(PUBLISHER, host_zlib="C:/foobar2000/zlib1.dll")
        members = [(f"Volume/{index:02}.wav".encode().hex(), 200, 200, index, index * 250, 0, 0, "", False,
                    f"Volume/{index:02}.wav", "cp437", False, None)
                   for index in range(33)]
        commands = []

        def request(_, operation, **fields):
            commands.append((operation, fields))
            if operation == "mediafire_resolve":
                return {"status": 200, "url": direct, "referrer": share, "policy": "origin"}
            if operation == "mediafire_zip_open":
                return {"members": members, "size": 20500, "etag": '"fixture"', "modified": "",
                        "disposition": 'attachment; filename="fixture.zip"', "requests": 1, "fetched": 8192}
            if operation == "mediafire_zip_probes":
                return {"requests": len(commands), "fetched": 10000,
                        "observations": [{"metadata": {"codec": "PCM"}} if offset != 250 else
                                         {"prefix_status": "ZIP local header failed native validation"}
                                         for offset, _, _ in fields["members"]]}
            self.fail(operation)

        with patch.object(worker, "request", side_effect=request):
            result = inspect_zip(None, share, 2 * 1024 * 1024, directory_only=True,
                                 referrer=referrer, header_probes=True, native_mediafire=worker)
        self.assertEqual([op for op, _ in commands], ["mediafire_resolve", "mediafire_zip_open",
                                                      "mediafire_zip_probes", "mediafire_zip_probes"])
        self.assertEqual([len(fields["members"]) for _, fields in commands[2:]], [32, 1])
        self.assertEqual(result["response_filename"], "fixture.zip")
        self.assertEqual(result["archive_http"]["member_reader"], "native-batch")
        self.assertEqual(result["archive_http"]["body_bytes"], 10000)
        self.assertEqual(result["audio_probes"]["Volume/32.wav"]["metadata"]["codec"], "PCM")
        self.assertIn("failed native validation", result["audio_probes"]["Volume/01.wav"]["prefix_status"])

    def test_mediafire_native_transport_preserves_range_evidence(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as zip:
            zip.writestr("sample.wav", bytes(16384))
        data = buffer.getvalue()
        share = "https://www.mediafire.com/file/fixture/file"
        direct = "https://download.mediafire.com/archive.zip?token=redacted"
        release = "https://doujinstyle.com/?p=page&id=1"
        publisher = ReferencePublisher(PUBLISHER)

        def wire(_, operation, **fields):
            if operation == "mediafire_resolve":
                self.assertEqual(fields, {"url": share, "referrer": release})
                return {"status": 200, "url": direct, "referrer": share, "policy": "origin"}
            self.assertEqual(operation, "mediafire_range")
            self.assertEqual((fields["url"], fields["referrer"], fields["policy"]), (direct, share, "origin"))
            start = fields["start"]
            self.assertEqual(fields["validator"], '"fixture"' if start is not None else "")
            lower = start if start is not None else max(0, len(data) - fields["length"])
            end = min(lower + fields["length"], len(data))
            return {"status": 206, "body": data[lower:end].hex(), "headers": {
                "Content-Range": f"bytes {lower}-{end - 1}/{len(data)}", "ETag": '"fixture"',
                "Content-Disposition": 'attachment; filename="test.zip"'}}

        with patch.object(publisher, "request", side_effect=wire):
            report = inspect_zip(None, share, 65536, directory_only=True, header_probes=True,
                                 referrer=release, native_mediafire=publisher)
        self.assertEqual(report["inventory"][0]["path"], "sample.wav")
        self.assertEqual(report["response_filename"], "test.zip")
        self.assertEqual(report["archive_http"]["transport"], "winhttp")
        self.assertEqual(report["archive_http"]["etag"], '"fixture"')
        with patch.object(publisher, "request", return_value={"status": 403}):
            with self.assertRaises(HTTPError) as blocked:
                publisher.mediafire_resolve(share, release)
            self.assertEqual((blocked.exception.code, blocked.exception.url), (403, share))

    @unittest.skipUnless(os.name != "nt" and which("true"), "Requires an executable that returns immediately")
    def test_packaged_charts_remain_readable_when_native_worker_exits_at_startup(self):
        charts = {
            "month": [{"id": 26, "album": "Fixture monthly", "chart_rank": 1, "chart_downloads": 1280}],
            "all": [{"id": 27, "album": "Fixture all-time", "chart_rank": 1, "chart_downloads": 13820}],
        }
        with closing(CatalogCache(self.database)) as cache:
            cache.put_page(GUIDES_URL, {"charts": charts, "releases": []})
            for chart in charts.values():
                row = chart[0]
                copy = cache.add_copy({"id": row["id"], "album": row["album"]}, {},
                                      f"https://example.test/{row['id']}.zip")
                cache.put_archive(copy, {"format": "zip", "inventory": [{"path": self.member}],
                                         "header_probe_pass_complete": True}, None)
                with cache.write_transaction():
                    cache.db.execute("UPDATE releases SET offer_checked=seen WHERE id=?", (row["id"],))
        app = self.root / "worker.pyz"
        source = Path(__file__).resolve().parent
        app.write_bytes(payload(source, Path(which("true"))))
        for period, title in (("month", "Fixture monthly"), ("all", "Fixture all-time")):
            with self.subTest(period=period):
                output = subprocess.check_output([
                    sys.executable, str(app), "--cache", str(self.database), "--output", str(self.root / "tracks"),
                    "--popular", period, "--result-rows"
                ], text=True, encoding="utf-8", timeout=12)
                self.assertIn(title, output)
                self.assertIn("Indexing unavailable", output)
                self.assertIn("startup, exit status 0", output)
        self.assertEqual(len(list((self.root / "tracks").rglob("*.djt"))), 0)

    @unittest.skipUnless(os.name != "nt" and which("true"), "Requires an executable that returns immediately")
    def test_failed_helper_cannot_fall_back_to_unpatched_wal_writes(self):
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        with patch("catalog_cache.sqlite3.sqlite_version_info", (3, 50, 4)):
            with self.assertRaisesRegex(OSError, "Recovery writer unavailable.*patched SQLite"):
                open_worker_catalog(self.database, ReferencePublisher(which("true")), lambda _: None)
        with closing(CatalogCache(self.database, read_only=True)) as cache:
            self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(cache.listing(12)["album"], "夜桜")

    def test_native_chart_snapshot_and_new_releases_commit_together(self):
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            row = {"id": 26, "album": "Chart discovery"}
            chart = {"charts": {"month": [{**row, "chart_rank": 1}]}, "releases": []}
            cache.put_page(GUIDES_URL, chart, seeds=[row, {"id": 12, "album": "Chart republished"}])
            self.assertEqual(cache.page_snapshot(GUIDES_URL)[0], chart)
            self.assertEqual(cache.listing(26), row)
            self.assertEqual(cache.listing(12)["album"], "夜桜")
            with self.assertRaisesRegex(OSError, "Invalid catalog request"):
                cache.put_page(GUIDES_URL, {"charts": {}, "releases": []}, seeds=[{"album": "Malformed"}])
            self.assertEqual(cache.page_snapshot(GUIDES_URL)[0], chart)

    def test_native_placement_writes_keep_a_legacy_catalog_in_rollback_mode(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        with patch("catalog_cache.local_catalog", return_value=False):
            self.assertEqual(self.publish(self.root / "tracks")["written"], 2)
        with closing(CatalogCache(self.database, read_only=True)) as cache:
            self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(cache.db.execute("SELECT count(*) FROM published_references").fetchone()[0], 2)

    def test_native_ingest_matches_python_catalog_semantics(self):
        def exercise(path, publisher):
            with closing(CatalogCache(path, publisher=publisher)) as cache:
                observed = {"id": 41, "album": "夏", "tags": ["Techno"], "events": [{"id": "M3-54"}],
                            "evidence": {"album": "listing", "ignored": "absent field"},
                            "technical": {"foo": 3, "saved": "yes"},
                            "download_forms": [{"fields": ["default"]}, {"source": "2", "fields": ["old"]}]}
                url = "https://doujinstyle.com/?p=home"
                cache.put_page(url, {"releases": [observed, {"id": 42, "album": "Other"}]},
                               checkpoint={"last": "夏"})
                cache.put_observation({"id": 41, "album": None, "tags": [], "technical": {"foo": None, "bar": 4},
                                       "download_forms": [{"source": "2", "fields": ["updated"]},
                                                          {"source": "3", "fields": ["added"]}]})
                [first, second] = cache.put_offers(41, [
                    ("doujinstyle", {"filename": "Sample.zip"}, "https://mediafire.com/file/TestID/Sample.zip/file"),
                    ("doujinstyle:2", {"host": "mega.nz"}, "https://mega.nz/file/abc#key"),
                ], None)
                [same] = cache.put_offers(42, [
                    ("doujinstyle", {"filename": None}, "https://www.mediafire.com/file/TestID/file"),
                ], None)
                self.assertEqual(first, same)
                cache.put_offers(41, [], "unavailable", {"doujinstyle"}, observation={"id": 41, "artist": "作曲"})
                track = {"path": "track.flac", "crc32": "deadbeef", "uncompressed_bytes": 120,
                         "kind": "audio_candidate", "zip_method": 8}
                discarded = {"path": "discarded.flac", "crc32": "123", "uncompressed_bytes": 99}
                cache.put_archive(first, {"format": "zip", "inventory": [track, discarded],
                    "header_probe_pass_complete": True, "cached_front_cover": "track.flac",
                    "audio_probes": {"track.flac": {"tags": {"TITLE": ["保留"]}}, "discarded.flac": {"old": True}},
                    "audio_sample": {"path": "track.flac", "data": "legacy"}}, None)
                cache.put_archive(first, {"format": "zip", "inventory": [track, {**discarded, "crc32": "456"}],
                    "header_probe_pass_complete": False,
                    "audio_probes": {"track.flac": {"metadata": {"sample_rate_hz": 44100}}}}, None)
                cache.put_archive(first, None, "timeout")
                cache.put_archive(second, {"format": "rar"}, None)
                self.assertTrue(cache.claim_lookup("bangumi", 41, "first", 30, 20))
                self.assertFalse(cache.claim_lookup("bangumi", 41, "second", 35, 21))
                self.assertTrue(cache.claim_lookup("bangumi", 41, "second", 40, 30))
                cache.release_lookup("bangumi", 41, "first")
                self.assertFalse(cache.claim_lookup("bangumi", 41, "third", 45, 31))
                cache.release_lookup("bangumi", 41, "second")
                self.assertTrue(cache.claim_lookup("bangumi", 41, "third", 45, 31))
                return (cache.page(url), cache.backfill_checkpoint(), cache.listing(41), cache.copies_for(41),
                        cache.copies_for(42), cache.summary())

        with patch("catalog_cache.time.time", return_value=1770000000):
            python = exercise(self.root / "old.sqlite", None)
            native = exercise(self.root / "new.sqlite", ReferencePublisher(PUBLISHER))
        self.assertEqual(native, python)

    def test_native_writer_enables_wal_with_an_old_python_reader(self):
        path = self.root / "new.sqlite"
        with patch("catalog_cache.sqlite3.sqlite_version_info", (3, 51, 2)):
            with closing(CatalogCache(path, publisher=ReferencePublisher(PUBLISHER))) as cache:
                self.assertEqual(cache.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    cache.db.execute("INSERT INTO pages VALUES ('direct', '{}', 0)")
                cache.put_listing({"id": 41, "album": "新譜"})
                self.assertEqual(cache.listing(41)["album"], "新譜")

    def test_interrupted_page_rolls_back_page_listings_and_checkpoint(self):
        with closing(sqlite3.connect(self.database)) as fixture, fixture:
            fixture.execute("""
                CREATE TRIGGER abort_ingest BEFORE INSERT ON releases WHEN NEW.id=42
                BEGIN SELECT RAISE(ABORT,'fixture interruption'); END
            """)
        with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
            page = {"releases": [{"id": 41, "album": "First"}, {"id": 42, "album": "Second"}]}
            with self.assertRaisesRegex(OSError, "fixture interruption"):
                cache.put_page("fixture", page, checkpoint={"page": 1})
            self.assertIsNone(cache.page("fixture"))
            self.assertIsNone(cache.listing(41))
            self.assertIsNone(cache.backfill_checkpoint())
            with closing(sqlite3.connect(self.database)) as fixture, fixture:
                fixture.execute("DROP TRIGGER abort_ingest")
            cache.put_page("fixture", page, checkpoint={"page": 1})
            self.assertEqual(cache.page("fixture"), page)
            self.assertEqual(cache.backfill_checkpoint(), {"page": 1})

    def test_native_writers_merge_concurrent_observations_while_a_reader_holds_a_snapshot(self):
        start = threading.Barrier(4, timeout=8)

        def update(worker):
            with closing(CatalogCache(self.database, publisher=ReferencePublisher(PUBLISHER))) as cache:
                start.wait()
                for item in range(10):
                    cache.put_observation({"id": 12, "technical": {f"{worker}-{item}": item}})

        with closing(CatalogCache(self.database, read_only=True)) as reader:
            reader.db.execute("BEGIN")
            before = reader.listing(12)
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(update, range(4)))
            self.assertEqual(reader.listing(12), before)
            reader.db.commit()
            self.assertEqual(len(reader.listing(12)["technical"]), 40)
            self.assertEqual(reader.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_native_bootstrap_migrates_version_zero_catalog(self):
        path = self.root / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as legacy:
            with legacy:
                legacy.execute("""CREATE TABLE releases (
                    id INTEGER PRIMARY KEY, listing TEXT NOT NULL, seen INTEGER NOT NULL,
                    offer TEXT, share_url TEXT, host TEXT, offer_checked INTEGER, offer_error TEXT,
                    archive TEXT, archive_checked INTEGER, archive_error TEXT)""")
                legacy.execute("""INSERT INTO releases VALUES (3,'{"id":3,"album":"Legacy"}',10,
                    '{"filename":"Archive.zip"}','https://mediafire.com/file/TestID/Archive.zip/file',
                    'mediafire.com',11,NULL,'{"format":"zip","inventory":[]}',12,NULL)""")
        with closing(CatalogCache(path, publisher=ReferencePublisher(PUBLISHER))) as cache:
            [copy] = cache.copies_for(3)
            self.assertEqual(copy["archive"], {"format": "zip", "inventory": []})
            self.assertEqual(copy["archive_checked"], 12)
            self.assertEqual(copy["sources"][0]["first_seen"], 11)
            self.assertEqual(cache.db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_native_publisher_rejects_an_invalid_filename_before_writing(self):
        with closing(ReferencePublisher(PUBLISHER)) as publisher:
            output = self.root / "tracks"
            with self.assertRaisesRegex(OSError, "Invalid track reference"):
                publisher.publish(self.database, output, "", [("../escape.djt", "DOUJIN-TRACK 1\n")])
            self.assertFalse(output.exists())
            self.assertFalse((self.root / "escape.djt").exists())
            published = publisher.publish(self.database, output, "", [(self.filename, "DOUJIN-TRACK 1\n")])
            self.assertEqual(published, [(self.filename, True)])
