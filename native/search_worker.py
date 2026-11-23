"""Process one explicit DoujinStyle query and publish indexed tracks."""
import argparse
from collections import deque
from contextlib import closing, contextmanager, ExitStack
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from http.client import HTTPException
from itertools import chain
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import sys
import tempfile
from threading import Lock, local
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request
import zipfile

from zipimport import zipimporter as _NativeBundleImporter
if isinstance(__loader__, _NativeBundleImporter):
    class LxmlError(Exception):
        pass  # Compatibility exception name; installed worker never imports lxml.
else:
    from lxml.etree import LxmlError

from catalog_cache import CatalogCache, INCOMPLETE_ZIP_PROBES, MEDIAFIRE_SHARE_HOSTS, host_family
if isinstance(__loader__, _NativeBundleImporter):
    from native.runtime_contract import (HTML_ACCEPT, UA, Http, NavigationRedirect,
                                         catalog_hint, ds_url, navigation_referrer)
else:
    from doujin import (HTML_ACCEPT, UA, Http, NavigationRedirect,
                       catalog_hint, ds_url, navigation_referrer, iso_release_date, event)
# The production Windows idle runner is native; retain the reference fixture
# exclusively outside the installed zipapp.
if not isinstance(__loader__, _NativeBundleImporter):
    from native.idle_backfill import idle_backfill, wait_for_idle
if isinstance(__loader__, _NativeBundleImporter):
    from native.runtime_contract import ARCHIVE_PROVIDERS, can_inspect
else:
    from native.archive_providers import ARCHIVE_PROVIDERS
    can_inspect = getattr(__import__("catalog_cache"), "can_inspect")
from native.reference_publisher import NativeCatalogWorkerStartupFailed, NativeCatalogWorkerUnavailable, ReferencePublisher
from native.worker_scope import standalone_only, worker_fallback, runtime_deprecated


ARCHIVE_LIMIT = 6
INDEX_WORKERS = 3
ARCHIVE_BYTES = 2 * 1024 * 1024
RANGE_REQUESTS = 12
COVER_BYTES = 96 * 1024
COVER_TTL = 7 * 86400
GUIDES_URL = ds_url(p="guides")
CHART_TTL = {"month": 6 * 3600, "all": 24 * 3600}
DEFAULT_ICON = "https://doujinstyle.com/favicon.ico"
EXPECTED_FAILURES = (ValueError, OSError, HTTPException, zipfile.BadZipFile, LxmlError)
IDLE_BROWSE, IDLE_DETAILS, IDLE_LINKS, IDLE_ZIP, IDLE_TAGS, IDLE_COVERS = (1, 2, 4, 8, 16, 32)
ARCHIVE_HOSTS = tuple(ARCHIVE_PROVIDERS)


def size_text(size):
    value = size
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,.0f} B" if unit == "B" else f"{value:,.2f} {unit}"
        value /= 1024


@standalone_only
def check_url(url):
    target = urlsplit(url)
    host = target.hostname or ""
    allowed = host in {"doujinstyle.com", "www.doujinstyle.com", "mediafire.com"} or host.endswith(".mediafire.com")
    if not allowed or target.scheme != "https" or target.username is not None or target.port not in (None, 443):
        raise ValueError("Search indexing only opens HTTPS pages on DoujinStyle and MediaFire")


@standalone_only
class SearchRedirect(NavigationRedirect):
    pass  # The installed class is an inert type marker; HTTP lives in C++.
    @standalone_only
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SearchHttp(Http):
    pass  # The installed class is an inert type marker; HTTP lives in C++.
    @standalone_only
    def __init__(self):
        super().__init__(SearchRedirect())

    @standalone_only
    def get(self, url, **kwargs):
        check_url(url)
        return super().get(url, **kwargs)

    @standalone_only
    def open_archive(self, url, source, policy):
        check_url(url)
        headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
        if referrer := navigation_referrer(source, url, policy):
            headers["Referer"] = referrer
        request = Request(url, headers=headers)
        request.referrer_policy = policy
        return self.opener.open(request, timeout=20)


def index_http(http):
    # The detail request and its POST share cookies, scoped to this job.
    return SearchHttp() if type(http) is SearchHttp else http


def discovery_worker(http, cache):
    # The native cached-page path is portable; live HTTPS uses WinHTTP on Windows.
    return (cache.publisher if cache is not None and cache.native and type(http) is SearchHttp else None)


def release_details(http, target, release_id, publisher=None):
    if publisher is not None:
        try:
            parsed = publisher.request(None, "site_release", id=release_id, url=target)
        except OSError:
            pass
        else:
            return release_response(parsed)
    from zipimport import zipimporter
    if isinstance(__loader__, zipimporter):
        raise NativeCatalogWorkerUnavailable("Native release detail fetch unavailable")
    headers, body, url = http.get(target, headers={"Accept": HTML_ACCEPT})
    from doujin import __dict__ as legacy
    parser = legacy.get("parse_detail")
    if parser is None:
        raise OSError("Native detail parser unavailable; no Python production parser")
    return parser(body, release_id, url, headers)


def release_response(parsed):
    if "status" in parsed:
        raise ValueError(f"DoujinStyle release returned HTTP {parsed['status']}")
    return parsed["row"], parsed["forms"]


@standalone_only
def index_status(cache, emit):
    depth = cache.depth_report()
    storage = cache.storage_report()
    listings = depth["listings"]
    emit(f"Catalog listings  •  {listings['releases']:,} releases on {listings['cached_pages']:,} cached pages")
    if listings["releases"]:
        emit(f"Observed release IDs  •  {listings['min_release_id']}–{listings['max_release_id']}")
    latest = listings["latest_observation_utc"]
    checkpoint = listings["backfill_checkpoint_complete"]
    emit("Browse backfill  •  " + ("Reached newest page" if checkpoint else
                                   "In progress" if checkpoint is False else "No checkpoint"))
    if latest:
        emit(f"Last catalog observation  •  {latest} UTC")
    earliest, newest = listings["earliest_site_date"], listings["latest_site_date"]
    if earliest:
        emit(f"Date Added  •  {earliest['date']} (ID {earliest['release_id']}) to "
             f"{newest['date']} (ID {newest['release_id']})  •  {listings['releases_with_site_date']:,} dated")
    for key, label, unit, description in (
        ("release_details_with_evidence", "Release details", None, ""),
        ("download_links", "Download links", "copies", "copies"),
        ("archive_directories", "ZIP directories", "audio_members", "audio members"),
        ("track_headers_probed", "Headers probed", "members_probed", "members"),
        ("technical_metadata", "Technical metadata", "members_with_technical_metadata", "members"),
        ("track_tags", "Track tags", "members_with_tags", "members"),
        ("cover_candidates", "Cover candidates", "candidates", "candidates"),
        ("front_covers_recorded", "Front covers recorded", None, ""),
    ):
        layer = depth[key]
        extra = f"  •  {layer[unit]:,} {description}" if unit else ""
        emit(f"{label}  •  {layer['releases']:,} releases{extra}")
    emit(f"SQLite  •  {size_text(storage['database_page_bytes'])}")
    emit("Payload sizes  •  " + "  ·  ".join(
        f"{label} {size_text(storage['payload_bytes'][key])}" for key, label in (
            ("pages.snapshot", "pages"), ("releases.listing", "listings"),
            ("copies.offer", "offers"), ("copies.archive", "archives"))))
    allocation = storage["allocated_bytes_by_object"]
    if allocation is not None:
        index_bytes = sum(allocation.get(index["name"], 0) for index in storage["indexes"])
        emit(f"SQLite indexes  •  {size_text(index_bytes)} across {len(storage['indexes'])} indexes")
    emit(f"SQLite journal  •  {cache.db.execute('PRAGMA journal_mode').fetchone()[0].upper()}")


@standalone_only
def archive_suffix(first):
    return next((extension for magic, extension in (
        (b"PK\x03\x04", ".zip"), (b"PK\x05\x06", ".zip"),
        (b"Rar!\x1a\x07", ".rar"), (b"7z\xbc\xaf\x27\x1c", ".7z"), (b"\x1f\x8b", ".gz"),
    ) if first.startswith(magic)), None)


@standalone_only
def archive_filename(filename, release_id, suffix):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    if PureWindowsPath(filename).is_reserved() or not filename:
        filename = f"DoujinStyle-{release_id}"
    if not filename.lower().endswith(suffix):
        filename += suffix
    return filename[:-len(suffix)][:150] + suffix


@standalone_only
def save_archive(chunks, output, name, suffix, emit, expected=None):
    received = 0
    last_report = time.monotonic()
    part = None
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=output, prefix=".doujin-", suffix=".part", delete=False) as stream:
            part = Path(stream.name)
            for chunk in chunks:
                stream.write(chunk)
                received += len(chunk)
                if time.monotonic() - last_report >= 1:
                    emit(f"Downloading  •  {size_text(received)}")
                    last_report = time.monotonic()
        if expected is not None and received != expected:
            raise ValueError("The archive length disagrees with the advertised length")
        path = output / name
        for index in range(1, 1000):
            try:
                if os.name == "nt":
                    part.rename(path)
                else:
                    os.link(part, path)
                emit(f"Saved {path}")
                return path
            except FileExistsError:
                path = output / f"{name[:-len(suffix)]} ({index + 1}){suffix}"
        raise ValueError("Could not choose an unused download filename")
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


@runtime_deprecated('Native C++ owns offers, archive selection and streaming; Python still launches and reports downloads')
def download_archive(http, cache, release_id, output, emit):
    if not cache.native:
        # Only source fixtures retain the old Python downloading path.
        reference = globals().get("compatibility_download_archive")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Installed archive download requires the native worker")
        return reference(http, cache, release_id, output, emit)
    if cache.listing(release_id) is None:
        raise ValueError("The selected release is missing from the local catalog")
    def run():
        return cache.publisher.request(cache.path, "archive_download_job", id=release_id,
            output=str(output.resolve()), referrer=cache.listing(release_id).get("url") or "")
    result = run()
    if result["status"] == "missing_release":
        raise ValueError("The selected release is missing from the local catalog")
    if result["status"] == "no_offer":
        # Native release offer lookup does its own POST; never replay a lost POST.
        # Explicit download enumerates every offer once. Never resubmit an
        # uncertain POST if the worker dies after the request was sent.
        entry = native_offer_batch(cache, [release_id], workers=1, all_forms=True)[0]
        if not entry["had_observation"] and entry["error"]:
            raise ValueError(entry["error"])
        result = run()
    if result["status"] != "saved":
        raise ValueError("This release has no supported MediaFire or public MEGA file offer. Use Open release.")
    saved = result["archive"]
    if not saved.get("path") or not isinstance(saved.get("size"), int):
        raise OSError("Native archive download returned an incomplete result")
    if saved.get("authenticated") is True:
        if saved.get("integrity") == "transport_and_length_only":
            raise OSError("Native archive download returned contradictory integrity claims")
        emit(f"Saved {saved['path']} ({size_text(saved['size'])} authenticated with MEGA MAC)")
    elif saved.get("integrity") == "transport_and_length_only" and saved.get("authenticated") is False:
        emit(f"Saved {saved['path']} ({size_text(saved['size'])} HTTPS transfer; no whole-file MAC)")
    else:
        raise OSError("Native archive download did not report its integrity level")
    return Path(saved["path"])


