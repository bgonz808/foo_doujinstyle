#!/usr/bin/env python3
"""Write foobar2000 track references for ZIP members already indexed in the catalog."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import tempfile
import unicodedata
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from catalog_cache import CatalogCache
from doujin import archive_sidecar, catalog_number_only, event, iso_release_date
from native.mega_download import is_public_file
from native.worker_scope import standalone_only, worker_fallback


GENRE_TAGS = frozenset("""
ambient|artcore|breakbeat|breakcore|chiptune|classical|dance|denpa|dnb|drum and bass|dubstep
edm|electro|electro house|electro pop|electronic|eurobeat|experimental|folk|footcore|footwork
future bass|gabber|glitch|happy hardcore|hard trance|hardcore|hardstyle|hip-hop|house|industrial
j-core|j-pop|jazz|juke|metal|nightcore|orchestral|pop|power metal|progressive house|psy-trance
rock|schranz|soundtrack|speedcore|symphonic|techno|trance|uk hardcore|world
""".strip().replace("\n", "|").split("|"))


@standalone_only
def clean(value):
    return "".join(character for character in str(value) if ord(character) >= 32)


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def unique_values(values):
    seen = {}
    for value in values:
        if value and (key := normalized(value)):
            seen.setdefault(key, value.strip())
    return tuple(seen.values())


@standalone_only
def match_track(stem, tracks):
    candidates = {normalized(stem)}
    pieces = stem.split(" - ", 2)
    if len(pieces) > 1 and pieces[0].strip().isdigit():
        candidates.add(normalized(pieces[-1]))
    matches = [track for track in tracks if normalized(track["title"]) in candidates]
    return matches[0] if len(matches) == 1 else {}


@standalone_only
def archive_tags(archive):
    probes = list((archive.get("audio_probes") or {}).values())
    if archive.get("audio_sample"):
        probes.append(archive["audio_sample"])
    return [probe.get("tags") or {} for probe in probes]


@standalone_only
def archive_album_artist(archive):
    artists = {}
    for tags in archive_tags(archive):
        for artist in tags.get("ALBUMARTIST") or tags.get("ALBUM ARTIST") or []:
            if normalized(artist):
                artists.setdefault(normalized(artist), artist)
    return next(iter(artists.values())) if len(artists) == 1 else None


@standalone_only
def archive_catalog_numbers(archive):
    candidates = {}
    for path, probe in (archive.get("audio_probes") or {}).items():
        folder = PurePosixPath(path.replace("\\", "/")).parent
        tags = probe.get("tags") or {}
        numbers = candidates.setdefault(folder, set())
        for field in ("CATALOGNUMBER", "CATALOG NUMBER", "CATALOGID"):
            numbers.update(number for value in tags.get(field) or [] if (number := catalog_number_only(value)))
    return {folder: next(iter(numbers)) for folder, numbers in candidates.items() if len(numbers) == 1}


@standalone_only
def archive_catalog_number(archive):
    numbers = {number for tags in archive_tags(archive) for field in ("CATALOGNUMBER", "CATALOG NUMBER", "CATALOGID")
               for value in tags.get(field) or [] if (number := catalog_number_only(value))}
    return next(iter(numbers)) if len(numbers) == 1 else None


def release_events(release, external):
    return unique_values([entry["id"] for entry in (release.get("events") or []) + (external.get("events") or [])
                          if entry.get("id")] +
                         [observed["id"] for tag in release.get("tags") or [] if (observed := event(tag))])


def choose_release_date(release, embedded=None, track_events=()):
    external = ((release.get("external_lookup") or {}).get("bangumi") or {}).get("fields") or {}
    for raw, source in ((embedded, "Embedded"), (release.get("release_date"), "DoujinStyle release date"),
                        (external.get("release_date"), "Bangumi")):
        if value := iso_release_date(raw):
            return value, source
    year = embedded if embedded and re.fullmatch(r"[0-9]{4}", embedded) else None
    events = track_events or release_events(release, external)
    if len(events) == 1 and (candidate := event(events[0])):
        proposed = candidate["start"]
        if proposed and proposed == candidate["end"] and (not year or proposed.startswith(year + "-")):
            return proposed, f"Release event {candidate['id']}"
    added = iso_release_date(release.get("listed_at"))
    if added and (not year or added.startswith(year + "-")):
        return added, "DoujinStyle date added"
    return None, None


@standalone_only
def legacy_zip_name(item):
    path = item["path"]
    raw = item.get("zip_name", {}).get("raw")
    if raw and bytes.fromhex(raw) != path.encode("utf-8"):
        return bytes.fromhex(raw).decode("cp437")
    return path


@standalone_only
def descriptor_filename(release_id, item, cache, output, output_key):
    previous = legacy_zip_name(item)

    def filename(path):
        key = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
        return f"{release_id}-{key}.djt"

    original = filename(previous)
    alternate = item.get("zip_name", {}).get("legacy_unicode_path")
    if previous == item["path"] and not alternate:
        return original

    # Keep either a v0.0.33 native name or a name previously written by
    # Python's compatibility reader, which can prefer the Unicode extra field.
    candidates = [previous, item["path"]]
    if alternate:
        candidates.append(alternate)
    for name in dict.fromkeys(candidates + [path.replace("\\", "/") for path in candidates]):
        candidate = filename(name)
        if (output / reference_location(cache, output, output_key, candidate, "")).is_file():
            return candidate
    return original


@standalone_only
def make_descriptor(release, item, url, archive, tracks, *, album_artist=None, album_catalog=None):
    member = item["path"]
    if archive_sidecar(member):
        return None
    name = PurePosixPath(member.replace("\\", "/")).name
    if not name or any(character in name for character in ':?#|') or not PurePosixPath(name).suffix:
        return None
    stem = PurePosixPath(name).stem
    track = match_track(stem, tracks)
    sample = archive.get("audio_sample") or {}
    existing = sample if sample.get("path") == member else {}
    observed = (archive.get("audio_probes") or {}).get(member, {})
    tags = {**existing.get("tags", {}), **observed.get("tags", {})}
    tags = {key.upper(): unique_values(values) for key, values in tags.items()}
    stream = {**(existing.get("metadata") or {}), **(observed.get("metadata") or {})}
    release_tags = unique_values(release.get("tags") or [])
    match = ((release.get("external_lookup") or {}).get("bangumi") or {})
    external = match.get("fields") or {}
    genres = tuple(tag for tag in release_tags if normalized(tag) in GENRE_TAGS)
    event_tags = (*tags.get("COMMENT", ()), *tags.get("CATALOGNUMBER", ()), *tags.get("CATALOG NUMBER", ()),
                  release.get("catalog_id"), external.get("catalog_id"))
    events = unique_values([*release_events(release, external),
                            *(record["id"] for tag in event_tags if tag and (record := event(tag)))])

    def preferred(fields, fallback=None):
        return next((tags[key][0] for key in fields if tags.get(key)), fallback)

    raw_date = preferred(("DATE", "YEAR"))
    date, date_source = choose_release_date(release, raw_date, events)

    def catalog_value(*values):
        return next((value for value in values if value and not event(value)), None)

    catalog_sources = {
        "TRACK": (catalog_value(*tags.get("CATALOGNUMBER", ()), *tags.get("CATALOG NUMBER", ())) or
                  next((code for value in tags.get("CATALOGID", ()) if (code := catalog_number_only(value))), None)),
        "RELEASE": catalog_value(release.get("catalog_id")),
        "LOOKUP": catalog_value(external.get("catalog_id")),
        "ALBUM": catalog_value(album_catalog),
    }
    year = raw_date if not date and raw_date and re.fullmatch(r"[0-9]{4}", raw_date) else None
    raw = item.get("zip_name", {}).get("raw")
    locator = None
    if (raw and bytes.fromhex(raw) != member.encode("utf-8") and item.get("zip_method") in (0, 8) and
            max(item["compressed_bytes"], item["uncompressed_bytes"]) <= 0xffffffff):
        locator = f'{raw}:{item["zip_header_offset"]}:{int(item["crc32"], 16)}'
    meta = {
        "URL": quote(url, safe=":/?&=%+#[]!$'()*+,;@~-"),
        "REFERRER": release.get("url"),
        "MEMBER": member, "MEMBER_LOCATOR": locator, "FILENAME": name, "RELEASE_ID": release["id"],
        "TITLE": preferred(("TITLE",), track.get("title") or stem),
        "ARTIST": preferred(("ARTIST",), track.get("artist")),
        "ALBUM": preferred(("ALBUM",), release["album"]),
        "ALBUM ARTIST": preferred(("ALBUMARTIST", "ALBUM ARTIST"),
                                  album_artist or release.get("album_artist") or release.get("artist")),
        "DATE": date, "DATE SOURCE": date_source, "RELEASE YEAR": year,
        "LABEL": preferred(("LABEL", "PUBLISHER"), release.get("publisher") or external.get("publisher")),
        "CATALOGNUMBER": catalog_value(*catalog_sources.values()),
        "CATALOG SOURCES": 1,
        **{f"CATALOG SOURCE {source}": value for source, value in catalog_sources.items()},
        "BANGUMI URL": match.get("url"),
        "TRACKNUMBER": preferred(("TRACKNUMBER",), track.get("number")),
        "DISCNUMBER": preferred(("DISCNUMBER",)),
        "TOTALTRACKS": preferred(("TOTALTRACKS", "TRACKTOTAL")),
        "TOTALDISCS": preferred(("TOTALDISCS", "DISCTOTAL")),
        "DOUJINSTYLE DATE ADDED": release.get("listed_at"),
        "SAMPLERATE": stream.get("sample_rate_hz"), "CHANNELS": stream.get("channels"),
        "BITSPERSAMPLE": stream.get("bits_per_sample"), "CODEC": stream.get("codec"),
        "ENCODING": stream.get("encoding"), "SOURCE_BYTES": item.get("uncompressed_bytes"),
        "ZIP_MEMBER_COMPRESSED_BYTES": item.get("compressed_bytes"),
    }
    if stream.get("duration_seconds"):
        meta["DURATION_MS"] = round(stream["duration_seconds"] * 1000)
        average = stream.get("file_average_bitrate_bps")
        if average is None and item.get("uncompressed_bytes"):
            average = item["uncompressed_bytes"] * 8 / stream["duration_seconds"]
        if average:
            meta["BITRATE_KBPS"] = round(average / 1000)
    if clean(member) != member:
        return None
    fields = [(key, value) for key, value in meta.items() if value is not None]
    for key, values in (("GENRE", tags.get("GENRE") or genres), ("RELEASE GENRE", genres),
                        ("RELEASE EVENT", events), ("DOUJINSTYLE TAG", release_tags), ("COMMENT", tags.get("COMMENT") or ())):
        fields.extend((key, value) for value in values)
    return "DOUJIN-TRACK 1\n" + "".join(f"{key}={clean(value)}\n" for key, value in fields)


def write_reference(path, data):
    if path.exists() and path.read_bytes() == data:
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


@standalone_only
def reference_location(cache, output, output_key, filename, text):
    flat = output / filename
    if flat.is_file():
        return filename
    recorded = cache.db.execute("SELECT path FROM published_references WHERE root=? AND filename=?",
                                (output_key, filename)).fetchone()
    saved = None
    if recorded:
        relative = recorded["path"]
        if relative in (filename, f"undated/{filename}") or re.fullmatch(
                r"[0-9]{4}/(?:0[1-9]|1[012])/" + re.escape(filename), relative):
            if (output / relative).is_file():
                return relative
            saved = relative
    # A restored catalog can predate the registry. Preserve an existing shard
    # whenever its descriptor identity is already present under this root.
    undated = f"undated/{filename}"
    if (output / undated).is_file():
        return undated
    for path in sorted(output.glob(f"[0-9][0-9][0-9][0-9]/[0-1][0-9]/{filename}")):
        if path.parent.name in {f"{month:02}" for month in range(1, 13)} and path.is_file():
            return path.relative_to(output).as_posix()
    if saved is not None:
        return saved
    date = next((line.removeprefix("DATE=") for line in text.splitlines() if line.startswith("DATE=")), None)
    iso = iso_release_date(date)
    return f"{iso[:4]}/{iso[5:7]}/{filename}" if iso else undated


@standalone_only
def release_snapshot(cache, release_id, copy_id):
    with cache.db:
        cache.db.execute("BEGIN")
        release = cache.listing(release_id)
        if release is None:
            return None
        copies = cache.copies_for(release_id)
        selected = next((copy for copy in copies if (copy_id is None or copy["copy_id"] == copy_id) and
                         not copy["archive_error"] and (copy["archive"] or {}).get("format") == "zip"), None)
        if selected is None:
            return None
        url = cache.db.execute("SELECT share_url FROM copies WHERE id=?", (selected["copy_id"],)).fetchone()[0]
        if not url.startswith(("http://", "https://")) or ("#" in url and not is_public_file(url)):
            return None
        tracklists = cache.db.execute(
            "SELECT tracks FROM tracklists WHERE release_id=? ORDER BY source_url", (release_id,)).fetchall()
    tracks = [track for source in tracklists for track in json.loads(source["tracks"])]
    tracks = [dict(values) for values in dict.fromkeys(tuple(sorted(track.items())) for track in tracks)]
    return release, url, selected["archive"], tracks


@standalone_only
def publish_python_references(cache, output, root_key, references):
    notifications, placements = [], []
    for filename, text in references:
        relative = reference_location(cache, output, root_key, filename, text) if root_key else filename
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = write_reference(path, text.encode("utf-8"))
        if root_key:
            placements.append((root_key, filename, relative))
        notifications.append((relative, changed))
    if placements:
        with cache.write_transaction():
            cache.db.executemany("""
                INSERT INTO published_references VALUES (?,?,?)
                ON CONFLICT(root, filename) DO UPDATE SET path=excluded.path
                WHERE published_references.path<>excluded.path
            """, placements)
    return notifications


@standalone_only
def publish_legacy_release(cache, output, output_key, release_id, copy_id):
    snapshot = release_snapshot(cache, release_id, copy_id)
    if snapshot is None:
        return None
    references = []
    release, url, archive, tracks = snapshot
    album_artist = archive_album_artist(archive)
    album_catalogs = archive_catalog_numbers(archive)
    members = archive.get("inventory", archive.get("audio", []))
    for item in members:
        if item.get("kind", "audio_candidate") != "audio_candidate" or item.get("encrypted"):
            continue
        member = item["path"]
        folder = PurePosixPath(member.replace("\\", "/")).parent
        text = make_descriptor(release, item, url, archive, tracks, album_artist=album_artist,
                               album_catalog=album_catalogs.get(folder))
        if text is None:
            continue
        filename = descriptor_filename(release_id, item, cache, output, output_key)
        references.append((filename, text))
    return publish_python_references(cache, output, output_key, references)


@standalone_only
def export(cache, output, release_ids, *, on_reference=None, on_updated=None, copy_id=None, structured=None):
    output.mkdir(parents=True, exist_ok=True)
    if structured is None:
        structured = cache.structured_references
    output_key = os.path.normcase(str(output.resolve())) if structured else None
    total = updated = skipped = 0
    for release_id in release_ids:
        with cache.publication_lock():
            if cache.native:
                notifications = cache.publisher.publish_release(cache.path, output, output_key, release_id, copy_id)
            else:
                # Source fixtures still exercise the legacy implementation.
                # The installed package has no Python descriptor projector.
                legacy = globals().get("publish_legacy_release")
                if legacy is None:
                    raise OSError("Native descriptor publisher unavailable; no Python production fallback")
                notifications = legacy(cache, output, output_key, release_id, copy_id)
        if notifications is None:
            skipped += 1
            continue
        total += len(notifications)
        for name, changed in notifications:
            updated += changed
            if on_reference is not None:
                on_reference(release_id, name)
            if changed and on_updated is not None:
                on_updated(release_id, name)
    return {"references": total, "written": updated, "skipped_releases": skipped}


@standalone_only
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache", type=Path, default=Path("doujin.sqlite"))
    parser.add_argument("--ids", type=int, nargs="+", help="limit export to these listing IDs")
    args = parser.parse_args()
    if not args.cache.is_file():
        parser.error(f"catalog does not exist: {args.cache}")
    cache = CatalogCache(args.cache)
    try:
        ids = args.ids or [row[0] for row in cache.db.execute("""
            SELECT DISTINCT rc.release_id FROM release_copies AS rc
            JOIN copies AS c ON c.id=rc.copy_id WHERE c.archive IS NOT NULL ORDER BY rc.release_id
        """)]
        print(json.dumps(export(cache, args.output, ids), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    finally:
        cache.close()


if __name__ == "__main__":
    main()
