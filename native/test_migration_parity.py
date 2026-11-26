"""Differential coordinator qualification, using the retained Python source oracle.

Both arms exercise the same compiled native data plane on separate SQLite
backups. This is NOT an immutable old-binary or foobar2000 GUI comparison.
"""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from catalog_cache import CatalogCache
from native.reference_publisher import ReferencePublisher
from native import search_worker as legacy
from native.test_native_foreground_job import page_markup

NATIVE = os.getenv("DOUJINSTYLE_TEST_PUBLISHER")
CHART = (b"<html><body><h3>This Month</h3><div>"
         b'<a href="/?p=page&amp;type=1&amp;id=52">Fixture Catalog</a>'
         b"<b>801 downloads</b></div><h3>All-Time</h3><div>"
         b'<a href="/?p=page&amp;type=1&amp;id=52">Fixture Catalog</a>'
         b"<b>1,200 downloads</b></div></body></html>")
TABLES = ("pages", "backfill_checkpoint", "releases", "copies", "release_copies",
          "listing_activity", "tracklists", "catalog_lookup_claims",
          "published_references", "catalog_post_attempts")


def snapshot(path, output):
    with closing(sqlite3.connect(path)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise AssertionError("Invalid catalog")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise AssertionError("Broken foreign keys")
        rows = {}
        for table in TABLES:
            fields = [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")]
            if not fields:
                raise AssertionError("Missing table " + table)
            records = [dict(zip(fields, row)) for row in db.execute("SELECT * FROM " + table)]
            if table == "published_references":
                for row in records:
                    if Path(row["root"]).resolve() != output.resolve():
                        raise AssertionError("Unexpected publication root")
                    row["root"] = "<isolated-output>"
            rows[table] = sorted(records, key=lambda row: json.dumps(row, sort_keys=True))
        return rows


def observation(lines):
    rows, ranks, notices, updates, terminal = {}, [], [], [], []
    for line in lines:
        if line.startswith("\x1e"):
            fields = line[1:].split("\t")
            if len(fields) != 8 or not fields[0].isdigit():
                raise AssertionError("Malformed UI row")
            rows[fields[0]] = fields[1:]
        elif line.startswith("\x15"):
            ranks.append(line[1:].split("\t"))
        elif line.startswith("\x1c"):
            notices.append(line[1:])
        elif line.startswith("\x19"):
            updates.append(line[1:])
        elif line.startswith(("Done. ", "Coverage: ")):
            terminal.append(line)
    return {"rows": rows, "ranks": ranks, "notices": sorted(notices),
            "updates": sorted(updates), "terminal": terminal}


def files(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@unittest.skipUnless(NATIVE, "Compiled native worker required")
class MigrationParity(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="migration-parity-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.seed = self.root / "seed.sqlite"
        with closing(ReferencePublisher(executable=NATIVE)) as worker:
            worker.request(self.seed, "initialize", wal=True, legacy=[])
            url = "https://doujinstyle.com/?p=search&source=1&type=blanket&result=Fixture&page=0"
            worker.request(self.seed, "site_search_job", query="Fixture", pages=1,
                           fixture_pages={url: page_markup().hex()})
            worker.request(self.seed, "site_chart_refresh", previous=0, html=CHART.hex())
            with closing(CatalogCache(self.seed, publisher=worker, structured_references=True)) as cache:
                copy = cache.add_copy({"id": 52, "album": "Fixture Catalog", "artist": "夜桜",
                                       "tags": ["Techno", "同人"]}, {},
                                      "https://example.test/archive.zip")
                cache.put_archive(copy, {"format": "zip", "inventory": [{"path": "夜桜/01 春.flac"}],
                                         "audio_probes": {"夜桜/01 春.flac": {"tags": {"DATE": ["2019-10-27"]}}},
                                         "header_probe_pass_complete": True}, None)
        with closing(sqlite3.connect(self.seed)) as db:
            db.execute("UPDATE releases SET offer_checked=?, offer_error=NULL", (int(time.time()),))
            db.commit()

    def clone(self, arm):
        root = self.root / arm
        root.mkdir()
        output = root / "tracks"
        output.mkdir()
        path = root / "catalog.sqlite"
        with closing(sqlite3.connect(self.seed)) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
        return path, output

    def python_arm(self, db, output, action):
        lines = []
        with closing(ReferencePublisher(executable=NATIVE)) as worker:
            with closing(CatalogCache(db, publisher=worker, structured_references=True)) as cache:
                options = dict(emit_result=lambda row: lines.append(legacy.result_record(row, worker)),
                               emit_reference=lambda rid, name: lines.append(f"\x1c{rid}\t{name}"))
                # No art-cache argument, no network-dependent asset fetch.
                # Python HTTP is forbidden; the native data plane uses warm
                # seeded pages and an intentionally unsupported provider URL.
                with patch.object(legacy.SearchHttp, "get", side_effect=AssertionError("unexpected Python HTTP")):
                    if action == "search":
                        totals = legacy.run_search(legacy.SearchHttp(), cache, "Fixture", 1,
                                                   output, lines.append, **options)
                    elif action == "prefetch":
                        totals = legacy.prefetch_charts(legacy.SearchHttp(), cache, output, lines.append, **options)
                    else:
                        totals = legacy.run_popular(legacy.SearchHttp(), cache, action, output,
                                    lines.append, emit_chart=lambda rows: lines.append("\x15"+"\t".join(map(str, rows))),
                                    **options)
        lines.append(f"Done. {totals['listings']} listings, {totals['archives_indexed']}/{totals['archives_attempted']} "
                     f"archives inspected, {totals['references_written']} references written.")
        lines.append(f"Coverage: {totals['releases_with_tracks']}/{totals['listings']} releases have published tracks.")
        return observation(lines)

    def native_arm(self, db, output, action):
        args = {"search": ["--query=Fixture", "--pages", "1"],
                "month": ["--popular", "month"], "all": ["--popular", "all"],
                "prefetch": ["--prefetch-charts"]}[action]
        proc = subprocess.run([NATIVE, "--foreground-search", "--cache", str(db),
                               "--output", str(output), "--result-rows", *args],
                              capture_output=True, timeout=20, check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return observation(proc.stdout.decode("utf-8", "strict").split("\n"))

    def exercise(self, action):
        a, out_a = self.clone(action+"-python")
        b, out_b = self.clone(action+"-native")
        for repeat in range(2):
            with self.subTest(action=action, repeat=repeat):
                expected = self.python_arm(a, out_a, action)
                actual = self.native_arm(b, out_b, action)
                self.assertTrue(expected["rows"])
                self.assertEqual(actual, expected)
                self.assertEqual(snapshot(a, out_a), snapshot(b, out_b))
                self.assertEqual(files(out_a), files(out_b))
                self.assertTrue(files(out_b))
                self.assertEqual(len(actual["notices"]), len(set(actual["notices"])))
                if repeat:
                    self.assertEqual(actual["updates"], [])
                    self.assertIn("0 references written.", actual["terminal"][0])
        self.assertEqual(snapshot(self.seed, self.root)["published_references"], [])

    def test_search(self): self.exercise("search")
    def test_month(self): self.exercise("month")
    def test_all_time(self): self.exercise("all")
    def test_prefetch(self): self.exercise("prefetch")


if __name__ == "__main__":
    unittest.main()