@standalone_only
def compatibility_download_archive(http, cache, release_id, output, emit):
    from doujin import archive_destination
    release = cache.listing(release_id)
    if release is None:
        raise ValueError("The selected release is missing from the local catalog")
    def select_copy():
        copies = [copy for copy in cache.copies_for(release_id)
                  if any(source["current"] for source in copy["sources"])]
        for host in ARCHIVE_HOSTS:
            for copy in copies:
                if host_family(copy["host"]) != host:
                    continue
                share = cache.db.execute("SELECT share_url FROM copies WHERE id=?", (copy["copy_id"],)).fetchone()[0]
                if can_inspect(share, copy["offer"]):
                    return share, ARCHIVE_PROVIDERS[host], copy["offer"]
        return None

    choice = select_copy()
    if choice is None:
        _, observation, offers, error = fetch_offer(http, release, discovery_worker(http, cache), all_forms=True)
        if observation is None and error:
            raise ValueError(error)
        cache.put_offers(release_id, offers, error, observation=observation)
        choice = select_copy()
    if choice is None:
        raise ValueError("This release has no supported MediaFire or public MEGA file offer. Use Open release.")
    share, provider, offer = choice
    if host_family(urlsplit(share).hostname or "") == "mega.nz" and cache.native:
        # Authenticated native streaming to an exclusive temp file; the helper
        # commits only after verifying MEGA's whole-file MAC.
        result = cache.publisher.mega_download_archive(share, output, release_id)
        if result.get("authenticated") is not True or not result.get("path"):
            raise OSError("Native MEGA downloader did not confirm archive authentication")
        saved = Path(result["path"])
        emit(f"Saved {saved} ({size_text(result['size'])} verified via MEGA MAC)")
        return saved
    if provider.open_file:
        with provider.open_file(share) as (original_name, chunks):
            first = next(chunks, b"")
            if not (suffix := archive_suffix(first)):
                raise ValueError("MEGA did not return a recognized archive")
            emit("Downloading public MEGA archive over HTTPS…")
            name = archive_filename(original_name, release_id, suffix)
            return save_archive(chain((first,), chunks), output, name, suffix, emit)

    url, source, policy = archive_destination(http, share, release["url"])
    with http.open_archive(url, source, policy) as response:
        if response.status != 200:
            raise ValueError(f"Archive download returned HTTP {response.status}")
        first = response.read(8)
        if not (suffix := archive_suffix(first)):
            raise ValueError("MediaFire did not return a recognized archive")
        original_name = (response.headers.get_filename() or offer.get("filename") or
                         unquote(urlsplit(response.geturl()).path.rsplit("/", 1)[-1]))
        name = archive_filename(original_name, release_id, suffix)
        expected = response.headers.get("Content-Length")
        chunks = chain((first,), iter(lambda: response.read(1024 * 1024), b""))
        return save_archive(chunks, output, name, suffix, emit, int(expected) if expected is not None else None)


def write_reference(path, data):
    """Atomic artwork write; native `.djt` publication is handled by publish_many."""
    if path.exists() and path.read_bytes() == data:
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
        temp = Path(stream.name)
        stream.write(data)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return True


@standalone_only
def choose_release_date(release):
    """Small UI-only date projection; full descriptor precedence is native."""
    external = ((release.get("external_lookup") or {}).get("bangumi") or {}).get("fields") or {}
    for value, source in ((release.get("release_date"), "DoujinStyle release date"),
                          (external.get("release_date"), "Bangumi")):
        if date := iso_release_date(value):
            return date, source
    values = [entry["id"] for entry in (release.get("events") or []) + (external.get("events") or [])
              if entry.get("id")]
    values += [found["id"] for tag in release.get("tags") or [] if (found := event(tag))]
    identifiers = {value for value in values}
    if len(identifiers) == 1:
        observed = event(next(iter(identifiers)))
        if observed and observed["start"] and observed["start"] == observed["end"]:
            return observed["start"], f"Release event {observed['id']}"
    if date := iso_release_date(release.get("listed_at")):
        return date, "DoujinStyle date added"
    return None, None


def result_record(row, publisher=None):
    """Frame a catalog row for the native dialog without letting field text split records."""
    tags = [" ".join(tag.split())[:120] for tag in row.get("tags") or []]
    rank = f"#{row['chart_rank']} · {row['chart_downloads']:,} downloads" if row.get("chart_rank") else ""
    fields = (str(row["id"]), row["album"], row.get("artist") or "",
              row.get("technical", {}).get("advertised_format") or "", ", ".join(tags), display_date(row, publisher), rank)
    return "\x1e" + "\t".join(" ".join(field.split())[:512] for field in fields) + "\t" + "\x1f".join(tags)


def display_date(row, publisher=None):
    if "_native_ui_date" in row:
        return row["_native_ui_date"]
    if publisher is not None:
        return publisher.request(None, "ui_date", release=row)["date"]
    from zipimport import zipimporter
    if isinstance(__loader__, zipimporter):
        # Cached charts are readable even when the native helper fails to start.
        # The native helper owns date precedence; do not reintroduce a second
        # Python projector in the installed worker. Omit only the date field.
        return ""
    return globals()["choose_release_date"](row)[0] or ""


def cache_cover(art_directory, release_id, publisher=None):
    """Cache the same numeric JPEG thumbnails served to DoujinStyle's result cards."""
    target = art_directory / f"{release_id}.jpg"
    try:
        if target.is_file() and time.time() - target.stat().st_mtime < COVER_TTL:
            return True
        if publisher is not None:
            response = publisher.request(None, "site_resource", kind="art",
                                         url=f"https://doujinstyle.com/thumbs/{release_id}.jpg", cap=COVER_BYTES)
            image = bytes.fromhex(response["body"]) if response["status"] == 200 else b""
        else:
            from zipimport import zipimporter
            if isinstance(__loader__, zipimporter):
                raise NativeCatalogWorkerUnavailable("Native cover transport unavailable")
            _, image, _ = SearchHttp().get(f"https://doujinstyle.com/thumbs/{release_id}.jpg", cap=COVER_BYTES,
                                           redirects=False, timeout=4)
        if not image.startswith(b"\xff\xd8\xff"):
            return target.is_file()
        write_reference(target, image)
        return True
    except EXPECTED_FAILURES:
        return target.is_file()


def cache_brand(art_directory, url=None, publisher=None):
    target = art_directory / "site-icon.img"
    try:
        if target.is_file() and time.time() - target.stat().st_mtime < COVER_TTL:
            return True
        url = url or DEFAULT_ICON
        address = urlsplit(url)
        if (address.scheme != "https" or address.hostname not in {"doujinstyle.com", "www.doujinstyle.com"}
                or address.username is not None or address.port not in (None, 443)):
            return False
        if publisher is not None:
            response = publisher.request(None, "site_resource", kind="art", url=url, cap=COVER_BYTES)
            body = bytes.fromhex(response["body"]) if response["status"] == 200 else b""
        else:
            from zipimport import zipimporter
            if isinstance(__loader__, zipimporter):
                raise NativeCatalogWorkerUnavailable("Native site icon transport unavailable")
            _, body, _ = SearchHttp().get(url, cap=COVER_BYTES, redirects=False, timeout=4)
        if body.startswith((b"\x00\x00\x01\x00", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"\xff\xd8\xff")):
            write_reference(target, body)
            return True
    except EXPECTED_FAILURES:
        return target.is_file()
    return False


@standalone_only
def cached_listing(*args, **kwargs):
    return __import__("doujin").__dict__["cached_listing"](*args, **kwargs)


@standalone_only
def compatibility_search_page(http, cache, query, kind, page, refresh, seen):
    url = ds_url(p="search", source=1, type=kind, result=query, page=page - 1)
    batch = cached_listing(http, cache, url, page, 20, refresh)
    distinct = set(seen)
    releases = []
    for row in batch["releases"]:
        if row["id"] not in distinct:
            distinct.add(row["id"])
            releases.append(row)
    batch["releases"] = releases
    total = batch["total_results"]
    batch["more"] = bool(batch["rows_on_page"]) and (total is None or total > page * 20)
    return batch


@standalone_only
def compatibility_run_search(http, cache, query, pages, output, emit=print, *, refresh=False, emit_result=None,
                             emit_reference=None, art_directory=None, emit_icon=None):
    search_type = "blanket"
    if query[:4].lower() == "tag:":
        search_type, query = "tags", query[4:].strip()
        if not query:
            raise ValueError("Enter a DoujinStyle tag after tag:.")
    found = {}
    publisher = discovery_worker(http, cache)
    # A completely warm search can be retrieved in one native IPC roundtrip.
    # A cold search remains page-by-page, preserving prompt results and
    # commit-before-display even if a later network request stalls or fails.
    warm_steps = None
    if publisher is not None and not refresh and pages > 1 and all(
            cache.page(ds_url(p="search", source=1, type=search_type,
                              result=query, page=page - 1)) is not None
            for page in range(1, pages + 1)):
        try:
            warm_steps = publisher.request(cache.path, "site_search_job", query=query, pages=pages)["pages"]
        except OSError:
            warm_steps = None
    for page in range(1, pages + 1):
        native_page = False
        try:
            batch = None
            if warm_steps is not None:
                if page > len(warm_steps):
                    break
                batch = warm_steps[page - 1]
                native_page = True
            elif publisher is not None:
                try:
                    batch = publisher.request(cache.path, "site_search_job_page", query=query, kind=search_type,
                                              page=page, seen=list(found), refresh=refresh)
                    native_page = True
                except OSError as error:
                    emit(f"Native catalog search unavailable ({error}). Retrying with the compatibility reader.")
            if batch is None:
                compatibility = globals().get("compatibility_search_page")
                if compatibility is None:
                    raise OSError("Native search unavailable; no Python production parser")
                batch = compatibility(http, cache, query, search_type, page, refresh, found)
        except EXPECTED_FAILURES as exc:
            if page == 1:
                raise
            emit(f"Results page {page} unavailable ({exc}). Continuing with saved results.")
            break
        total = batch["total_results"]
        if page == 1 and emit_icon is not None:
            emit_icon(batch.get("site_icon"))
        result_count = f" of {total} matches" if total is not None else ""
        mode = "cached" if batch["cache_hit"] else "saved"
        if native_page:
            mode += " by native catalog"
        emit(f"Page {page}  •  {batch['rows_on_page']} results{result_count}  •  {mode}")
        for row in batch["releases"]:
            found[row["id"]] = row
            if emit_result is not None:
                emit_result(cache.listing(row["id"]) or row)
                continue
            artist = row.get("artist")
            credit = f"{artist} — " if artist else ""
            emit(f"  {row['id']}   {credit}{row['album']}")
        if not batch["more"]:
            break

    return index_results(http, cache, found, output, emit, emit_result=emit_result,
                         emit_reference=emit_reference, art_directory=art_directory)


