"""SQLite listings, hosted copies, and shared archive indexes."""
import gzip
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import closing, contextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit
from native.worker_scope import standalone_only, worker_fallback, native_worker_only


PAGE_TTL = 600
OFFER_TTL = 30 * 86400
RETRY_TTL = 86400
SQLITE_BUSY_SECONDS = 2
SCHEMA_VERSION = 2
MEDIAFIRE_SHARE_HOSTS = {"mediafire.com", "www.mediafire.com", "m.mediafire.com"}
INCOMPLETE_ZIP_PROBES = """(json_extract(c.archive, '$.format')='zip' AND (
    json_extract(c.archive, '$.header_probe_pass_complete') IS NOT 1
    OR json_type(c.archive, '$.audio_probes') IS NULL
    OR EXISTS (
        SELECT 1 FROM json_each(c.archive, '$.inventory') AS member
        WHERE json_extract(member.value, '$.kind')='audio_candidate'
          AND json_extract(member.value, '$.encrypted') IS NOT 1
          AND json_extract(member.value, '$.zip_method') IN (0, 8)
          AND NOT EXISTS (
              SELECT 1 FROM json_each(c.archive, '$.audio_probes') AS probe
              WHERE probe.key=json_extract(member.value, '$.path')
          )
    )
))"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def host_family(host):
    host = host.casefold().rstrip(".")
    for canonical, aliases in {
        "mediafire.com": ("mediafire.com",),
        "mega.nz": ("mega.nz", "mega.co.nz"),
        "google-drive": ("drive.google.com", "drive.usercontent.google.com"),
        "dropbox.com": ("dropbox.com", "dropboxusercontent.com"),
    }.items():
        if any(host == alias or host.endswith("." + alias) for alias in aliases):
            return canonical
    return host


def destination_key(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
        raise ValueError("Expected an HTTP(S) share URL without embedded credentials")
    match = re.fullmatch(r"/file/([A-Za-z0-9]+)(?:/[^/]+)?(?:/file)?/?", parts.path)
    if (parts.hostname in MEDIAFIRE_SHARE_HOSTS and parts.port in (None, 80, 443)
            and not parts.query and not parts.fragment and match):
        return "mediafire:file:" + match[1]
    return "url:" + url


def copy_link(offer, url, source):
    return {"destination": destination_key(url), "host": host_family(urlsplit(url).hostname),
            "offer": offer, "url": url, "source": source}


@standalone_only
def can_inspect(url, offer):
    from native.archive_providers import ARCHIVE_PROVIDERS
    host = urlsplit(url).hostname or ""
    family = host_family(host)
    if provider := ARCHIVE_PROVIDERS.get(family):
        return provider.can_read(url)
    return family != "google-drive" and bool(offer.get("format_hint"))


@standalone_only
def wal_supported(version):
    return (version >= (3, 51, 3) or version[:2] == (3, 50) and version[2] >= 7
            or version[:2] == (3, 44) and version[2] >= 6)


def local_catalog(path):
    if os.name != "nt":
        return True
    from ctypes import WinDLL, c_wchar_p
    drive_type = WinDLL("kernel32").GetDriveTypeW
    drive_type.argtypes = (c_wchar_p,)
    return drive_type(Path(path).resolve().anchor) in (2, 3, 6)


@standalone_only
def enable_journal(db, path):
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    allow_wal = str(path) != ":memory:" and local_catalog(path) and wal_supported(sqlite3.sqlite_version_info)
    if not allow_wal:
        if mode == "wal":
            raise sqlite3.OperationalError(
                "Writing this WAL catalog requires patched SQLite (3.51.3 or a fixed backport) on a local volume.")
        return
    if mode == "wal":
        return
    # First opens can race while SQLite changes the database header.
    deadline = time.monotonic() + SQLITE_BUSY_SECONDS
    while True:
        try:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal":
                return
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", None)
            # Python 3.10 does not expose the numeric error on the exception.
            if code is not None and (code & 0xff) not in (5, 6):
                raise
            if code is None and not any(word in str(error).lower() for word in ("locked", "busy")):
                raise
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError("Catalog is busy. SQLite could not switch it to WAL.")
        time.sleep(0.025)


@standalone_only
def write_snapshot(source, path):
    path = Path(path)
    if path.suffix.lower() == ".gz":
        with tempfile.TemporaryDirectory() as scratch:
            raw_snapshot = Path(scratch) / "snapshot.sqlite"
            write_snapshot(source, raw_snapshot)
            with path.open("xb") as raw:
                try:
                    with raw_snapshot.open("rb") as original, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                        shutil.copyfileobj(original, compressed)
                except BaseException:
                    raw.close()
                    path.unlink(missing_ok=True)
                    raise
        return
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as dest:
            source.backup(dest)
            dest.execute("PRAGMA journal_mode=DELETE").fetchone()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@standalone_only
def restore_snapshot(snapshot, path):
    snapshot = Path(snapshot).resolve()
    if snapshot.suffix.lower() == ".gz":
        with tempfile.TemporaryDirectory() as scratch:
            uncompressed = Path(scratch) / "snapshot.sqlite"
            with gzip.open(snapshot, "rb") as original, uncompressed.open("wb") as dest:
                shutil.copyfileobj(original, dest)
            restore_snapshot(uncompressed, path)
        return
    with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as source:
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"releases", "pages"} <= tables:
            raise ValueError("File is not a DoujinStyle catalog snapshot")
        if source.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Catalog snapshot failed SQLite quick_check")
        write_snapshot(source, path)


class CatalogCache:
    def __init__(self, path, *, read_only=False, structured_references=False, publisher=None):
        self.read_only = read_only
        self.structured_references = structured_references
        self.publisher = publisher
        self.path = None if str(path) == ":memory:" else Path(path).resolve()
        self.native = publisher is not None and not read_only and self.path is not None
        from zipimport import zipimporter
        if isinstance(__loader__, zipimporter) and not (self.native or read_only):
            raise RuntimeError("Bundled DoujinStyle catalog requires its native helper; no Python writer fallback")
        self.db = None
        try:
            if self.native:
                self.publisher.request(self.path, "initialize", wal=local_catalog(self.path),
                                       legacy=self.legacy_links())
            self.db = (sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=SQLITE_BUSY_SECONDS)
                       if read_only or self.native else sqlite3.connect(path, timeout=SQLITE_BUSY_SECONDS))
            self.db.row_factory = sqlite3.Row
            if not (read_only or self.native):
                getattr(self, "initialize")(path)
        except BaseException:
            self.close()
            raise

    def legacy_links(self):
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                     timeout=SQLITE_BUSY_SECONDS)) as reader:
            reader.execute("BEGIN")
            if (reader.execute("PRAGMA user_version").fetchone()[0] != 0 or not
                    reader.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='releases'").fetchone()):
                return []
            return [dict(copy_link(json.loads(offer) if offer else {"host": host}, url, "doujinstyle"),
                         id=release_id, old_offer=offer, old_host=host)
                    for release_id, url, offer, host in reader.execute("""
                        SELECT id,share_url,offer,host FROM releases WHERE share_url IS NOT NULL
                        ORDER BY COALESCE(offer_checked,seen),id
                    """)]

    @standalone_only
    def initialize(self, path):
        enable_journal(self.db, path)
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        with self.write_transaction():
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                statement = ""
                for line in files("native").joinpath("catalog_schema.sql").read_text("utf-8").splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self.db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise sqlite3.OperationalError("Incomplete catalog schema")
                if version == 0:
                    self.migrate_single_offers()
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @standalone_only
    @contextmanager
    def write_transaction(self):
        # Check the mode with the writer reservation held. A native worker can
        # promote the file to WAL after an older Python SQLite connection opens.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not wal_supported(sqlite3.sqlite_version_info) and self.db.execute(
                    "PRAGMA journal_mode").fetchone()[0] == "wal":
                raise sqlite3.OperationalError("Writing this WAL catalog requires patched SQLite.")
            yield

    @standalone_only
    @contextmanager
    def publication_lock(self):
        # Keep publishing order across processes without reserving SQLite's writer.
        if self.path is None:
            yield
            return
        with self.path.with_name(self.path.name + ".publish.lock").open("ab") as lock:
            if os.name == "nt":
                from msvcrt import LK_NBLCK, LK_UNLCK, locking
                import errno
                deadline = time.monotonic() + 20
                while True:
                    try:
                        locking(lock.fileno(), LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EDEADLK) or time.monotonic() >= deadline:
                            raise
                        time.sleep(0.025)
                try:
                    yield
                finally:
                    locking(lock.fileno(), LK_UNLCK, 1)
            else:
                from fcntl import LOCK_EX, flock
                flock(lock.fileno(), LOCK_EX)
                yield

    def close(self):
        try:
            if self.publisher is not None:
                self.publisher.close()
        finally:
            if self.db is not None:
                self.db.close()

    @standalone_only
    def backup(self, path):
        write_snapshot(self.db, path)

    @standalone_only
    def migrate_single_offers(self):
        previous = self.db.execute("""
            SELECT * FROM releases WHERE share_url IS NOT NULL
            ORDER BY COALESCE(offer_checked, seen), id
        """).fetchall()
        for row in previous:
            offer = json.loads(row["offer"]) if row["offer"] else {"host": row["host"]}
            copy_id = self.attach_copy(row["id"], offer, row["share_url"], "doujinstyle",
                                       row["offer_checked"] or row["seen"])
            if row["archive"] is not None or row["archive_checked"] is not None:
                self.db.execute("""
                    UPDATE copies SET
                        archive=CASE
                            WHEN archive IS NULL OR ? >= COALESCE(archive_checked, 0)
                            THEN COALESCE(?, archive) ELSE archive END,
                        archive_error=CASE WHEN ? >= COALESCE(archive_checked, 0)
                            THEN ? ELSE archive_error END,
                        archive_checked=MAX(COALESCE(archive_checked, 0), ?)
                    WHERE id=?
                """, (row["archive_checked"] or 0, row["archive"],
                      row["archive_checked"] or 0, row["archive_error"],
                      row["archive_checked"] or 0, copy_id))

    def page_snapshot(self, url):
        result = self.db.execute("SELECT snapshot,fetched FROM pages WHERE url=?", (url,)).fetchone()
        return (json.loads(result["snapshot"]), result["fetched"]) if result else (None, 0)

    def page(self, url):
        snapshot, fetched = self.page_snapshot(url)
        return snapshot if fetched >= int(time.time()) - PAGE_TTL else None

    def listing(self, release_id):
        row = self.db.execute("SELECT listing FROM releases WHERE id=?", (release_id,)).fetchone()
        return json.loads(row["listing"]) if row else None

    @standalone_only
    def put_listings(self, rows, now):
        self.db.executemany("""
            INSERT INTO releases (id, listing, seen) VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET listing=excluded.listing, seen=excluded.seen
        """, [(row["id"], encode(row), now) for row in rows])

    def backfill_checkpoint(self):
        result = self.db.execute("SELECT snapshot FROM backfill_checkpoint WHERE id=1").fetchone()
        return json.loads(result["snapshot"]) if result else None

    @native_worker_only
    def put_page(self, url, page, *, checkpoint=None, seeds=()):
        now = int(time.time())
        if self.native:
            self.publisher.request(self.path, "page", url=url, page=page, checkpoint=checkpoint, now=now, seeds=seeds)
            return
        with self.write_transaction():
            self.db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?)", (url, encode(page), now))
            self.put_listings([self.merge_observation(row) for row in page["releases"]], now)
            self.db.executemany("INSERT INTO releases (id,listing,seen) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                                [(row["id"], encode(row), now) for row in seeds])
            if checkpoint is not None:
                self.db.execute("INSERT OR REPLACE INTO backfill_checkpoint VALUES (1,?)", (encode(checkpoint),))

    @standalone_only
    def merge_observation(self, row):
        stored = self.listing(row["id"]) or {}
        combined = {**stored, **{key: value for key, value in row.items() if value is not None}}
        if stored.get("tags") and not row.get("tags"):
            combined["tags"] = stored["tags"]
            combined["events"] = stored.get("events", [])
        combined["evidence"] = {
            **stored.get("evidence", {}),
            **{key: desc for key, desc in row.get("evidence", {}).items() if row.get(key) is not None},
        }
        combined["technical"] = {
            **stored.get("technical", {}),
            **{key: value for key, value in row.get("technical", {}).items() if value is not None},
        }
        forms = {
            fields.get("source", "0"): fields
            for source in (stored, row) for fields in source.get("download_forms", [])
        }
        if forms:
            combined["download_forms"] = list(forms.values())
        return combined

    @native_worker_only
    def put_observation(self, row):
        if self.native:
            self.publisher.request(self.path, "observe", row=row, now=int(time.time()))
            return
        with self.write_transaction():
            self.put_listings([self.merge_observation(row)], int(time.time()))

    @standalone_only
    def put_listing(self, listing):
        if self.native:
            self.publisher.request(self.path, "listing", row=listing, now=int(time.time()))
            return
        with self.write_transaction():
            self.put_listings([listing], int(time.time()))

    @standalone_only
    def import_listings(self, records, baseline=False):
        now = int(time.time())
        listing_ids = set()
        new = set()
        updated = set()
        linked = set()
        with self.write_transaction():
            for listing, offers, tracklist in records:
                release_id = listing["id"]
                previous = self.listing(release_id)
                if previous is None:
                    new.add(release_id)
                elif previous != listing:
                    updated.add(release_id)
                self.put_listings([listing], now)
                listing_ids.add(release_id)
                for offer, url in offers:
                    copy_id = self.attach_copy(release_id, offer, url, "export", now)
                    linked.add((release_id, copy_id))
                if tracklist is not None:
                    self.put_tracklist(release_id, *tracklist)
            if not baseline:
                self.db.executemany("INSERT INTO listing_activity VALUES (?,?)",
                                    [(release_id, now) for release_id in new])
        updated -= new
        return {"imported_listings": len(listing_ids), "new_listings": len(new), "updated_listings": len(updated),
                "unchanged_listings": len(listing_ids - new - updated), "imported_copy_links": len(linked),
                "new_listing_activity": 0 if baseline else len(new), "baseline": baseline}

    @standalone_only
    def put_tracklist(self, release_id, source_url, tracks):
        self.db.execute("""
            INSERT INTO tracklists VALUES (?,?,?)
            ON CONFLICT(release_id, source_url) DO UPDATE SET tracks=excluded.tracks
        """, (release_id, source_url, encode(tracks)))

    @standalone_only
    def song_search(self, query, limit):
        def normalize(value):
            return unicodedata.normalize("NFKC", value).casefold()

        needle = normalize(query).strip()
        if not needle:
            raise ValueError("Song search needs a title or filename fragment")
        hits = []
        tracks = self.db.execute("SELECT * FROM tracklists ORDER BY release_id DESC, source_url")
        for row in tracks:
            for track in json.loads(row["tracks"]):
                if needle in normalize(track["title"]):
                    hits.append({"release_id": row["release_id"], **track,
                                 "source": "tracklist", "source_url": row["source_url"]})
                    if len(hits) >= limit:
                        return hits
        archives = self.db.execute("SELECT id, archive FROM copies WHERE archive IS NOT NULL ORDER BY id DESC")
        for copy in archives:
            archive = json.loads(copy["archive"])
            sample = archive.get("audio_sample", {})
            release_ids = None
            for title in sample.get("tags", {}).get("TITLE", []):
                if needle in normalize(title):
                    release_ids = self.releases_for_copy(copy["id"])
                    hits.append({"release_ids": release_ids, "copy_id": copy["id"], "title": title,
                                 "path": sample["path"], "source": "embedded_tags"})
                    if len(hits) >= limit:
                        return hits
            for member in archive.get("inventory", archive.get("audio", [])):
                if member.get("kind", "audio_candidate") != "audio_candidate":
                    continue
                name = member["path"].replace("\\", "/").rsplit("/", 1)[-1]
                if needle not in normalize(name):
                    continue
                if release_ids is None:
                    release_ids = self.releases_for_copy(copy["id"])
                hits.append({"release_ids": release_ids, "copy_id": copy["id"],
                             "path": member["path"], "source": "archive_filename"})
                if len(hits) >= limit:
                    return hits
        return hits

    def releases_for_copy(self, copy_id):
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT release_id FROM release_copies WHERE copy_id=? ORDER BY release_id", (copy_id,))]

    @standalone_only
    def offer_jobs(self, limit, ids=None, refresh=False):
        now = int(time.time())
        params = []
        conditions = []
        if not refresh:
            conditions.append("""(offer_checked IS NULL
                OR (offer_error IS NOT NULL AND offer_checked < ?)
                OR (offer_error IS NULL AND offer_checked < ?))""")
            params.extend([now - RETRY_TTL, now - OFFER_TTL])
        if ids:
            conditions.append("id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        query = "SELECT * FROM releases"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY offer_checked IS NOT NULL, offer_checked, id DESC LIMIT ?"
        return list(self.db.execute(query, [*params, limit]))

    @standalone_only
    def attach_copy(self, release_id, offer, url, source, now):
        link = copy_link(offer, url, source)
        key, host = link["destination"], link["host"]
        self.db.execute("""
            INSERT INTO copies(destination, share_url, host, offer) VALUES (?,?,?,?)
            ON CONFLICT(destination) DO NOTHING
        """, (key, url, host, encode(offer)))
        previous = self.db.execute("SELECT id, offer FROM copies WHERE destination=?", (key,)).fetchone()
        copy_id = previous["id"]
        # Keep a filename and format obtained via a more descriptive alias.
        merged = {**json.loads(previous["offer"]), **{k: v for k, v in offer.items() if v is not None}}
        self.db.execute("UPDATE copies SET share_url=?, offer=? WHERE id=?", (url, encode(merged), copy_id))
        if source == "doujinstyle" or source.startswith("doujinstyle:"):
            self.db.execute("""
                UPDATE release_copies SET current=0
                WHERE release_id=? AND source=? AND copy_id<>? AND current=1
            """, (release_id, source, copy_id))
        self.db.execute("""
            INSERT INTO release_copies VALUES (?,?,?,?,?,1)
            ON CONFLICT(release_id, copy_id, source)
            DO UPDATE SET current=1, last_seen=excluded.last_seen
        """, (release_id, copy_id, source, now, now))
        return copy_id

    @standalone_only
    def put_offer(self, release_id, offer, url, error):
        results = [] if error is not None else [("doujinstyle", offer, url)]
        ids = self.put_offers(release_id, results, error)
        return ids[0] if ids else None

    @native_worker_only
    def put_offers(self, release_id, results, error, observed_sources=None, *, observation=None):
        now = int(time.time())
        if self.native:
            return self.publisher.request(
                self.path, "offers", id=release_id, links=[copy_link(offer, url, source)
                for source, offer, url in results], error=error, observation=observation, now=now,
                observed_sources=list(observed_sources) if observed_sources is not None else None)
        with self.write_transaction():
            if observation is not None:
                self.put_listings([self.merge_observation(observation)], now)
            self.db.execute("UPDATE releases SET offer_checked=?, offer_error=? WHERE id=?",
                            (now, error, release_id))
            if observed_sources is not None:
                active = self.db.execute("""
                    SELECT DISTINCT source FROM release_copies
                    WHERE release_id=? AND current=1 AND source LIKE 'doujinstyle%'
                """, (release_id,)).fetchall()
                self.db.executemany("""
                    UPDATE release_copies SET current=0 WHERE release_id=? AND source=?
                """, [(release_id, row["source"]) for row in active if row["source"] not in observed_sources])
            return [self.attach_copy(release_id, offer, url, source, now) for source, offer, url in results]

    @standalone_only
    def add_copy(self, listing, offer, url):
        now = int(time.time())
        if self.native:
            return self.publisher.request(self.path, "copy", row=listing, link=copy_link(offer, url, "manual"), now=now)
        with self.write_transaction():
            if self.listing(listing["id"]) is None:
                self.put_listings([listing], now)
            return self.attach_copy(listing["id"], offer, url, "manual", now)

    @standalone_only
    def index_jobs(self, limit, host, *, release_ids=None, require_header_probes=False):
        now = int(time.time())
        query = """
            SELECT c.*, (SELECT MIN(release_id) FROM release_copies WHERE copy_id=c.id) AS release_id
            FROM copies c
        """
        params = [now - RETRY_TTL, now - OFFER_TTL]
        overdue = "archive_checked < ?"
        if require_header_probes:
            overdue += " OR " + INCOMPLETE_ZIP_PROBES
        query += f""" WHERE (archive_checked IS NULL
            OR (archive_error IS NOT NULL AND archive_checked < ?)
            OR (archive_error IS NULL AND ({overdue})))"""
        if host:
            hosts = (host,) if isinstance(host, str) else host
            query += " AND c.host IN (" + ",".join("?" for _ in hosts) + ")"
            params.extend(map(host_family, hosts))
        if release_ids is not None:
            if not release_ids:
                return []
            placeholders = ",".join("?" for _ in release_ids)
            query += f""" AND EXISTS (SELECT 1 FROM release_copies AS rc
                WHERE rc.copy_id=c.id AND rc.current=1 AND rc.release_id IN ({placeholders}))"""
            params.extend(release_ids)
        query += """
            ORDER BY archive_checked IS NOT NULL,
                (SELECT MAX(current) FROM release_copies WHERE copy_id=c.id) DESC, c.id DESC
        """
        with closing(self.db.execute(query, params)) as rows:
            jobs = []
            for row in rows:
                if can_inspect(row["share_url"], json.loads(row["offer"])):
                    jobs.append(row)
                    if limit is not None and len(jobs) >= limit:
                        break
            return jobs

    @native_worker_only
    def put_archive(self, copy_id, archive, error):
        if self.native:
            self.publisher.request(self.path, "archive", id=copy_id, archive=archive, error=error, now=int(time.time()))
            return
        with self.write_transaction():
            if archive and archive.get("format") == "zip":
                record = self.db.execute("SELECT archive FROM copies WHERE id=?", (copy_id,)).fetchone()
                if record and record["archive"]:
                    prior = json.loads(record["archive"])
                    previous = {row["path"]: (row.get("crc32"), row.get("uncompressed_bytes"))
                                for row in prior.get("inventory", [])}
                    unchanged = {row["path"] for row in archive.get("inventory", [])
                                 if row.get("crc32") and previous.get(row["path"]) ==
                                 (row["crc32"], row.get("uncompressed_bytes"))}
                    samples = {path: values for path, values in prior.get("audio_probes", {}).items()
                               if path in unchanged}
                    for path, values in archive.get("audio_probes", {}).items():
                        samples[path] = {**samples.get(path, {}), **values}
                    legacy = prior.get("audio_sample") or {}
                    if legacy.get("path") in unchanged and "audio_sample" not in archive:
                        archive = {**archive, "audio_sample": legacy}
                    if samples:
                        archive = {**archive, "audio_probes": samples}
                    if prior.get("header_probe_pass_complete") and not archive.get("header_probe_pass_complete"):
                        candidates = {row["path"] for row in archive.get("inventory", [])
                                      if row.get("kind") == "audio_candidate" and not row.get("encrypted")
                                      and row.get("zip_method") in (0, 8)}
                        if candidates <= samples.keys():
                            archive = {**archive, "header_probe_pass_complete": True}
                    if prior.get("cached_front_cover") in unchanged and "cached_front_cover" not in archive:
                        archive = {**archive, "cached_front_cover": prior["cached_front_cover"]}
            self.db.execute("""
                UPDATE copies SET archive=COALESCE(?, archive), archive_checked=?, archive_error=?
                WHERE id=?
            """, (encode(archive) if archive is not None else None, int(time.time()), error, copy_id))

    @standalone_only
    def claim_lookup(self, provider, release_id, token, expires, now):
        if self.native:
            return self.publisher.request(self.path, "claim", provider=provider, id=release_id,
                                          token=token, expires=expires, now=now)
        with self.write_transaction():
            return self.db.execute("""
                INSERT INTO catalog_lookup_claims VALUES (?,?,?,?)
                ON CONFLICT(provider,release_id) DO UPDATE SET token=excluded.token,expires=excluded.expires
                WHERE catalog_lookup_claims.expires<=?
            """, (provider, release_id, token, expires, now)).rowcount == 1

    @standalone_only
    def release_lookup(self, provider, release_id, token):
        if self.native:
            self.publisher.request(self.path, "release", provider=provider, id=release_id, token=token)
            return
        with self.write_transaction():
            self.db.execute("DELETE FROM catalog_lookup_claims WHERE provider=? AND release_id=? AND token=?",
                            (provider, release_id, token))

    def copies_for(self, release_id):
        links = self.db.execute("""
            SELECT c.id, c.host, c.offer, c.archive, c.archive_checked, c.archive_error,
                   rc.source, rc.first_seen, rc.last_seen, rc.current
            FROM release_copies rc JOIN copies c ON c.id=rc.copy_id
            WHERE release_id=?
            ORDER BY (source='doujinstyle' AND current=1) DESC, current DESC, last_seen DESC, c.id
        """, (release_id,))
        found = {}
        for item in links:
            copy_id = item["id"]
            if copy_id not in found:
                found[copy_id] = {"copy_id": copy_id, "host": item["host"], "offer": json.loads(item["offer"]),
                                  "archive": json.loads(item["archive"]) if item["archive"] else None,
                                  "archive_checked": item["archive_checked"], "archive_error": item["archive_error"],
                                  "sources": []}
            found[copy_id]["sources"].append({"source": item["source"], "current": bool(item["current"]),
                                              "first_seen": item["first_seen"], "last_seen": item["last_seen"]})
        return list(found.values())

    @standalone_only
    def local_search(self, query, limit):
        needle = unicodedata.normalize("NFKC", query).casefold()
        hits = []
        with closing(self.db.execute("SELECT * FROM releases ORDER BY id DESC")) as cursor:
            for item in cursor:
                row = json.loads(item["listing"])
                row.pop("download_form", None)
                row.pop("download_forms", None)
                copies = self.copies_for(item["id"])
                row["copies"] = copies
                primary = copies[0] if copies else {}
                row["offer"] = primary.get("offer")
                row["archive"] = primary.get("archive")
                row["cache"] = {key: item[key] for key in ("seen", "offer_checked", "offer_error")}
                row["cache"].update({key: primary.get(key) for key in ("archive_checked", "archive_error")})
                terms = [str(row["id"]), row.get("album"), row.get("artist"), row.get("album_artist"),
                         row.get("publisher"), row.get("catalog_id"),
                         *row.get("tags", [])]
                for copy in copies:
                    offer = copy["offer"]
                    archive = copy["archive"] or {}
                    terms.extend([copy["host"], offer.get("filename"), offer.get("catalog_id_candidate"),
                                  archive.get("response_filename"), *archive.get("catalog_id_candidates", []),
                                  *(member["path"] for member in archive.get("inventory", []))])
                haystack = unicodedata.normalize("NFKC", " ".join(value for value in terms if value)).casefold()
                if needle in haystack:
                    hits.append(row)
                    if len(hits) >= limit:
                        break
        return hits

    @standalone_only
    def summary(self):
        from collections import Counter

        counts = self.db.execute("""
            SELECT count(*) AS listings, count(offer_checked) AS host_attempted,
                   count(offer_error) AS host_errors,
                   (SELECT count(*) FROM listing_activity) AS recorded_new_listing_activity,
                   (SELECT count(DISTINCT release_id) FROM release_copies
                    WHERE source LIKE 'doujinstyle%' AND current=1) AS host_resolved,
                   (SELECT count(*) FROM (SELECT DISTINCT release_id, copy_id FROM release_copies)) AS copy_links
            FROM releases
        """).fetchone()
        copies = self.db.execute("""
            SELECT count(*) AS copies, count(archive_checked) AS archive_attempted,
                   count(archive) AS archive_indexed, count(archive_error) AS archive_errors
            FROM copies
        """).fetchone()
        archive_formats = Counter(
            json.loads(row["archive"])["format"]
            for row in self.db.execute("SELECT archive FROM copies WHERE archive IS NOT NULL")
        )
        return {**dict(counts), **dict(copies), "archive_formats": dict(archive_formats), "hosts": {
            row["host"]: row["n"] for row in self.db.execute("""
                SELECT host, count(*) AS n FROM copies GROUP BY host ORDER BY n DESC, host
            """)
        }}

    @standalone_only
    def depth_report(self):
        from collections import Counter
        from datetime import date

        listed_dates = {}
        detail_ids = set()
        release_ids = set()
        for row in self.db.execute("SELECT id, listing FROM releases"):
            identifier = row["id"]
            release_ids.add(identifier)
            listing = json.loads(row["listing"])
            if any(key in listing for key in ("listed_at", "updated_at", "download_offer")):
                detail_ids.add(identifier)
            value = listing.get("listed_at")
            if isinstance(value, str):
                try:
                    listed_dates[identifier] = date.fromisoformat(value).isoformat()
                except ValueError:
                    pass

        def coverage(ids):
            dates = [(listed_dates[identifier], identifier) for identifier in ids if identifier in listed_dates]
            earliest, latest = (min(dates), max(dates)) if dates else (None, None)
            return {
                "releases": len(ids),
                "releases_with_site_date": len(dates),
                "earliest_site_date": {"date": earliest[0], "release_id": earliest[1]} if earliest else None,
                "latest_site_date": {"date": latest[0], "release_id": latest[1]} if latest else None,
            }

        releases_by_copy = {}
        for row in self.db.execute("SELECT copy_id, release_id FROM release_copies WHERE current=1"):
            releases_by_copy.setdefault(row["copy_id"], set()).add(row["release_id"])
        layers = {
            "download_links": "copies",
            "archive_directories": "audio_members",
            "track_headers_probed": "members_probed",
            "technical_metadata": "members_with_technical_metadata",
            "track_tags": "members_with_tags",
            "cover_candidates": "candidates",
            "front_covers_recorded": "copies",
        }
        counts = Counter()
        copies_by_layer = Counter()
        releases_by_layer = {name: set() for name in layers}
        for row in self.db.execute("SELECT id, archive FROM copies"):
            linked = releases_by_copy.get(row["id"])
            if not linked:
                continue
            archive = json.loads(row["archive"]) if row["archive"] is not None else {}
            probes = archive.get("audio_probes", {})
            legacy = archive.get("audio_sample") or {}
            if legacy.get("path"):
                probes = {**probes, legacy["path"]: {**legacy, **probes.get(legacy["path"], {})}}
            artwork_files = archive.get("artwork")
            if artwork_files is None:
                artwork_files = [member for member in archive.get("inventory", []) if member.get("kind") == "artwork"]
            quantities = {
                "download_links": 1,
                "archive_directories": archive.get("audio_members", len(archive.get("audio", []))),
                "track_headers_probed": len(probes),
                "technical_metadata": sum(bool(probe.get("metadata")) for probe in probes.values()),
                "track_tags": sum(bool(probe.get("tags")) for probe in probes.values()),
                "cover_candidates": len(artwork_files) + sum(len(probe.get("artwork", [])) for probe in probes.values()),
                "front_covers_recorded": int(bool(archive.get("cached_front_cover"))),
            }
            for name, quantity in quantities.items():
                covered = quantity > 0
                if name == "archive_directories":
                    covered = archive.get("format") == "zip" and ("members" in archive or "inventory" in archive)
                if covered:
                    counts[name] += quantity
                    copies_by_layer[name] += 1
                    releases_by_layer[name].update(linked)

        checkpoint = self.backfill_checkpoint()
        return {
            "listings": {
                **coverage(release_ids),
                "cached_pages": self.db.execute("SELECT count(*) FROM pages").fetchone()[0],
                "min_release_id": min(release_ids, default=None),
                "max_release_id": max(release_ids, default=None),
                "backfill_checkpoint_complete": checkpoint.get("complete") if checkpoint else None,
                "latest_observation_utc": self.db.execute(
                    "SELECT datetime(max(seen), 'unixepoch') FROM releases").fetchone()[0],
            },
            "release_details_with_evidence": coverage(detail_ids),
            **{name: {**coverage(releases_by_layer[name]), "copies": copies_by_layer[name],
                      **({unit: counts[name]} if unit != "copies" else {})}
               for name, unit in layers.items()},
        }

    @standalone_only
    def storage_report(self):
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        allocations = None
        if self.db.execute("SELECT sqlite_compileoption_used('ENABLE_DBSTAT_VTAB')").fetchone()[0]:
            allocations = dict(self.db.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name"))
        indexes = [dict(row) for row in self.db.execute("""
            SELECT t.name AS table_name, i.name, i."unique" AS is_unique, i.origin, i.partial
            FROM sqlite_schema AS t JOIN pragma_index_list(t.name) AS i
            WHERE t.type='table' ORDER BY t.name, i.name
        """)]
        for index in indexes:
            index["columns"] = [row[0] for row in self.db.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index["name"],))]
        payload_bytes = {}
        for table, column in (("pages", "snapshot"), ("releases", "listing"),
                              ("copies", "offer"), ("copies", "archive"), ("tracklists", "tracks")):
            payload_bytes[f"{table}.{column}"] = self.db.execute(
                f"SELECT coalesce(sum(length(CAST({column} AS BLOB))), 0) FROM {table}").fetchone()[0]
        return {
            "sqlite_version": sqlite3.sqlite_version,
            "database_page_bytes": page_size * self.db.execute("PRAGMA page_count").fetchone()[0],
            "free_page_bytes": page_size * self.db.execute("PRAGMA freelist_count").fetchone()[0],
            "payload_bytes": payload_bytes,
            "allocated_bytes_by_object": allocations,
            "indexes": indexes,
        }