@standalone_only
@runtime_deprecated('Installed foreground SearchDialog now calls native --foreground-search; Python reference used for contract parity only')
def run_search(http, cache, query, pages, output, emit=print, *, refresh=False, emit_result=None,
               emit_reference=None, art_directory=None, emit_icon=None):
    publisher = discovery_worker(http, cache)
    if publisher is None:
        reference = globals().get("compatibility_run_search")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Native foreground search job is required")
        return reference(http, cache, query, pages, output, emit, refresh=refresh,
                         emit_result=emit_result, emit_reference=emit_reference,
                         art_directory=art_directory, emit_icon=emit_icon)
    found, streamed = {}, []

    def committed_page(event):
        batch = event.get("batch") if isinstance(event, dict) else None
        page = len(streamed) + 1
        if (not isinstance(event, dict) or event.get("kind") != "search_page" or
                type(event.get("page")) is not int or event["page"] != page or page > pages or
                not isinstance(batch, dict) or not isinstance(batch.get("releases"), list) or
                len(batch["releases"]) > 20 or not isinstance(batch.get("cache_hit"), bool) or
                not isinstance(batch.get("more"), bool) or
                type(batch.get("rows_on_page")) is not int or not 0 <= batch["rows_on_page"] <= 20 or
                not isinstance(event.get("ui_dates"), dict)):
            raise NativeCatalogWorkerUnavailable("Invalid committed native search page event")
        ids = [row.get("id") for row in batch["releases"] if isinstance(row, dict)]
        if (len(ids) != len(batch["releases"]) or any(type(id) is not int or id <= 0 or id in found for id in ids) or
                len(ids) != len(set(ids)) or set(event["ui_dates"]) != {str(id) for id in ids} or
                any(not isinstance(value, str) or len(value) > 128 for value in event["ui_dates"].values())):
            raise NativeCatalogWorkerUnavailable("Invalid native search result identities or dates")
        streamed.append(batch)
        if page == 1 and emit_icon is not None:
            emit_icon(batch.get("site_icon"))
        count = batch.get("total_results")
        total = f" of {count} matches" if count is not None else ""
        mode = "cached" if batch["cache_hit"] else "saved"
        emit(f"Page {page}  •  {batch['rows_on_page']} results{total}  •  {mode} by native catalog")
        for row in batch["releases"]:
            id = row["id"]
            found[id] = row
            if emit_result is not None:
                # An event callback must not perform another request on its
                # own native pipe. The C++ event includes the date computed
                # from the already-committed listing, not a Python duplicate.
                emit_result({**(cache.listing(id) or row), "_native_ui_date": event["ui_dates"][str(id)]})
            else:
                artist = row.get("artist")
                emit(f"  {id}   {artist + ' — ' if artist else ''}{row['album']}")

    result = publisher.request(cache.path, "site_search_job", query=query, pages=pages,
                               refresh=refresh, on_event=committed_page)
    if (not isinstance(result, dict) or not isinstance(result.get("pages"), list) or
            not isinstance(result.get("late_error"), str) or not streamed or
            streamed != result["pages"]):
        raise NativeCatalogWorkerUnavailable("Native foreground search returned incomplete committed pages")
    if result["late_error"]:
        emit(f"Results page {len(streamed) + 1} unavailable ({result['late_error']}). Continuing with saved results.")
    return index_results(http, cache, found, output, emit, emit_result=emit_result,
                         emit_reference=emit_reference, art_directory=art_directory)


@standalone_only
def cached_tags(cache):
    return (row[0] for row in cache.db.execute("""
        SELECT tag.value FROM releases AS release
        CROSS JOIN json_each(release.listing, '$.tags') AS tag
        WHERE json_type(release.listing, '$.tags')='array'
          AND tag.type='text' AND length(tag.value) BETWEEN 1 AND 120
        GROUP BY tag.value COLLATE NOCASE
        ORDER BY count(DISTINCT release.id) DESC, tag.value COLLATE NOCASE
        LIMIT 4096
    """))


def chart_snapshot(cache):
    try:
        snapshot, fetched = cache.page_snapshot(GUIDES_URL) if cache is not None else (None, 0)
    except sqlite3.Error:
        if cache is None or not cache.read_only:
            raise
        return None, 0
    if snapshot and all(snapshot.get("charts", {}).get(period) for period in CHART_TTL):
        return snapshot, fetched
    return None, 0


@standalone_only
@contextmanager
def chart_refresh_lock(cache):
    """Serialize the shared page fetch across workers without holding a SQLite transaction."""
    if cache is None or cache.path is None or cache.read_only:
        yield True
        return
    with cache.path.with_name(cache.path.name + ".charts.lock").open("ab") as lock:
        if os.name == "nt":
            import errno
            from msvcrt import LK_NBLCK, LK_UNLCK, locking
            try:
                locking(lock.fileno(), LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                yield False
                return
            try:
                yield True
            finally:
                locking(lock.fileno(), LK_UNLCK, 1)
        else:
            from fcntl import LOCK_EX, LOCK_NB, flock
            try:
                flock(lock.fileno(), LOCK_EX | LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True


def chart_listings(snapshot, periods):
    return {row["id"]: {"url": ds_url(p="page", type=1, id=row["id"]),
                        **{key: value for key, value in row.items() if not key.startswith("chart_")}}
            for period in periods for row in snapshot["charts"][period]}


@standalone_only
def refresh_charts(http, cache, previous, *, persist):
    # An existing snapshot can be shown immediately if another worker is already refreshing it.
    deadline = time.monotonic() + (0 if previous else 10)
    while True:
        with chart_refresh_lock(cache) as acquired:
            if acquired:
                snapshot, fetched = chart_snapshot(cache)
                if fetched > previous:
                    return snapshot, None
                publisher = discovery_worker(http, cache) if persist else None
                if publisher is not None:
                    try:
                        return publisher.request(cache.path, "site_charts"), None
                    except OSError:
                        pass
                _, body, _ = http.get(GUIDES_URL, headers={"Accept": HTML_ACCEPT}, timeout=8)
                from doujin import __dict__ as legacy
                parser = legacy.get("parse_popular")
                if parser is None:
                    raise OSError("Native chart parser unavailable; no Python production parser")
                charts, icon = parser(body)
                snapshot = {"charts": charts, "site_icon": icon, "releases": []}
                if persist:
                    try:
                        cache.put_page(GUIDES_URL, snapshot, seeds=list(chart_listings(snapshot, CHART_TTL).values()))
                    except NativeCatalogWorkerUnavailable as error:
                        return snapshot, error
                return snapshot, None
        snapshot, fetched = chart_snapshot(cache)
        if fetched > previous or time.monotonic() >= deadline:
            return snapshot, None
        time.sleep(0.1)


@standalone_only
def compatibility_run_popular(http, cache, period, output, emit, *, refresh=False, emit_result=None, emit_reference=None,
                art_directory=None, emit_icon=None, read_only_reason=None, emit_chart=None):
    saved, fetched = chart_snapshot(cache)
    fresh = time.time() - fetched < CHART_TTL[period]
    rows, found = {}, {}

    def ranked(row):
        source = rows[row["id"]]
        if emit_result is not None:
            emit_result({**row, "chart_rank": source["chart_rank"], "chart_downloads": source["chart_downloads"]})

    def show(snapshot, source):
        nonlocal rows, found
        rows = {row["id"]: row for row in snapshot["charts"][period]}
        listings = chart_listings(snapshot, (period,))
        found = {}
        if emit_chart is not None:
            emit_chart(rows)
        for identifier, listing in listings.items():
            try:
                stored = cache.listing(identifier) if cache is not None else None
            except sqlite3.Error:
                if read_only_reason is None:
                    raise
                stored = None
            found[identifier] = {**listing, **(stored or {})}
            ranked(found[identifier])
        if emit_icon is not None:
            emit_icon(snapshot.get("site_icon"))
        emit(f"{'This Month' if period == 'month' else 'All Time'}  •  {len(rows)} ranked releases  •  {source}")

    if saved:
        show(saved, "cached" if fresh else "cached, refresh due")
    if refresh or not fresh or not saved:
        try:
            updated, failure = refresh_charts(http, cache, fetched, persist=read_only_reason is None and cache is not None)
            read_only_reason = failure or read_only_reason
            if updated is None and not saved:
                raise OSError("Another worker is refreshing the charts. Open the chart again shortly.")
            if updated and (not saved or updated != saved):
                show(updated, "updated")
        except (*EXPECTED_FAILURES, sqlite3.Error) as error:
            if not saved:
                raise
            emit(f"Chart refresh failed ({error}). Showing saved rankings.")
    if read_only_reason is None:
        try:
            for identifier, row in found.items():
                if cache.listing(identifier) is None:
                    cache.put_observation(row)
            return index_results(http, cache, found, output, emit, emit_result=ranked,
                                 emit_reference=emit_reference, art_directory=art_directory)
        except NativeCatalogWorkerUnavailable as error:
            read_only_reason = error
    emit(f"Chart loaded. Indexing unavailable: {read_only_reason}")
    return None


@standalone_only
def compatibility_prefetch_charts(http, cache, output, emit, *, art_directory=None, emit_result=None, emit_reference=None,
                    emit_icon=None):
    snapshot, fetched = chart_snapshot(cache)
    if snapshot is None or any(time.time() - fetched >= ttl for ttl in CHART_TTL.values()):
        try:
            updated, failure = refresh_charts(http, cache, fetched, persist=True)
            if failure is not None:
                raise failure
            snapshot = updated or snapshot
        except (*EXPECTED_FAILURES, sqlite3.Error) as error:
            if snapshot is None:
                raise
            emit(f"Chart prefetch could not refresh ({error}). Indexing saved rankings.")
    if snapshot is None:
        return None
    if emit_icon is not None:
        emit_icon(snapshot.get("site_icon"))
    found = chart_listings(snapshot, CHART_TTL)
    for identifier, row in found.items():
        stored = cache.listing(identifier)
        if stored is None:
            cache.put_observation(row)
        else:
            found[identifier] = {**row, **stored}
    if emit_result is not None:
        for row in found.values():
            emit_result(row)
    return index_results(http, cache, found, output, emit, emit_result=emit_result, emit_reference=emit_reference,
                         art_directory=art_directory, workers=1)


@standalone_only
def _consume_native_chart(cache, period, emit, emit_result, emit_chart, emit_icon, *, refresh=False):
    """Render post-commit C++ chart events. No nested requests on the worker pipe."""
    stages = []
    found = {}
    ranks = {}

    def committed(stage):
        nonlocal found, ranks
        if (not isinstance(stage, dict) or stage.get("kind") != "chart_stage" or
                stage.get("period") != period or stage.get("source") not in
                {"cached", "cached, refresh due", "updated", "prefetch"} or
                not isinstance(stage.get("ranks"), list) or
                not isinstance(stage.get("releases"), list) or
                len(stage["releases"]) > 8192 or
                any(not isinstance(row, dict) or type(row.get("id")) is not int or
                    row["id"] <= 0 or "_native_ui_date" not in row
                    for row in stage["releases"])):
            raise NativeCatalogWorkerUnavailable("Malformed committed native chart stage")
        releases = {row["id"]: row for row in stage["releases"]}
        if len(releases) != len(stage["releases"]):
            raise NativeCatalogWorkerUnavailable("Duplicate native chart identity")
        new_ranks = {row["id"]: row for row in stage["ranks"]}
        if len(new_ranks) != len(stage["ranks"]):
            raise NativeCatalogWorkerUnavailable("Duplicate native ranking identity")
        if period != "prefetch" and (not new_ranks or set(new_ranks) != set(releases)):
            raise NativeCatalogWorkerUnavailable("Incomplete native chart ranking")
        stages.append(stage)
        found, ranks = releases, new_ranks
        if period != "prefetch" and emit_chart is not None:
            emit_chart(ranks)
        if emit_result is not None:
            for row in found.values():
                emit_result(row)
        if emit_icon is not None:
            emit_icon(stage.get("site_icon"))
        if period != "prefetch":
            name = "This Month" if period == "month" else "All Time"
            emit(f"{name}  •  {len(ranks)} ranked releases  •  {stage['source']}")

    result = cache.publisher.request(cache.path, "catalog_chart_job", period=period,
                                     refresh=refresh, on_event=committed)
    if (not isinstance(result, dict) or result.get("stages") != stages or
            not isinstance(result.get("warnings"), list) or
            any(not isinstance(warning, str) for warning in result["warnings"])):
        raise NativeCatalogWorkerUnavailable("Incomplete native chart job response")
    for warning in result["warnings"]:
        emit(warning)
    return found, ranks


@standalone_only
@runtime_deprecated('Native C++ owns chart staging and rank projection; Python is reference only')
def run_popular(http, cache, period, output, emit, *, refresh=False, emit_result=None, emit_reference=None,
                art_directory=None, emit_icon=None, read_only_reason=None, emit_chart=None):
    if cache is None or not cache.native or type(http) is not SearchHttp:
        compatibility = globals().get("compatibility_run_popular")
        if compatibility is not None and (cache is None or type(http) is not SearchHttp):
            return compatibility(http, cache, period, output, emit, refresh=refresh, emit_result=emit_result,
                                 emit_reference=emit_reference, art_directory=art_directory, emit_icon=emit_icon,
                                 read_only_reason=read_only_reason, emit_chart=emit_chart)
        if read_only_reason is not None:
            # Only the installed no-helper path renders the last saved rankings.
            snapshot, _ = chart_snapshot(cache)
            if snapshot is None:
                raise NativeCatalogWorkerUnavailable("No saved chart rankings; native helper unavailable")
            for row in snapshot["charts"][period]:
                if emit_result: emit_result(row)
            if emit_chart: emit_chart({row["id"]: row for row in snapshot["charts"][period]})
            if emit_icon: emit_icon(snapshot.get("site_icon"))
            emit(f"{period} chart loaded from saved rankings; Indexing unavailable: {read_only_reason}")
            return None
        if compatibility is None:
            raise NativeCatalogWorkerUnavailable("Installed chart job requires the native worker")
        return compatibility(http, cache, period, output, emit, refresh=refresh, emit_result=emit_result,
                             emit_reference=emit_reference, art_directory=art_directory, emit_icon=emit_icon,
                             read_only_reason=read_only_reason, emit_chart=emit_chart)
    found, ranks = _consume_native_chart(cache, period, emit, emit_result, emit_chart,
                                         emit_icon, refresh=refresh)
    if not found:
        raise NativeCatalogWorkerUnavailable("Native chart job did not deliver rankings")

    def ranked(row):
        if emit_result is not None:
            stored = found.get(row["id"], row)
            rank = ranks.get(row["id"], {})
            emit_result({**row, **stored, "chart_rank": rank.get("chart_rank"),
                         "chart_downloads": rank.get("chart_downloads")})

    return index_results(http, cache, found, output, emit, emit_result=ranked if emit_result else None,
                         emit_reference=emit_reference, art_directory=art_directory)


@standalone_only
def prefetch_charts(http, cache, output, emit, *, art_directory=None, emit_result=None, emit_reference=None,
                    emit_icon=None):
    if cache is None or not cache.native or type(http) is not SearchHttp:
        compatibility = globals().get("compatibility_prefetch_charts")
        if compatibility is None:
            raise NativeCatalogWorkerUnavailable("Installed chart prefetch requires the native worker")
        return compatibility(http, cache, output, emit, art_directory=art_directory,
                             emit_result=emit_result, emit_reference=emit_reference, emit_icon=emit_icon)
    found, _ = _consume_native_chart(cache, "prefetch", emit, emit_result, None, emit_icon)
    if not found:
        raise NativeCatalogWorkerUnavailable("Native chart prefetch did not deliver rankings")
    return index_results(http, cache, found, output, emit, emit_result=emit_result,
                         emit_reference=emit_reference, art_directory=art_directory, workers=1)


def export(cache, output, release_ids, *, on_reference=None, on_updated=None, copy_id=None, structured=None):
    """Native publication batch. The source-only reference exporter remains for parity tests."""
    if not cache.native:
        # A packaged worker must never recover through a source or site-package
        # module with the same name. Source fixtures may use the reference exporter.
        from zipimport import zipimporter
        if isinstance(__loader__, zipimporter):
            raise OSError("Native batch publisher unavailable; no installed Python recovery")
        from native import export_library
        legacy = getattr(export_library, "export", None)
        if legacy is None:
            raise OSError("Native batch publisher unavailable; Python recovery removed from installed worker")
        return legacy(cache, output, release_ids, on_reference=on_reference, on_updated=on_updated,
                      copy_id=copy_id, structured=structured)
    output.mkdir(parents=True, exist_ok=True)
    if structured is None:
        structured = cache.structured_references
    root_key = os.path.normcase(str(output.resolve())) if structured else ""
    identifiers = [int(identifier) for identifier in release_ids]
    result = cache.publisher.request(cache.path, "publish_many", ids=identifiers,
                                     output=str(output.resolve()), root=root_key, copy_id=copy_id)
    if len(result["notifications"]) != result["references"]:
        raise OSError("Native publisher returned incomplete batch notifications")
    for release_id, name, changed in result["notifications"]:
        if on_reference is not None:
            on_reference(release_id, name)
        if changed and on_updated is not None:
            on_updated(release_id, name)
    return {key: result[key] for key in ("references", "written", "skipped_releases")}


def index_results(http, cache, found, output, emit=print, *, emit_result=None, emit_reference=None,
                  art_directory=None, workers=INDEX_WORKERS):
    totals = {"listings": len(found), "archives_attempted": 0, "archives_indexed": 0, "archive_body_bytes": 0,
              "references_written": 0, "releases_with_tracks": 0}
    if not found:
        return totals
    published_ids = set()
    reported_references = set()

    def publish_reference(release_id, name):
        published_ids.add(release_id)
        if emit_reference is not None and name not in reported_references:
            emit_reference(release_id, name)
            reported_references.add(name)

    def publish_update(release_id, name):
        if emit_reference is not None:
            emit(f"\x19{release_id}\t{name}")

    published = export(cache, output, found, on_reference=publish_reference, on_updated=publish_update)
    totals["references_written"] += published["written"]
    if published["references"]:
        emit(f"Already indexed: {published['references']} track references ({published['written']} written).")

    def publish_archive(event):
        if isinstance(event, dict):
            present_archive_event(event, totals, emit, art_directory)
        else:
            legacy = globals().get("save_archive_result")
            if legacy is None:
                raise NativeCatalogWorkerUnavailable("Installed worker requires native archive commits")
            legacy(cache, event, output, emit, publish_reference, art_directory, totals,
                   probe_cover=True, publish_update=publish_update)

    schedule_index(http, cache, found, emit, emit_result, art_directory is not None, publish_archive, workers)
    published = export(cache, output, found, on_reference=publish_reference, on_updated=publish_update)
    totals["references_written"] += published["written"]
    totals["releases_with_tracks"] = len(published_ids)
    return totals


def present_archive_event(event, totals, emit, artwork):
    """Render a committed native archive event. Never write the archive DB here."""
    if (not isinstance(event, dict) or type(event.get("id")) is not int or event["id"] <= 0 or
            type(event.get("ok")) is not bool or not isinstance(event.get("ids"), list) or
            not event["ids"] or any(type(id) is not int or id <= 0 for id in event["ids"]) or
            (event["ok"] and event.get("error") is not None) or
            (not event["ok"] and not isinstance(event.get("error"), str))):
        raise NativeCatalogWorkerUnavailable("Malformed committed archive event")
    payload = event.get("cover_payload")
    if payload is not None:
        if not isinstance(payload, str) or len(payload) > 256 * 1024 or len(payload) % 2:
            raise NativeCatalogWorkerUnavailable("Malformed committed archive cover")
        if artwork is not None:
            cover = bytes.fromhex(payload)
            for release_id in event["ids"]:
                write_reference(artwork / f"{release_id}-archive.img", cover)
    totals["archives_attempted"] += 1
    label = ", ".join(str(id) for id in event["ids"])
    if not event["ok"]:
        emit(f"  {label}   Archive unavailable ({event['error']})")
        return
    totals["archives_indexed"] += 1
    http = event.get("archive_http")
    if not isinstance(http, dict) or type(http.get("body_bytes")) is not int or http["body_bytes"] < 0:
        raise NativeCatalogWorkerUnavailable("Malformed committed archive HTTP statistics")
    transferred = http["body_bytes"]
    totals["archive_body_bytes"] += transferred
    fmt = event.get("format")
    if not isinstance(fmt, str):
        raise NativeCatalogWorkerUnavailable("Malformed committed archive format")
    members = event.get("audio_members")
    if fmt == "zip" and (type(members) is not int or members < 0):
        raise NativeCatalogWorkerUnavailable("Malformed committed ZIP member count")
    if fmt == "zip":
        emit(f"  {label}   native archive committed  •  {members} audio members  •  {size_text(transferred)} read")
    else:
        emit(f"  {label}   {fmt} archive  •  {size_text(transferred)} read")


def dispatch_archives(cache, jobs, ids, emit, notify, *, cover=False, workers=3, idle=False):
    """Validate streamed durable events; do not replay uncertain probes."""
    if not jobs:
        return
    selected = {job["id"] for job in jobs}
    allowed = set(ids) if ids is not None else None
    streamed = []

    def committed(event):
        if (not isinstance(event, dict) or type(event.get("id")) is not int or
                event["id"] not in selected or event["id"] in {entry["id"] for entry in streamed} or
                (allowed is not None and (not isinstance(event.get("ids"), list) or
                                           any(id not in allowed for id in event["ids"])))):
            raise NativeCatalogWorkerUnavailable("Invalid native archive completion event")
        notify(event)
        streamed.append({key: value for key, value in event.items() if key != "cover_payload"})

    completed = cache.publisher.request(cache.path, "catalog_archive_dispatch", jobs=jobs,
        ids=ids, idle=idle, cover=cover, workers=min(workers, 3),
        host_zlib=str(cache.publisher.host_zlib), on_event=committed)
    if (not isinstance(completed, dict) or completed.get("cancelled") or
            not isinstance(completed.get("events"), list) or
            completed["events"] != streamed or completed.get("attempted") != len(streamed) or
            completed.get("selected") != len(jobs) or
            (not completed.get("rate_limited") and len(streamed) != len(jobs))):
        raise NativeCatalogWorkerUnavailable("Incomplete native archive dispatch terminal response")
    if completed["rate_limited"]:
        raise NativeCatalogWorkerUnavailable("Provider HTTP 429: archive dispatch halted")


@runtime_deprecated('Native index dispatch owns offer/archive scheduling and commits; Python only projects committed UI events')
def schedule_index(http, cache, found, emit, emit_result, cover, publish_archive, workers=INDEX_WORKERS):
    if not cache.native:
        reference = globals().get("compatibility_native_plan_index")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Native indexing coordinator is required")
        return reference(http, cache, found, emit, emit_result, cover, publish_archive, workers)
    if not found:
        return
    if not 1 <= workers <= 8:
        raise ValueError("Unsupported indexing concurrency")
    publisher = cache.publisher
    if os.name != "nt":
        # WinHTTP offer POSTs are deliberately unavailable in a portable
        # installed worker; the source-only test oracle remains opt-in.
        plan = publisher.request(cache.path, "catalog_index_plan", ids=list(found))
        if plan["offers"]:
            reference = globals().get("compatibility_native_plan_index")
            if reference is None:
                raise NativeCatalogWorkerUnavailable("Native WinHTTP offer discovery is required")
            return reference(http, cache, found, emit, emit_result, cover, publish_archive, workers)
    streamed = []
    expected_ids = set(found)

    def committed_offer(entry):
        # A completion event is authoritative only after the native SQLite
        # commit. Check IDs and duplicates before notifying the UI.
        if (not isinstance(entry, dict) or type(entry.get("id")) is not int or
                entry["id"] not in expected_ids or
                entry["id"] in {event["id"] for event in streamed} or
                not isinstance(entry.get("offers"), list) or
                not isinstance(entry.get("statuses"), list) or
                len(entry["statuses"]) != len(entry["offers"]) or
                any(not isinstance(status, str) for status in entry["statuses"]) or
                type(entry.get("had_observation")) is not bool):
            raise NativeCatalogWorkerUnavailable("Invalid native committed offer event")
        streamed.append(entry)
        # Never issue a nested native IPC request from within an event handler:
        # the server processes one request at a time. Show durable progress
        # immediately; project enriched results after the terminal frame.
        emit(f"  {entry['id']}   Offer response committed")

    dispatch = publisher.request(cache.path, "catalog_index_dispatch", ids=list(found),
                                 workers=min(workers, 3), on_event=committed_offer)
    if (dispatch.get("cancelled") or not isinstance(dispatch.get("events"), list) or
            not isinstance(dispatch.get("archives"), list) or
            len(dispatch["events"]) > dispatch["offers_count"] or
            (not dispatch.get("rate_limited") and len(dispatch["events"]) != dispatch["offers_count"]) or
            streamed != dispatch["events"]):
        raise NativeCatalogWorkerUnavailable("Native index dispatch returned an incomplete result")
    if not dispatch["offers_count"] and not dispatch["archives"]:
        return
    emit(f"Indexing {dispatch['offers_count']} unresolved releases and "
         f"{dispatch['initial_archives_count']} known archives.")
    for entry in streamed:
        release_id = entry["id"]
        if entry["had_observation"] and emit_result is not None:
            emit_result(cache.listing(release_id))
        for status in entry["statuses"]:
            emit(f"  {release_id}   {status}")
        if not entry["offers"] and entry.get("error") is not None:
            emit(f"  {release_id}   Offer unavailable ({entry['error']})")
    if dispatch["rate_limited"]:
        raise NativeCatalogWorkerUnavailable("DoujinStyle HTTP 429: stopped new offer requests")
    if dispatch["archives"]:
        # One native coordinator owns fetch fanout and all SQLite commits.
        # Python handles only post-commit UI/art and reference projection.
        dispatch_archives(cache, dispatch["archives"], list(found), emit,
                          publish_archive, cover=cover, workers=workers)

@standalone_only
def compatibility_native_plan_index(http, cache, found, emit, emit_result, cover, publish_archive, workers=INDEX_WORKERS):
    if not cache.native:
        reference = globals().get("compatibility_schedule_index")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Installed indexing requires the native coordinator")
        return reference(http, cache, found, emit, emit_result, cover, publish_archive, workers)
    if not found:
        return
    if not 1 <= workers <= 8:
        raise ValueError("Unsupported indexing concurrency")
    publisher = cache.publisher
    # A committed snapshot of the pending offers and archive copies is
    # selected by the same C++ worker that handles repair/download planning.
    plan = publisher.request(cache.path, "catalog_index_plan", ids=list(found))
    offers = deque(plan["offers"])
    archives = deque(plan["archives"])
    queued = {row["id"] for row in archives}
    if not offers and not archives:
        return
    emit(f"Indexing {len(offers)} unresolved releases and {len(archives)} known archives.")
    # On Windows the native worker owns the bounded offer fetch pool and the
    # SQLite commit. Keep the cross-platform reference scheduler until live
    # host qualification; it is not a shipped Windows execution path.
    if os.name == "nt" and offers:
        native_offers = list(offers)
        offers.clear()
        for offset in range(0, len(native_offers), 6):
            group = native_offers[offset:offset + 6]
            completed = publisher.request(cache.path, "catalog_offer_batch", ids=group,
                                          workers=min(workers, 3))
            if completed.get("cancelled") or len(completed["results"]) != len(group):
                raise NativeCatalogWorkerUnavailable("Native offer batch did not finish")
            # Results have already committed before this IPC response. Emit
            # only from the coordinating thread, never from transport threads.
            for entry in completed["results"]:
                id = entry["id"]
                if entry["had_observation"] and emit_result is not None:
                    emit_result(cache.listing(id))
                for _, offer, share in entry["offers"]:
                    emit(f"  {id}   {offer_status(offer, share, cache.publisher)}")
                if not entry["offers"] and entry["error"] is not None:
                    emit(f"  {id}   Offer unavailable ({entry['error']})")
            # Replan after each committed batch: any newly found ZIPs enter
            # the same indexing pass, without relying on Python selectors.
            refreshed = publisher.request(cache.path, "catalog_index_plan", ids=group, offers=False)
            for job in refreshed["archives"]:
                if job["id"] not in queued:
                    archives.append(job)
                    queued.add(job["id"])
    transport_threads = local()
    transport_lock = Lock()
    with ExitStack() as resources, ThreadPoolExecutor(max_workers=workers) as pool:
        def peer():
            if not hasattr(transport_threads, "peer"):
                with transport_lock:
                    transport_threads.peer = resources.enter_context(closing(publisher.transport_peer()))
            return transport_threads.peer

        def inspect(job, ids):
            worker = peer()
            mediafire = worker if os.name == "nt" and (urlsplit(job["share_url"]).hostname or "") in MEDIAFIRE_SHARE_HOSTS else None
            result = probe_archive(http, job, ids, found, True, cover, zip_worker=worker, native_mediafire=mediafire)
            if isinstance(result[-1], NativeCatalogWorkerUnavailable):
                raise result[-1]  # Native-only installed worker: no Python recovery.
            return result

        def queue_archives(ids):
            updated = publisher.request(cache.path, "catalog_index_plan", ids=ids, offers=False)
            for row in updated["archives"]:
                if row["id"] not in queued:
                    archives.append(row)
                    queued.add(row["id"])

        def resolve_offer(row):
            return fetch_offer(http, row, peer() if os.name == "nt" else None)

        active = {}
        while offers or archives or active:
            while len(active) < workers and (archives or offers):
                if archives:
                    job = archives.popleft()
                    ids = [id for id in cache.releases_for_copy(job["id"]) if id in found]
                    future = pool.submit(inspect, job, ids)
                    active[future] = "archive"
                else:
                    id = offers.popleft()
                    future = pool.submit(resolve_offer, found[id])
                    active[future] = "offer"
            ready, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in ready:
                kind = active.pop(future)
                result = future.result()
                if kind == "archive":
                    publish_archive(result)
                else:
                    save_offer(cache, result, emit, emit_result)
                    queue_archives([result[0]])

@standalone_only
def compatibility_schedule_index(http, cache, found, emit, emit_result, cover, publish_archive, workers=INDEX_WORKERS):
    offers = deque(cache.offer_jobs(len(found), list(found)))
    archives = deque()
    queued_archives = set()

    def add_archives(release_ids):
        for job in cache.index_jobs(None, ARCHIVE_HOSTS, release_ids=release_ids, require_header_probes=True):
            if job["id"] not in queued_archives:
                archives.append(job)
                queued_archives.add(job["id"])

    add_archives(list(found))
    if not offers and not archives:
        return
    emit(f"Indexing {len(offers)} unresolved releases and {len(archives)} known archives.")
    transport_threads = local()
    transport_lock = Lock()
    publisher = cache.publisher if cache.native and type(http) is SearchHttp else None

    def peer_for_job():
        if publisher is None:
            return None
        if not hasattr(transport_threads, "peer"):
            with transport_lock:
                transport_threads.peer = resources.enter_context(closing(publisher.transport_peer()))
        return transport_threads.peer

    def inspect(job, ids):
        peer = peer_for_job()
        if peer is None:
            return probe_archive(http, job, ids, found, True, cover)
        mediafire = peer if os.name == "nt" and (urlsplit(job["share_url"]).hostname or "") in MEDIAFIRE_SHARE_HOSTS else None
        result = probe_archive(http, job, ids, found, True, cover, zip_worker=peer, native_mediafire=mediafire)
        if isinstance(result[-1], NativeCatalogWorkerUnavailable):
            # The independent catalog writer is still available.
            transport_threads.peer.close()
            return probe_archive(http, job, ids, found, True, cover)
        return result

    def native_offer(row):
        return fetch_offer(http, row, peer_for_job())

    with ExitStack() as resources, ThreadPoolExecutor(max_workers=workers) as pool:
        def resolve_offer(row):
            return fetch_offer(http, row, peer() if os.name == "nt" else None)

        active = {}
        while offers or archives or active:
            while len(active) < workers and (archives or offers):
                if archives:
                    job = archives.popleft()
                    ids = [rid for rid in cache.releases_for_copy(job["id"]) if rid in found]
                    future = pool.submit(inspect, job, ids)
                    active[future] = "archive"
                else:
                    job = offers.popleft()
                    listing = found[job["id"]]
                    if publisher is not None and sys.platform == "win32":
                        future = pool.submit(native_offer, listing)
                    else:
                        future = pool.submit(fetch_offer, http, listing)
                    active[future] = "offer"
            ready, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in ready:
                result = future.result()
                if active.pop(future) == "archive":
                    publish_archive(result)
                else:
                    save_offer(cache, result, emit, emit_result)
                    add_archives([result[0]])


@standalone_only
def fetch_offer(http, row, publisher=None, *, all_forms=False):
    release_id = row["id"]
    if publisher is not None:
        try:
            result = publisher.request(None, "site_release_offers", id=release_id, url=row["url"], all_forms=all_forms)
        except NativeCatalogWorkerStartupFailed:
            pass
        except OSError as failure:
            # The worker may have posted before its reply was lost.
            return release_id, None, [], str(failure)
        else:
            if "status" in result:
                return release_id, None, [], f"DoujinStyle release returned HTTP {result['status']}"
            if not result.get("retry_with_python"):
                for _, offer, _ in result["offers"]:
                    if offer["filename"] and (hint := catalog_hint(offer["filename"])):
                        offer["catalog_id_candidate"] = hint
                return release_id, result["observation"], result["offers"], result["error"]
    recovery = globals().get("fetch_python_offers")
    if recovery is None:
        return release_id, None, [], "Native release discovery unavailable; no Python production fallback"
    return recovery(index_http(http), row, all_forms)


@standalone_only
def fetch_python_offers(http, row, all_forms):
    release_id = row["id"]
    observation = None
    try:
        detailed, forms = release_details(http, row["url"], release_id)
        observation = {**detailed, "download_forms": forms}
        resolve = __import__("doujin").__dict__["resolve_offers"] if all_forms else resolve_archive_offers
        offers, errors = resolve(http, detailed, forms)
        return release_id, observation, offers, next(iter(errors.values()), None)
    except EXPECTED_FAILURES as exc:
        return release_id, observation, [], str(exc)


@standalone_only
def save_offer(cache, result, emit, emit_result):
    release_id, observation, offers, error = result
    cache.put_offers(release_id, offers, error, observation=observation)
    if observation is not None and emit_result is not None:
        emit_result(cache.listing(release_id))
    for _, offer, share in offers:
        emit(f"  {release_id}   {offer_status(offer, share)}")
    if not offers and error is not None:
        emit(f"  {release_id}   Offer unavailable ({error})")


def discover_offers(http, cache, jobs, found, emit, emit_result):
    if not cache.native or os.name != "nt":
        fallback = globals().get("compatibility_discover_offers")
        if fallback is None:
            raise NativeCatalogWorkerUnavailable("Native offer discovery is required")
        return fallback(http, cache, jobs, found, emit, emit_result)
    ids = [job["id"] for job in jobs]
    for offset in range(0, len(ids), 6):
        group = ids[offset:offset + 6]
        native_offer_batch(cache, group, workers=3, emit=emit, emit_result=emit_result)

@standalone_only
def compatibility_discover_offers(http, cache, jobs, found, emit, emit_result):
    publisher = discovery_worker(http, cache)
    for job in jobs:
        save_offer(cache, fetch_offer(http, found[job["id"]], publisher), emit, emit_result)


@runtime_deprecated('Native C++ executes and commits offer tasks; Python only adapts completed events for foobar2000')
def native_offer_batch(cache, ids, *, workers=3, all_forms=False, emit=None, emit_result=None):
    """The native helper commits every offer before returning any UI notification."""
    if not ids or len(ids) > 6 or len(set(ids)) != len(ids):
        raise ValueError("Invalid native offer batch selection")
    reply = cache.publisher.request(cache.path, "catalog_offer_batch", ids=ids,
                                   workers=workers, all_forms=all_forms)
    events = reply.get("results", ())
    if reply.get("cancelled") or not isinstance(events, list) or \
            len(events) > len(ids) or {item["id"] for item in events} - set(ids) or \
            len({item["id"] for item in events}) != len(events):
        raise NativeCatalogWorkerUnavailable("Native offer batch did not finish")
    if not reply.get("rate_limited") and len(events) != len(ids):
        raise NativeCatalogWorkerUnavailable("Native offer batch returned an incomplete result")
    if emit is not None:
        for entry in events:
            id = entry["id"]
            if entry["had_observation"] and emit_result is not None:
                emit_result(cache.listing(id))
            if len(entry["statuses"]) != len(entry["offers"]):
                raise NativeCatalogWorkerUnavailable("Malformed native offer statuses")
            for status in entry["statuses"]:
                emit(f"  {id}   {status}")
            if not entry["offers"] and entry["error"] is not None:
                emit(f"  {id}   Offer unavailable ({entry['error']})")
    if reply.get("rate_limited"):
        raise NativeCatalogWorkerUnavailable("DoujinStyle HTTP 429: stopped new offer requests")
    return events


@standalone_only
def offer_status(offer, url, publisher=None):
    host = offer["host"]
    family = host_family(host)
    if publisher is not None:
        readable = publisher.request(None, "provider_capability", url=url, host=family, offer=offer)["supported"]
    else:
        from zipimport import zipimporter
        if isinstance(__loader__, zipimporter):
            raise NativeCatalogWorkerUnavailable("Native provider capability required")
        readable = family in ARCHIVE_PROVIDERS and can_inspect(url, offer)
    if family == "mega.nz":
        target = urlsplit(url)
        if target.path.startswith("/folder/") or target.fragment.startswith("F!"):
            return "Found MEGA folder link (use Open release)"
        if readable:
            return "Found MEGA public file (eligible for archive indexing)"
        return "Found MEGA link without a supported public file URL and key (use Open release)"
    return f"Found {host} offer" + ("" if readable else " (use Open release)")


@standalone_only
def resolve_archive_offers(http, listing, forms):
    offers, errors = __import__("doujin").__dict__["resolve_offers"](http, listing, forms[:1])
    if len(forms) > 1 and not any(can_inspect(url, offer) for _, offer, url in offers):
        alternatives, extra_errors = __import__("doujin").__dict__["resolve_offers"](http, listing, forms[1:])
        offers.extend(alternatives)
        errors.update(extra_errors)
    return offers, errors


@standalone_only
def inspect_zip(*args, **kwargs):
    """Source-only Python archive oracle; installed worker never imports it."""
    from doujin import inspect_zip as source_reference
    return source_reference(*args, **kwargs)


@standalone_only
def probe_archive(http, job, ids, found, probe_headers, cover, *, zip_worker=None, native_mediafire=None):
    covers = []
    try:
        if (os.name == "nt" and zip_worker is not None and
                type(http) is SearchHttp):
            archive = zip_worker.request(None, "archive_inspect_job", url=job["share_url"],
                                         budget=ARCHIVE_BYTES, requests=RANGE_REQUESTS,
                                         headers=probe_headers, cover=cover,
                                         suffix=65557, host_zlib=str(zip_worker.host_zlib),
                                         referrer=found[ids[0]]["url"] if ids else "")
            if archive.get("cover_payload"):
                covers.append(bytes.fromhex(archive.pop("cover_payload")))
        else:
            from zipimport import zipimporter
            reference = globals().get("inspect_zip")
            if isinstance(__loader__, zipimporter) or reference is None:
                raise NativeCatalogWorkerUnavailable("Native archive inspector required; no Python ZIP fallback")
            archive = reference(index_http(http), job["share_url"], ARCHIVE_BYTES, directory_only=True,
                                header_probes=probe_headers, max_requests=RANGE_REQUESTS,
                                cover_sink=covers.append if cover else None,
                                referrer=found[ids[0]]["url"] if ids else None, zip_worker=zip_worker,
                                native_mediafire=native_mediafire)
        return job["id"], ids, archive, covers, None
    except EXPECTED_FAILURES as exc:
        return job["id"], ids, None, [], exc


@standalone_only
def save_archive_result(cache, result, output, emit, publish_reference, art_directory, totals, *,
                        probe_cover, repair=False, publish_update=None):
    copy_id, ids, archive, covers, error = result
    ids_label = ", ".join(str(release_id) for release_id in ids)
    totals["archives_attempted"] += 1
    try:
        if error is not None:
            raise error
        for cover in covers:
            for release_id in ids:
                write_reference(art_directory / f"{release_id}-archive.img", cover)
        if probe_cover and art_directory is not None and archive["format"] == "zip":
            archive["cover_probe_attempted"] = True
        cache.put_archive(copy_id, archive, None)
    except EXPECTED_FAILURES as exc:
        if (repair and isinstance(exc, HTTPError) and exc.code == 403
                and urlsplit(exc.url).hostname in MEDIAFIRE_SHARE_HOSTS):
            raise
        if not repair:
            cache.put_archive(copy_id, None, str(exc))
        emit(f"  {ids_label}   Archive unavailable ({exc})")
        return
    totals["archives_indexed"] += 1
    transferred = archive["archive_http"]["body_bytes"]
    totals["archive_body_bytes"] += transferred
    format_name = archive["format"]
    if format_name != "zip":
        emit(f"  {ids_label}   {format_name} archive  •  {size_text(transferred)} read")
        return
    probes = archive.get("audio_probes", {}).values()
    headers = sum(bool(item.get("metadata")) for item in probes)
    published = export(cache, output, ids, on_reference=publish_reference, on_updated=publish_update,
                       copy_id=copy_id if repair else None)
    totals["references_written"] += published["written"]
    embedded = sum(len(item.get("artwork", [])) for item in probes)
    backend = "native ZIP directory  •  " if archive["archive_http"].get("directory_reader") == "native" else ""
    if archive["archive_http"].get("transport") == "winhttp":
        backend += "native MediaFire HTTP  •  "
    if archive["archive_http"].get("member_reader") == "native-batch":
        backend += "native ZIP probes  •  "
    inferred = sum(item.get("zip_name", {}).get("encoding") == "cp932-inferred"
                   for item in archive.get("inventory", []))
    warnings = sum(bool(item.get("zip_name", {}).get("warning")) for item in archive.get("inventory", []))
    if inferred:
        backend += f"{inferred} CP932 names inferred  •  "
    if warnings:
        backend += f"{warnings} ZIP name encoding warnings  •  "
    emit(f"  {ids_label}   {backend}{archive['audio_members']} audio members  •  {headers} headers  •  "
         f"{len(archive.get('artwork', []))} artwork files, {embedded} embedded  •  {size_text(transferred)} read  •  "
         f"{published['written']} references written")


@standalone_only
def inspect_archives(http, cache, jobs, found, output, emit, publish_reference, art_directory, totals, *,
                     probe_headers=True, probe_cover=True, repair=False, publish_update=None):
    for job in jobs:
        ids = [rid for rid in cache.releases_for_copy(job["id"]) if rid in found]
        zip_worker = cache.publisher if cache.native else None
        transport = cache.publisher if cache.native and os.name == "nt" and type(http) is SearchHttp else None
        result = probe_archive(http, job, ids, found, probe_headers, probe_cover and art_directory is not None,
                               zip_worker=zip_worker, native_mediafire=transport)
        save_archive_result(cache, result, output, emit, publish_reference, art_directory, totals,
                            probe_cover=probe_cover, repair=repair, publish_update=publish_update)


@runtime_deprecated('Native repair selection and archive commits; Python still adapts playlist update notifications')
def repair_release(http, cache, release_id, output, emit):
    if not cache.native:
        reference = globals().get("compatibility_repair_release")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Native metadata repair is required")
        return reference(http, cache, release_id, output, emit)
    if cache.listing(release_id) is None:
        raise ValueError("Release is missing from this catalog. Search for it first.")

    def find(excluded=""):
        plan = cache.publisher.request(cache.path, "archive_download_plan", id=release_id, exclude=excluded)
        if plan["status"] != "ready":
            return None
        copy = plan["copy"]
        return {"id": copy["id"], "share_url": copy["url"]}

    def refresh():
        # Never replay a possibly successful POST in Python. The ledger in
        # the native offer executor refuses uncertain cross-session replay.
        native_offer_batch(cache, [release_id], workers=1)

    job = find()
    if job is None:
        refresh()
        job = find()
    if job is None:
        raise ValueError("No supported current archive offer for remote metadata repair.")

    totals = dict.fromkeys(("archives_attempted", "archives_indexed", "archive_body_bytes", "references_written"), 0)
    def probe(job):
        observed = []
        def committed(event):
            present_archive_event(event, totals, emit, None)
            observed.append(event)
        dispatch_archives(cache, [job], [release_id], emit, committed, cover=False, workers=1)
        if len(observed) != 1:
            raise NativeCatalogWorkerUnavailable("Repair archive commit notification missing")
        return observed[0]

    event = probe(job)
    if (not event["ok"] and "HTTP 403" in event["error"] and
            (urlsplit(job["share_url"]).hostname or "") in MEDIAFIRE_SHARE_HOSTS):
        emit(f"  {release_id}   MediaFire share page returned HTTP 403. Checking the current DoujinStyle offer.")
        refresh()
        replacement = find(job["share_url"])
        if replacement is None:
            raise ValueError("MediaFire share page returned HTTP 403 and no replacement offer was found.")
        job = replacement
        event = probe(job)
    if not event["ok"]:
        raise ValueError(f"Archive could not be repaired ({event['error']})")

    names = []
    published = export(cache, output, [release_id], copy_id=job["id"],
                       on_reference=lambda _, name: names.append(name))
    if not names:
        raise ValueError("No track references were repaired.")
    for name in names:
        emit(f"\x1c{release_id}\t{name}")
    emit(f"Repair complete  •  {published['written']} references updated, {len(names)} queued for reload")


@standalone_only
def compatibility_repair_release(http, cache, release_id, output, emit):
    listing = cache.listing(release_id)
    if listing is None:
        raise ValueError("Release is missing from this catalog. Search for it first.")
    found = {release_id: listing}

    def find_archive(exclude=None):
        if cache.native:
            plan = cache.publisher.request(cache.path, "archive_download_plan", id=release_id, exclude=exclude or "")
            if plan["status"] == "ready":
                copy = plan["copy"]
                return {"id": copy["id"], "share_url": copy["url"]}
            return None
        for copy in cache.copies_for(release_id):
            if copy["host"] in ARCHIVE_PROVIDERS and any(source["current"] for source in copy["sources"]):
                job = cache.db.execute("SELECT id, share_url FROM copies WHERE id=?", (copy["copy_id"],)).fetchone()
                if job["share_url"] != exclude and can_inspect(job["share_url"], copy["offer"]):
                    return job
        return None

    job = find_archive()
    if job is None:
        discover_offers(http, cache, [{"id": release_id}], found, emit, None)
        job = find_archive()
    if job is None:
        raise ValueError("No supported current archive offer for remote metadata repair.")
    totals = dict.fromkeys(("archives_attempted", "archives_indexed", "archive_body_bytes",
                            "references_written"), 0)
    names = []
    def inspect(job):
        inspect_archives(http, cache, [job], found, output, emit, lambda _, name: names.append(name),
                         None, totals, probe_cover=False, repair=True)

    try:
        inspect(job)
    except HTTPError as exc:
        emit(f"  {release_id}   MediaFire share page returned HTTP 403. Checking the current DoujinStyle offer.")
        discover_offers(http, cache, [{"id": release_id}], found, emit, None)
        refreshed = find_archive(exclude=job["share_url"])
        if refreshed is None:
            raise ValueError("MediaFire share page returned HTTP 403 and no replacement offer was found.") from exc
        inspect(refreshed)
    if not names:
        raise ValueError("No track references were repaired.")
    for name in names:
        emit(f"\x1c{release_id}\t{name}")
    emit(f"Repair complete  •  {totals['references_written']} references updated, {len(names)} queued for reload")


@standalone_only
def run_idle_layers(http, cache, layers, output, artwork, emit, slot=wait_for_idle, *,
                    catalog_signals=False):
    if cache is None or not cache.native:
        reference = globals().get("compatibility_run_idle_layers")
        if reference is None:
            raise NativeCatalogWorkerUnavailable("Installed idle indexing requires the native worker")
        return reference(http, cache, layers, output, artwork, emit, slot, catalog_signals=catalog_signals)
    pause = 0
    emit("\x1b")
    def candidates(mask):
        return cache.publisher.request(cache.path, "catalog_idle_plan", layers=mask)

    if layers & IDLE_DETAILS:
        committed = []

        def detail_event(event):
            if (not isinstance(event, dict) or event.get("kind") != "idle_detail" or
                    type(event.get("id")) is not int or event["id"] <= 0 or
                    event["id"] in committed or len(committed) >= 6):
                raise NativeCatalogWorkerUnavailable("Malformed committed idle detail event")
            committed.append(event["id"])
            emit(f"  {event['id']}   Release details saved")

        result = cache.publisher.request(cache.path, "catalog_idle_detail_job", on_event=detail_event)
        if (not isinstance(result, dict) or not isinstance(result.get("events"), list) or
                result["events"] != [{"kind": "idle_detail", "id": id} for id in committed]):
            raise NativeCatalogWorkerUnavailable("Incomplete native idle detail job response")
    link_jobs = candidates(IDLE_LINKS)["links"] if layers & IDLE_LINKS else ()
    if os.name == "nt" and link_jobs:
        for offset in range(0, len(link_jobs), 6):
            pause = slot(pause)
            ids = [item["id"] for item in link_jobs[offset:offset + 6]]
            for entry in native_offer_batch(cache, ids, workers=3):
                emit(f"  {entry['id']}   " + ("Download link saved" if entry["offers"] else
                      "Download link unavailable"))
    else:
        for job in link_jobs:
            release_id = job["id"]
            pause = slot(pause)
            fallback = globals().get("compatibility_idle_offer")
            if fallback is None:
                raise NativeCatalogWorkerUnavailable("Native idle offers are required")
            fallback(http, cache, job, emit)
    if layers & (IDLE_ZIP | IDLE_TAGS | IDLE_COVERS):
        jobs = candidates(layers & (IDLE_ZIP | IDLE_TAGS | IDLE_COVERS))["archives"]
        totals = dict.fromkeys(("archives_attempted", "archives_indexed", "archive_body_bytes", "references_written"), 0)
        committed_ids = set()

        def idle_archive_event(event):
            present_archive_event(event, totals, emit, artwork)
            if event["ok"]:
                committed_ids.update(event["ids"])

        # Native idle gate now paces EACH archive fetch; no Python job loop.
        dispatch_archives(cache, jobs, None, emit, idle_archive_event,
                          cover=artwork is not None, workers=1, idle=True)
        if committed_ids:
            export(cache, output, sorted(committed_ids),
                   on_updated=lambda release_id, name: emit(f"\x19{release_id}\t{name}"))
    if catalog_signals and layers & IDLE_TAGS:
        for release_id in cache.publisher.request(cache.path, "bangumi_idle"):
            emit(f"\x1a{release_id}")
    emit("\x1b")
    if layers & IDLE_BROWSE:
        pages_saved = 0

        def browse_progress(line):
            nonlocal pages_saved
            emit(line)
            if line.startswith("Idle catalog page "):
                pages_saved += 1
                if pages_saved % 10 == 0:
                    emit("\x1b")
        idle_backfill(http, cache, browse_progress, slot=slot)
        emit("\x1b")
    emit("Selected idle indexing pass finished.")


@standalone_only
def compatibility_idle_offer(http, cache, job, emit):
    release_id = job["id"]
    _, observation, offers, error = fetch_offer(http, job["listing"], discovery_worker(http, cache))
    cache.put_offers(release_id, offers, error, observation=observation)
    emit(f"  {release_id}   " + ("Download link saved" if offers else "Download link unavailable"))

@standalone_only
def compatibility_run_idle_layers(http, cache, layers, output, artwork, emit, slot=wait_for_idle, *,
                    catalog_signals=False):
    pause = 0
    emit("\x1b")
    if layers & IDLE_DETAILS:
        rows = cache.db.execute("""
            SELECT id, listing FROM releases
            WHERE json_type(listing, '$.download_offer') IS NULL
              AND json_type(listing, '$.listed_at') IS NULL
            ORDER BY id LIMIT ?
        """, (ARCHIVE_LIMIT,)).fetchall()
        for row in rows:
            pause = slot(pause)
            url = ds_url(p="page", type=1, id=row["id"])
            details, forms = release_details(http, url, row["id"], discovery_worker(http, cache))
            cache.put_observation({**details, "download_forms": forms})
            emit(f"  {row['id']}   Release details saved")
    if layers & IDLE_LINKS:
        ready = cache.db.execute("""
            SELECT id, listing FROM releases WHERE offer_checked IS NULL
              AND json_extract(listing, '$.download_offer')=1
              AND json_array_length(listing, '$.download_forms')>0 ORDER BY id LIMIT ?
        """, (ARCHIVE_LIMIT,)).fetchall()
        for job in ready:
            release_id, row = job["id"], json.loads(job["listing"])
            pause = slot(pause)
            _, observation, offers, error = fetch_offer(http, row, discovery_worker(http, cache))
            cache.put_offers(release_id, offers, error, observation=observation)
            emit(f"  {release_id}   " + ("Download link saved" if offers else "Download link unavailable"))
    if layers & (IDLE_ZIP | IDLE_TAGS | IDLE_COVERS):
        hosts = ",".join("?" for _ in ARCHIVE_HOSTS)
        jobs = cache.db.execute(f"""
            SELECT c.*, {INCOMPLETE_ZIP_PROBES} AS needs_headers FROM copies c
            WHERE c.host IN ({hosts})
              AND (c.archive_checked IS NULL OR c.archive_error IS NULL)
              AND EXISTS (SELECT 1 FROM release_copies r WHERE r.copy_id=c.id AND r.current=1)
            ORDER BY c.id
        """, ARCHIVE_HOSTS).fetchall()
        count = 0
        for job in jobs:
            if not can_inspect(job["share_url"], json.loads(job["offer"])):
                continue
            archive = json.loads(job["archive"]) if job["archive"] else {}
            indexed = archive.get("format") == "zip"
            new_directory = bool(layers & IDLE_ZIP) and job["archive_checked"] is None
            headers = bool(layers & IDLE_TAGS) and (new_directory or (indexed and job["needs_headers"]))
            cover = (indexed or new_directory) and bool(layers & IDLE_COVERS) and not (
                archive.get("cover_probe_attempted") or archive.get("cached_front_cover"))
            if not (new_directory or headers or cover):
                continue
            ids = [row[0] for row in cache.db.execute(
                "SELECT DISTINCT release_id FROM release_copies WHERE copy_id=? AND current=1", (job["id"],))]
            found = {identifier: cache.listing(identifier) for identifier in ids}
            pause = slot(pause)
            totals = dict.fromkeys(("archives_attempted", "archives_indexed", "archive_body_bytes", "references_written"), 0)
            inspect_archives(http, cache, [job], found, output, emit, lambda *_: None,
                             artwork, totals, probe_headers=headers, probe_cover=cover,
                             publish_update=lambda release_id, name: emit(f"\x19{release_id}\t{name}"))
            count += 1
            if count == ARCHIVE_LIMIT:
                break
    if catalog_signals and layers & IDLE_TAGS and cache.native:
        for release_id in cache.publisher.request(cache.path, "bangumi_idle"):
            emit(f"\x1a{release_id}")
    emit("\x1b")
    if layers & IDLE_BROWSE:
        pages_saved = 0

        def browse_progress(line):
            nonlocal pages_saved
            emit(line)
            if line.startswith("Idle catalog page "):
                pages_saved += 1
                if pages_saved % 10 == 0:
                    emit("\x1b")

        idle_backfill(http, cache, browse_progress, slot=slot)
        emit("\x1b")
    emit("Selected idle indexing pass finished.")


def open_worker_catalog(path, publisher, emit):
    try:
        return CatalogCache(path, structured_references=True, publisher=publisher)
    except NativeCatalogWorkerUnavailable as error:
        from zipimport import zipimporter
        if isinstance(__loader__, zipimporter):
            raise  # Native-only installed worker; no bundled SQLite writer recovery.
        try:
            cache = CatalogCache(path, structured_references=True)
        except sqlite3.OperationalError as unsafe:
            raise NativeCatalogWorkerUnavailable(f"{error}. Recovery writer unavailable: {unsafe}") from unsafe
        emit(f"Native catalog worker unavailable ({error}). Using the SQLite recovery writer.")
        return cache


def main(*, publisher_bundle=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--popular", choices=("month", "all"))
    mode.add_argument("--prefetch-charts", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--idle-backfill", action="store_true")
    mode.add_argument("--download-archive", type=int, metavar="RELEASE_ID")
    mode.add_argument("--repair-release", type=int, metavar="RELEASE_ID")
    parser.add_argument("--pages", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--idle-catalog-signals", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--result-rows", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--art-cache", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--host-zlib", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--idle-layers", type=int, default=IDLE_BROWSE, choices=range(1, 64),
                        metavar="LAYER_MASK", help=argparse.SUPPRESS)
    args = parser.parse_args()
    query = (args.query or "").strip()
    if args.query is not None:
        if not query or len(query) > 512:
            parser.error("Enter a search query of 1 to 512 characters")
        if args.output is None:
            parser.error("--output is required for a search")
    if (args.popular or args.prefetch_charts) and args.output is None:
        parser.error("--output is required to publish chart results")
    if args.download_archive is not None and args.output is None:
        parser.error("--output is required for an archive download")
    if args.repair_release is not None and args.output is None:
        parser.error("--output is required for metadata repair")
    if args.idle_backfill and args.idle_layers & (IDLE_ZIP | IDLE_TAGS | IDLE_COVERS) and args.output is None:
        parser.error("--output is required to publish indexed archives")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    output_lock = Lock()

    def emit(line):
        with output_lock:
            print(line)

    try:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        publisher = ReferencePublisher(bundle=publisher_bundle, host_zlib=args.host_zlib) if publisher_bundle is not None else None
        read_only_reason = None
        try:
            cache = open_worker_catalog(args.cache, publisher, emit)
        except NativeCatalogWorkerUnavailable as error:
            if not args.popular:
                raise
            read_only_reason = error
            try:
                cache = CatalogCache(args.cache, read_only=True)
            except sqlite3.Error:
                cache = None
        if args.download_archive is not None:
            with closing(cache):
                download_archive(SearchHttp(), cache, args.download_archive, args.output, emit)
            return 0
        if args.repair_release is not None:
            with closing(cache):
                repair_release(SearchHttp(), cache, args.repair_release, args.output, emit)
            return 0
        if args.idle_backfill:
            parser.error("Idle indexing is a native C++ executable job; Python execution retired")
        art_directory = args.art_cache
        if art_directory is not None:
            try:
                art_directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                emit(f"Artwork cache unavailable ({exc}).")
                art_directory = None
        try:
            # Each cover thread needs its OWN native worker connection. A
            # shared publisher would race the foreground streamed search and
            # could interleave JSONL requests and callbacks on one pipe.
            with ExitStack() as cover_peers, ThreadPoolExecutor(max_workers=1 if args.prefetch_charts else 3) as covers:
                queued = set()
                thread_peers, peers_lock = local(), Lock()

                def auxiliary_publisher():
                    if cache is None or not cache.native:
                        return None
                    if not hasattr(thread_peers, "publisher"):
                        with peers_lock:
                            thread_peers.publisher = cover_peers.enter_context(
                                closing(cache.publisher.transport_peer()))
                    return thread_peers.publisher

                def get_cover(release_id):
                    if cache_cover(art_directory, release_id, auxiliary_publisher()):
                        emit(f"\x1d{release_id}")

                def emit_result(row):
                    emit(result_record(row, cache.publisher if cache is not None and cache.native else None))
                    release_id = row["id"]
                    if art_directory is not None and release_id not in queued:
                        queued.add(release_id)
                        covers.submit(get_cover, release_id)

                def emit_reference(release_id, name):
                    emit(f"\x1c{release_id}\t{name}")

                def fetch_icon(url):
                    if cache_brand(art_directory, url, auxiliary_publisher()):
                        emit("\x17")

                options = dict(refresh=args.refresh, emit_result=emit_result if args.result_rows else None,
                               emit_reference=emit_reference if args.result_rows else None,
                               art_directory=art_directory,
                               emit_icon=(lambda url: covers.submit(fetch_icon, url)) if art_directory else None)
                if args.popular:
                    chart = globals().get("run_popular")
                    if chart is None: raise NativeCatalogWorkerUnavailable("Installed popular charts are native-only")
                    totals = chart(SearchHttp(), cache, args.popular, args.output, emit,
                                         read_only_reason=read_only_reason,
                                         emit_chart=(lambda rows: emit("\x15" + "\t".join(map(str, rows))))
                                         if args.result_rows else None, **options)
                elif args.prefetch_charts:
                    del options["refresh"]
                    prefetch = globals().get("prefetch_charts")
                    if prefetch is None: raise NativeCatalogWorkerUnavailable("Installed chart prefetch is native-only")
                    totals = prefetch(SearchHttp(), cache, args.output, emit, **options)
                else:
                    # Installed zipapp never handles query jobs. This reference
                    # remains only for source-side parity tests; foreground
                    # SearchDialog now launches native --foreground-search.
                    frontend = globals().get("run_search")
                    if frontend is None:
                        raise NativeCatalogWorkerUnavailable("Foreground search is native-only in installed worker")
                    totals = frontend(SearchHttp(), cache, query, args.pages, args.output, emit=emit, **options)
        finally:
            if cache is not None:
                cache.close()
    except (*EXPECTED_FAILURES, sqlite3.Error) as exc:
        message = ("Archive download stopped" if args.download_archive is not None else
                   "Metadata repair stopped" if args.repair_release is not None else "Search stopped")
        emit(f"{message}: {exc}")
        return 1
    if totals is None:
        return 0
    print(f"Done. {totals['listings']} listings, {totals['archives_indexed']}/{totals['archives_attempted']} "
          f"archives inspected, {totals['references_written']} references written.")
    print(f"Coverage: {totals['releases_with_tracks']}/{totals['listings']} releases have published tracks.")
    return 0
